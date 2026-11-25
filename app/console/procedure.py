"""One run of the test procedure (proposal 3.2.4):

  checks -> start the stack -> restore the dump -> verify the dataset -> k6
  (warm-up, ramp-up, sustain) -> resources and validity -> stop the stack.

Every step is logged to the events table and to <results>/<label>.log, next to the
k6 summary <label>.json. The commands are the ones in the README and in DEPLOY.md
of academico, so a run can be repeated by hand.
"""

import os
import shlex
import sqlite3
import subprocess
import time

import httpx

from . import checks, config, db, metrics, plan, stats
from . import summary as k6summary
from .config import IMPLEMENTATIONS


class RunFailed(Exception):
    pass


class RunCancelled(Exception):
    pass


class Context:
    def __init__(self, conn: sqlite3.Connection, run: sqlite3.Row):
        self.conn = conn
        self.run = run
        self.impl = IMPLEMENTATIONS[run['impl']]
        self.label = run['label']
        self.log_path = os.path.join(config.RESULTS_DIR, f'{self.label}.log')
        self.summary_path = os.path.join(config.RESULTS_DIR, f'{self.label}.json')
        self.k6_container = f'research-k6-{run["id"]}'

    def log(self, message: str, level: str = 'info') -> None:
        db.log(self.conn, self.run['id'], message, level)
        with open(self.log_path, 'a', encoding='utf-8') as file:
            file.write(f'{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())} [{level}] {message}\n')

    def update(self, **fields) -> None:
        db.update_run(self.conn, self.run['id'], **fields)

    def check_cancel(self) -> None:
        row = self.conn.execute('SELECT cancel_requested FROM runs WHERE id = ?', (self.run['id'],)).fetchone()
        if row['cancel_requested']:
            raise RunCancelled()

    def sh(self, cmd: list[str] | str, timeout: int = 300, check: bool = True) -> subprocess.CompletedProcess:
        shown = cmd if isinstance(cmd, str) else shlex.join(cmd)
        self.log(f'$ {shown}')
        if isinstance(cmd, str):
            cmd = ['bash', '-o', 'pipefail', '-c', cmd]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        output = (result.stdout + result.stderr).strip()
        if output:
            with open(self.log_path, 'a', encoding='utf-8') as file:
                file.write(output + '\n')
        if check and result.returncode != 0:
            raise RunFailed(f'{shown} keluar dengan kode {result.returncode}: {output[-300:]}')
        return result


def k6_command(ctx: Context) -> list[str]:
    run = ctx.run
    k6_env = {
        'BASE_URL': ctx.impl.base_url,
        'RUN_ID': ctx.label,
        'SUMMARY': f'/results/{ctx.label}.json',
        'THINK': config.THINK_S,
    }
    if run['kind'] != 'single':
        k6_env.update(VUS=str(run['vus']), WARMUP=f'{config.WARMUP_S}s', RAMP=f'{config.RAMP_S}s',
                      SUSTAIN=f'{config.SUSTAIN_S}s')
    command = [
        'docker', 'run', '--rm', '--name', ctx.k6_container, '--network', 'host', '--cpuset-cpus', '0',
        '--user', config.K6_USER,
        '-v', f'{config.K6_DIR}:/scripts:ro', '-v', f'{config.DATA_DIR}/k6:/data:ro', '-v', f'{config.RESULTS_DIR}:/results',
        '-e', 'K6_INFLUXDB_TAGS_AS_FIELDS=vu,iter,url',
        config.K6_IMAGE, 'run', '--quiet', '--out', f'influxdb={config.INFLUX_URL}/{config.INFLUX_DB}',
        '--tag', f'run_id={ctx.label}',
    ]
    for name, value in k6_env.items():
        command += ['-e', f'{name}={value}']
    return command + [f'/scripts/{run["script"]}']


def run_k6(ctx: Context) -> int:
    command = k6_command(ctx)
    ctx.log(f'$ {shlex.join(command)}')
    deadline = time.time() + config.K6_TIMEOUT_S
    with open(ctx.log_path, 'a', encoding='utf-8') as output:
        process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT)
        while process.poll() is None:
            try:
                ctx.check_cancel()
            except RunCancelled:
                subprocess.run(['docker', 'stop', '-t', '5', ctx.k6_container], capture_output=True, timeout=60)
                process.wait(timeout=60)
                raise
            if time.time() > deadline:
                subprocess.run(['docker', 'stop', '-t', '5', ctx.k6_container], capture_output=True, timeout=60)
                process.wait(timeout=60)
                raise RunFailed(f'k6 melewati batas {config.K6_TIMEOUT_S} detik')
            time.sleep(2)
    return process.returncode


def wait_http(url: str, timeout: float = 90) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=10)
            if response.status_code == 200:
                return
            last = f'status {response.status_code}'
        except httpx.HTTPError as error:
            last = str(error)
        time.sleep(2)
    raise RunFailed(f'{url} belum membalas 200 setelah {timeout:.0f} detik ({last})')


def stack_containers(ctx: Context) -> dict[str, str]:
    """Service name -> container name, as Prometheus (cAdvisor) labels it"""
    listed = ctx.sh(ctx.impl.compose('ps', '--format', '{{.Service}} {{.Name}}'), timeout=60)
    return dict(line.split(maxsplit=1) for line in listed.stdout.strip().splitlines() if ' ' in line)


def estimated_window(ctx: Context, k6_start: float, k6_end: float) -> tuple[float, float]:
    if ctx.run['kind'] == 'single':
        return k6_start, k6_end
    start = k6_start + config.WARMUP_S + config.RAMP_S
    return start, min(start + config.SUSTAIN_S, k6_end)


def execute(conn: sqlite3.Connection, run: sqlite3.Row) -> str:
    """Runs one queued run to its end and returns its final status"""
    if not os.path.isdir(config.RESULTS_DIR):
        # k6 writes its summary there as K6_USER, not as root
        os.makedirs(config.RESULTS_DIR)
        uid, gid = (int(part) for part in config.K6_USER.split(':'))
        os.chown(config.RESULTS_DIR, uid, gid)
    ctx = Context(conn, run)
    impl = ctx.impl
    ctx.update(status='running', started_at=time.time(), log_path=ctx.log_path, tag=impl.tag)
    ctx.log(f'mulai {ctx.label}: {impl.name}, {run["kind"]} {run["scenario"]}'
            + (f', {run["vus"]} VU' if run['vus'] else '') + f', percobaan {run["attempt"]}')

    sampler = None
    stack_up = False
    status = 'failed'
    try:
        results = checks.preflight([impl])
        for result in results:
            ctx.log(f'cek {result["name"]}: {"OK" if result["ok"] else "GAGAL"} - {result["detail"]}',
                    'info' if result['ok'] else 'error')
        failed = [result['name'] for result in results if not result['ok']]
        if failed:
            raise RunFailed('pemeriksaan awal gagal: ' + ', '.join(failed))
        ctx.update(git_commit=checks.commit(impl))
        ctx.check_cancel()

        stack_up = True
        # No --wait: the FrankenPHP base image has a healthcheck on the Caddy admin API
        # (localhost:2019/metrics), which the research Caddyfile turns off (admin off),
        # so the app container always reports unhealthy. depends_on still starts the app
        # only once MariaDB is healthy, and the login page below shows the app is up.
        ctx.sh(impl.compose('up', '-d', '--no-build'), timeout=300)
        containers = stack_containers(ctx)
        wait_http(f'{impl.base_url}/research/login', timeout=180)
        ctx.sh(f'gunzip -c {shlex.quote(config.DUMP)} | '
               + shlex.join(impl.compose('exec', '-T', 'mariadb', 'mariadb', '-uacademico', '-psecret',
                                         'academico_filament')), timeout=300)
        ctx.sh(impl.compose('exec', '-T', 'app', 'php', 'artisan', 'research:verify-dataset'), timeout=300)
        ctx.check_cancel()
        time.sleep(config.SETTLE_S)

        sampler = metrics.ProcStatSampler()
        sampler.start()
        k6_start = time.time()
        ctx.update(k6_started_at=k6_start, summary_path=ctx.summary_path)
        code = run_k6(ctx)
        k6_end = time.time()
        sampler.stop()
        ctx.update(k6_ended_at=k6_end)
        if code != 0:
            raise RunFailed(f'k6 keluar dengan kode {code}')
        ctx.log('k6 selesai')

        rows = k6summary.rows(k6summary.load(ctx.summary_path))

        window = None
        try:
            window = metrics.sustain_window(ctx.label)
        except httpx.HTTPError as error:
            ctx.log(f'InfluxDB: {error}', 'warning')
        estimated = window is None
        if estimated:
            window = estimated_window(ctx, k6_start, k6_end)
            ctx.log('jendela sustain diperkirakan dari jam mulai k6', 'warning')
        start, end = window

        # Let Prometheus scrape the last seconds of the window (scrape interval 5 s)
        time.sleep(max(0.0, end + 10 - time.time()))
        resources = {}
        for service, container in [*containers.items(), ('k6', ctx.k6_container)]:
            try:
                usage = metrics.container_usage(container, start, end)
            except httpx.HTTPError as error:
                ctx.log(f'Prometheus ({container}): {error}', 'warning')
                usage = None
            if usage:
                resources[service] = (container, usage)

        k6_usage = resources.get('k6', (None, {}))[1]
        steal_all = metrics.steal_fraction(sampler.samples, start, end, 'cpu')
        steal_cpu1 = metrics.steal_fraction(sampler.samples, start, end, 'cpu1')
        valid, reasons = stats.validity(k6_usage.get('cpu_max'), steal_all, steal_cpu1)

        with db.transaction(conn):
            conn.execute('DELETE FROM run_endpoints WHERE run_id = ?', (run['id'],))
            conn.executemany(
                'INSERT INTO run_endpoints (run_id, endpoint, requests, throughput, error_rate, avg_ms, med_ms, '
                'p95_ms, max_ms, peak_memory_avg, peak_memory_max) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                [(run['id'], r['endpoint'], *[r[f] for f in k6summary.FIELDS]) for r in rows])
            conn.execute('DELETE FROM run_resources WHERE run_id = ?', (run['id'],))
            conn.executemany(
                'INSERT INTO run_resources (run_id, service, container, cpu_avg, cpu_max, mem_avg, mem_max, samples) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                [(run['id'], service, container, u['cpu_avg'], u['cpu_max'], u['mem_avg'], u['mem_max'], u['samples'])
                 for service, (container, u) in resources.items()])
            status = 'done' if valid else 'invalid'
            ctx.update(status=status, valid=int(valid), invalid_reason='; '.join(reasons) or None,
                       sustain_start=start, sustain_end=end, sustain_estimated=int(estimated),
                       k6_cpu_avg=k6_usage.get('cpu_avg'), k6_cpu_max=k6_usage.get('cpu_max'),
                       steal_all=steal_all, steal_cpu1=steal_cpu1)
            if not valid and run['attempt'] < config.MAX_ATTEMPTS:
                plan.requeue(conn, run)

        overall = next((r for r in rows if r['endpoint'] == 'all'), {})
        ctx.log(f'hasil: {overall.get("requests")} request, p95 {overall.get("p95_ms") or 0:.1f} ms, '
                f'error {(overall.get("error_rate") or 0):.2%}; '
                + ('valid' if valid else 'TIDAK VALID: ' + '; '.join(reasons)
                   + (', diulang' if run['attempt'] < config.MAX_ATTEMPTS else ', batas percobaan tercapai')),
                'info' if valid else 'warning')
    except RunCancelled:
        status = 'cancelled'
        ctx.update(status=status)
        ctx.log('dibatalkan', 'warning')
    except Exception as error:
        status = 'failed'
        ctx.update(status=status, error=str(error)[:2000])
        ctx.log(f'gagal: {error}', 'error')
        # A failure needs a look (missing image, wrong checkout, ...): stop the queue
        db.set_control(conn, 'paused', '1')
        ctx.log('antrean dijeda karena run gagal', 'warning')
    finally:
        # Each cleanup step on its own, so a failing one never leaves the stack running
        if sampler is not None and sampler.is_alive():
            try:
                sampler.stop()
            except Exception as error:
                ctx.log(f'gagal menghentikan pencatat /proc/stat: {error}', 'error')
        try:
            subprocess.run(['docker', 'rm', '-f', ctx.k6_container], capture_output=True, timeout=60)
        except Exception as error:
            ctx.log(f'gagal menghapus kontainer k6: {error}', 'error')
        if stack_up:
            try:
                ctx.sh(impl.compose('down'), timeout=180, check=False)
            except Exception as error:
                ctx.log(f'gagal menghentikan stack: {error}', 'error')
        ctx.update(ended_at=time.time())
    return status
