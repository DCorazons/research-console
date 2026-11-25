"""Web interface of the research console.

    uvicorn console.web:app --host 127.0.0.1 --port 9000

Two roles, from two passwords in .env: 'view' reads every page and export, 'control'
also creates plans and starts, pauses, cancels, and repeats runs. Control actions are
POST forms with a CSRF token. This container has no Docker socket: it only writes
plans and flags to SQLite, and the runner acts on them.
"""

import csv
import hmac
import io
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import charts, config, db, plan, stats
from .config import IMPLEMENTATIONS

HERE = Path(__file__).parent
STATUSES = ('queued', 'running', 'done', 'invalid', 'failed', 'cancelled')

@asynccontextmanager
async def lifespan(_app):
    conn = db.connect()
    db.init(conn)
    conn.close()
    yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY or secrets.token_urlsafe(32),
                   session_cookie='research_console', same_site='strict', max_age=12 * 3600)
app.mount('/static', StaticFiles(directory=HERE / 'static'), name='static')
templates = Jinja2Templates(directory=HERE / 'templates')

TZ = timezone(timedelta(hours=config.TZ_OFFSET_HOURS))


def fmt_time(value) -> str:
    if value is None:
        return '-'
    return datetime.fromtimestamp(float(value), TZ).strftime('%Y-%m-%d %H:%M:%S')


def fmt_num(value, digits: int = 1) -> str:
    return '-' if value is None else f'{value:,.{digits}f}'


def fmt_pct(value, digits: int = 1) -> str:
    return '-' if value is None else f'{value * 100:.{digits}f} %'


def fmt_mib(value) -> str:
    return '-' if value is None else f'{value / 1048576:,.2f}'


def fmt_duration(seconds) -> str:
    if seconds is None:
        return '-'
    seconds = int(seconds)
    return f'{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}'


templates.env.filters.update(time=fmt_time, num=fmt_num, pct=fmt_pct, mib=fmt_mib, duration=fmt_duration)
templates.env.globals.update(IMPLEMENTATIONS=IMPLEMENTATIONS, NAMES=charts.NAMES)


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


class LoginRequired(Exception):
    pass


@app.exception_handler(LoginRequired)
def login_required(request: Request, exc: LoginRequired):
    return RedirectResponse('/login', status_code=303)


def viewer(request: Request) -> str:
    role = request.session.get('role')
    if role not in ('view', 'control'):
        raise LoginRequired()
    return role


def controller(request: Request, role: str = Depends(viewer)) -> str:
    if role != 'control':
        raise HTTPException(403, 'Butuh hak kontrol')
    return role


def check_csrf(request: Request, token: str) -> None:
    if not token or not hmac.compare_digest(token, request.session.get('csrf', '')):
        raise HTTPException(400, 'Token CSRF tidak cocok; muat ulang halaman')


def render(request: Request, name: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {
        'role': request.session.get('role'), 'csrf': request.session.get('csrf', ''), **context})


def back(request: Request, default: str = '/') -> RedirectResponse:
    referer = request.headers.get('referer', '')
    host = request.headers.get('host', '')
    target = default
    if host and f'//{host}/' in referer:
        target = referer.split(f'//{host}', 1)[1] or default
    return RedirectResponse(target, status_code=303)


# --- login -------------------------------------------------------------------------

@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request):
    return render(request, 'login.html', error=None,
                  configured=bool(config.VIEW_PASSWORD or config.CONTROL_PASSWORD))


@app.post('/login')
def login(request: Request, password: str = Form('')):
    role = None
    if config.CONTROL_PASSWORD and hmac.compare_digest(password, config.CONTROL_PASSWORD):
        role = 'control'
    elif config.VIEW_PASSWORD and hmac.compare_digest(password, config.VIEW_PASSWORD):
        role = 'view'
    if role is None:
        time.sleep(1)
        return render(request, 'login.html', error='Password salah',
                      configured=bool(config.VIEW_PASSWORD or config.CONTROL_PASSWORD))
    request.session.clear()
    request.session.update(role=role, csrf=secrets.token_urlsafe(32))
    return RedirectResponse('/', status_code=303)


@app.post('/logout')
def logout(request: Request):
    request.session.clear()
    return RedirectResponse('/login', status_code=303)


# --- home and status -------------------------------------------------------------------

def phase_of(run, now: float) -> str:
    if run['k6_started_at'] is None:
        return 'persiapan'
    if run['k6_ended_at'] is not None:
        return 'mengolah hasil'
    if run['kind'] == 'single':
        return 'single request'
    elapsed = now - run['k6_started_at']
    for name, until in (('pemanasan', config.WARMUP_S), ('ramp-up', config.WARMUP_S + config.RAMP_S),
                        ('sustain', config.WARMUP_S + config.RAMP_S + config.SUSTAIN_S)):
        if elapsed < until:
            return name
    return 'selesai, menunggu k6'


def status_context(conn) -> dict:
    now = time.time()
    current = conn.execute("SELECT * FROM runs WHERE status = 'running' ORDER BY id LIMIT 1").fetchone()
    heartbeat = db.get_control(conn, 'runner_heartbeat')
    preflight = db.get_control(conn, 'preflight')
    counts = {row['status']: row['n'] for row in conn.execute(
        'SELECT status, COUNT(*) AS n FROM runs GROUP BY status')}
    remaining = counts.get('queued', 0)
    return {
        'now': now,
        'paused': db.is_paused(conn),
        'runner_age': None if heartbeat is None else now - float(heartbeat),
        'preflight': json.loads(preflight) if preflight else None,
        'current': current,
        'phase': phase_of(current, now) if current else None,
        'elapsed': now - current['started_at'] if current and current['started_at'] else None,
        'events': conn.execute('SELECT * FROM events WHERE run_id = ? ORDER BY id DESC LIMIT 8',
                               (current['id'],)).fetchall() if current else [],
        'counts': counts,
        'statuses': STATUSES,
        'upcoming': conn.execute("SELECT * FROM runs WHERE status = 'queued' "
                                 'ORDER BY plan_id, seq, attempt, id LIMIT 6').fetchall(),
        'recent': conn.execute("SELECT * FROM runs WHERE status NOT IN ('queued', 'running') AND ended_at IS NOT NULL "
                               'ORDER BY ended_at DESC LIMIT 6').fetchall(),
        'remaining_s': remaining * 210,
    }


@app.get('/', response_class=HTMLResponse)
def home(request: Request, role: str = Depends(viewer), conn=Depends(get_conn)):
    return render(request, 'home.html', **status_context(conn))


@app.get('/partials/status', response_class=HTMLResponse)
def status_partial(request: Request, role: str = Depends(viewer), conn=Depends(get_conn)):
    return render(request, '_status.html', **status_context(conn))


@app.post('/control/{action}')
def control(request: Request, action: str, csrf: str = Form(''), role: str = Depends(controller),
            conn=Depends(get_conn)):
    check_csrf(request, csrf)
    if action == 'start':
        db.set_control(conn, 'paused', '0')
        db.log(conn, None, 'antrean dijalankan dari konsol')
    elif action == 'pause':
        db.set_control(conn, 'paused', '1')
        db.log(conn, None, 'antrean dijeda dari konsol (run yang berjalan diselesaikan)')
    elif action == 'check':
        db.set_control(conn, 'check_now', '1')
    else:
        raise HTTPException(404)
    return back(request)


# --- plans -----------------------------------------------------------------------------

def plan_rows(conn):
    return conn.execute("""
        SELECT p.*, COUNT(r.id) AS runs,
               SUM(r.status = 'queued') AS queued, SUM(r.status = 'done') AS done,
               SUM(r.status = 'invalid') AS invalid, SUM(r.status = 'failed') AS failed,
               SUM(r.status = 'cancelled') AS cancelled, SUM(r.status = 'running') AS running
        FROM plans p LEFT JOIN runs r ON r.plan_id = p.id
        GROUP BY p.id ORDER BY p.id DESC
    """).fetchall()


@app.get('/plans', response_class=HTMLResponse)
def plans_page(request: Request, blocks: int = 1, first_block: str = '', suite: str = 'web',
               stress: int = 1, role: str = Depends(viewer), conn=Depends(get_conn)):
    first_block = optional_int(first_block)
    if first_block is None:
        last = conn.execute('SELECT MAX(block) FROM runs WHERE suite = ?', (suite,)).fetchone()[0]
        first_block = (last or 0) + 1
    try:
        preview = plan.build(max(1, min(blocks, 20)), max(1, first_block), suite, bool(stress))
        error = None
    except ValueError as exc:
        preview, error = [], str(exc)
    return render(request, 'plans.html', plans=plan_rows(conn), preview=preview, error=error,
                  form={'blocks': blocks, 'first_block': first_block, 'suite': suite, 'stress': stress},
                  per_run_s=210, load_vus=config.LOAD_VUS, stress_vus=config.STRESS_VUS)


@app.post('/plans')
def create_plan(request: Request, blocks: int = Form(...), first_block: int = Form(...), suite: str = Form('web'),
                stress: int = Form(0), note: str = Form(''), csrf: str = Form(''), role: str = Depends(controller),
                conn=Depends(get_conn)):
    check_csrf(request, csrf)
    if not 1 <= blocks <= 20 or first_block < 1 or suite not in plan.SCRIPTS:
        raise HTTPException(422, 'Rencana tidak valid')
    plan_id = plan.create(conn, blocks, first_block, suite, bool(stress), note.strip() or None)
    db.log(conn, None, f'rencana {plan_id} dibuat: {suite}, blok {first_block}-{first_block + blocks - 1}')
    return RedirectResponse(f'/runs?plan={plan_id}', status_code=303)


@app.post('/plans/{plan_id}/cancel')
def cancel_plan(request: Request, plan_id: int, csrf: str = Form(''), role: str = Depends(controller),
                conn=Depends(get_conn)):
    check_csrf(request, csrf)
    conn.execute("UPDATE runs SET status = 'cancelled' WHERE plan_id = ? AND status = 'queued'", (plan_id,))
    db.log(conn, None, f'sisa antrean rencana {plan_id} dibatalkan')
    return back(request, '/plans')


# --- runs ------------------------------------------------------------------------------

def optional_int(value: str) -> int | None:
    """A filter field sent empty ("Semua") or not sent at all means no filter"""
    try:
        return int(value)
    except ValueError:
        return None


@app.get('/runs', response_class=HTMLResponse)
def runs_page(request: Request, plan: str = '', block: str = '', impl: str = '', kind: str = '',
              scenario: str = '', status: str = '', role: str = Depends(viewer), conn=Depends(get_conn)):
    plan, block = optional_int(plan), optional_int(block)
    where, params = [], []
    for column, value in (('plan_id', plan), ('block', block), ('impl', impl), ('kind', kind),
                          ('scenario', scenario), ('status', status)):
        if value not in (None, ''):
            where.append(f'r.{column} = ?')
            params.append(value)
    sql = ('SELECT r.*, e.requests, e.p95_ms, e.throughput, e.error_rate FROM runs r '
           "LEFT JOIN run_endpoints e ON e.run_id = r.id AND e.endpoint = 'all'")
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    rows = conn.execute(sql + ' ORDER BY r.plan_id, r.seq, r.attempt LIMIT 1000', params).fetchall()
    plans = conn.execute('SELECT id, suite, first_block, blocks FROM plans ORDER BY id DESC').fetchall()
    return render(request, 'runs.html', runs=rows, plans=plans, statuses=STATUSES,
                  filters={'plan': plan, 'block': block, 'impl': impl, 'kind': kind, 'scenario': scenario,
                           'status': status})


def get_run(conn, run_id: int):
    run = conn.execute('SELECT * FROM runs WHERE id = ?', (run_id,)).fetchone()
    if run is None:
        raise HTTPException(404, 'Run tidak ada')
    return run


@app.get('/runs/{run_id}', response_class=HTMLResponse)
def run_page(request: Request, run_id: int, role: str = Depends(viewer), conn=Depends(get_conn)):
    run = get_run(conn, run_id)
    return render(request, 'run.html', run=run,
                  endpoints=conn.execute('SELECT * FROM run_endpoints WHERE run_id = ? '
                                         "ORDER BY endpoint != 'all', rowid", (run_id,)).fetchall(),
                  resources=conn.execute('SELECT * FROM run_resources WHERE run_id = ? ORDER BY service',
                                         (run_id,)).fetchall(),
                  events=conn.execute('SELECT * FROM events WHERE run_id = ? ORDER BY id', (run_id,)).fetchall(),
                  attempts=conn.execute('SELECT id, attempt, status, label FROM runs WHERE plan_id = ? AND seq = ? '
                                        'ORDER BY attempt', (run['plan_id'], run['seq'])).fetchall(),
                  has_summary=bool(run['summary_path'] and os.path.isfile(run['summary_path'])),
                  has_log=bool(run['log_path'] and os.path.isfile(run['log_path'])))


@app.get('/runs/{run_id}/summary.json')
def run_summary(run_id: int, role: str = Depends(viewer), conn=Depends(get_conn)):
    run = get_run(conn, run_id)
    if not run['summary_path'] or not os.path.isfile(run['summary_path']):
        raise HTTPException(404, 'Ringkasan k6 tidak ada')
    return FileResponse(run['summary_path'], media_type='application/json', filename=f'{run["label"]}.json')


@app.get('/runs/{run_id}/log')
def run_log(run_id: int, role: str = Depends(viewer), conn=Depends(get_conn)):
    run = get_run(conn, run_id)
    if not run['log_path'] or not os.path.isfile(run['log_path']):
        raise HTTPException(404, 'Log tidak ada')
    return FileResponse(run['log_path'], media_type='text/plain; charset=utf-8')


@app.post('/runs/{run_id}/cancel')
def cancel_run(request: Request, run_id: int, csrf: str = Form(''), role: str = Depends(controller),
               conn=Depends(get_conn)):
    check_csrf(request, csrf)
    run = get_run(conn, run_id)
    if run['status'] == 'running':
        conn.execute('UPDATE runs SET cancel_requested = 1 WHERE id = ?', (run_id,))
    elif run['status'] == 'queued':
        conn.execute("UPDATE runs SET status = 'cancelled' WHERE id = ?", (run_id,))
    db.log(conn, run_id, 'pembatalan diminta dari konsol', 'warning')
    return back(request)


@app.post('/runs/{run_id}/retry')
def retry_run(request: Request, run_id: int, csrf: str = Form(''), role: str = Depends(controller),
              conn=Depends(get_conn)):
    check_csrf(request, csrf)
    run = get_run(conn, run_id)
    if run['status'] not in ('invalid', 'failed', 'cancelled'):
        raise HTTPException(409, 'Hanya run yang tidak valid, gagal, atau dibatalkan yang bisa diulang')
    with db.transaction(conn):
        new_id = plan.requeue(conn, run)
    db.log(conn, new_id, f'diulang dari run {run_id} lewat konsol')
    return back(request)


# --- results and export --------------------------------------------------------------

def chart_series(groups: list[dict], kind: str, scenario: str, metric: str, scale: float = 1.0) -> dict:
    series: dict[str, list] = {}
    for group in groups:
        if group['kind'] == kind and group['scenario'] == scenario and group['vus'] is not None:
            mean, sd = group[f'{metric}_mean'], group[f'{metric}_sd']
            series.setdefault(group['impl'], []).append(
                (group['vus'], None if mean is None else mean / scale, None if sd is None else sd / scale))
    return series


@app.get('/results', response_class=HTMLResponse)
def results_page(request: Request, suite: str = 'web', endpoint: str = 'all', role: str = Depends(viewer),
                 conn=Depends(get_conn)):
    groups = stats.aggregate(conn, suite, endpoint)
    endpoints = [row[0] for row in conn.execute(
        "SELECT DISTINCT e.endpoint FROM run_endpoints e JOIN runs r ON r.id = e.run_id WHERE r.suite = ? "
        "ORDER BY e.endpoint != 'all', e.endpoint", (suite,))]
    figures = []
    for kind in ('load', 'stress'):
        for scenario in ('read', 'write'):
            figures.append({
                'title': f'{kind.capitalize()} {scenario}',
                'p95': charts.line_chart(chart_series(groups, kind, scenario, 'p95_ms'), 'VU', 'p95 (ms)'),
                'throughput': charts.line_chart(chart_series(groups, kind, scenario, 'throughput'), 'VU', 'request/s'),
                'memory': charts.line_chart(chart_series(groups, kind, scenario, 'peak_memory_avg', 1048576), 'VU',
                                            'X-Peak-Memory rata-rata (MiB)'),
            })
    return render(request, 'results.html', groups=groups, figures=figures, suite=suite, endpoint=endpoint,
                  endpoints=endpoints or ['all'])


def csv_response(name: str, header: list[str], rows) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow(['' if value is None else value for value in row])
    return Response(buffer.getvalue(), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="{name}"'})


RUN_EXPORT = ('id', 'label', 'plan_id', 'suite', 'block', 'seq', 'attempt', 'impl', 'kind', 'scenario', 'script',
              'vus', 'status', 'valid', 'invalid_reason', 'error', 'tag', 'git_commit', 'started_at', 'k6_started_at',
              'k6_ended_at', 'sustain_start', 'sustain_end', 'sustain_estimated', 'ended_at', 'k6_cpu_avg',
              'k6_cpu_max', 'steal_all', 'steal_cpu1')
RUN_KEYS = ('id', 'label', 'plan_id', 'suite', 'block', 'seq', 'attempt', 'impl', 'kind', 'scenario', 'vus',
            'status', 'valid')


@app.get('/export/runs.csv')
def export_runs(role: str = Depends(viewer), conn=Depends(get_conn)):
    rows = conn.execute(f'SELECT {", ".join(RUN_EXPORT)} FROM runs ORDER BY plan_id, seq, attempt').fetchall()
    return csv_response('runs.csv', list(RUN_EXPORT), rows)


@app.get('/export/endpoints.csv')
def export_endpoints(role: str = Depends(viewer), conn=Depends(get_conn)):
    columns = ('endpoint', 'requests', 'throughput', 'error_rate', 'avg_ms', 'med_ms', 'p95_ms', 'max_ms',
               'peak_memory_avg', 'peak_memory_max')
    rows = conn.execute(f'SELECT {", ".join("r." + c for c in RUN_KEYS)}, {", ".join("e." + c for c in columns)} '
                        'FROM run_endpoints e JOIN runs r ON r.id = e.run_id '
                        'ORDER BY r.plan_id, r.seq, r.attempt, e.endpoint').fetchall()
    return csv_response('endpoints.csv', ['run_id', *RUN_KEYS[1:], *columns], rows)


@app.get('/export/resources.csv')
def export_resources(role: str = Depends(viewer), conn=Depends(get_conn)):
    columns = ('service', 'container', 'cpu_avg', 'cpu_max', 'mem_avg', 'mem_max', 'samples')
    rows = conn.execute(f'SELECT {", ".join("r." + c for c in RUN_KEYS)}, {", ".join("x." + c for c in columns)} '
                        'FROM run_resources x JOIN runs r ON r.id = x.run_id '
                        'ORDER BY r.plan_id, r.seq, r.attempt, x.service').fetchall()
    return csv_response('resources.csv', ['run_id', *RUN_KEYS[1:], *columns], rows)


@app.get('/export/aggregate.csv')
def export_aggregate(suite: str = 'web', role: str = Depends(viewer), conn=Depends(get_conn)):
    groups = stats.aggregate(conn, suite, None)
    header = ['suite', 'kind', 'scenario', 'vus', 'impl', 'endpoint', 'n', 'blocks']
    for metric in stats.METRICS:
        header += [f'{metric}_mean', f'{metric}_sd']
    rows = [[suite, g['kind'], g['scenario'], g['vus'], g['impl'], g['endpoint'], g['n'],
             ' '.join(map(str, g['blocks']))] + [g[h] for h in header[8:]] for g in groups]
    return csv_response(f'aggregate-{suite}.csv', header, rows)
