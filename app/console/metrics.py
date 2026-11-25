"""Measurements around a run: CPU steal from /proc/stat, the sustain window from
InfluxDB, and container CPU and memory from Prometheus (cAdvisor)."""

import threading
import time

import httpx

from . import config


def read_proc_stat(path: str = '/proc/stat') -> dict[str, tuple[int, int]]:
    """(steal, total) jiffies per CPU line: 'cpu' (all CPUs), 'cpu0', 'cpu1', ...

    /proc/stat is not namespaced, so inside a container it shows the whole VPS.
    guest and guest_nice are left out of the total: they are already part of user.
    """
    result = {}
    with open(path, encoding='ascii') as file:
        for line in file:
            if not line.startswith('cpu'):
                continue
            name, *values = line.split()
            fields = [int(v) for v in values[:8]] + [0] * (8 - len(values[:8]))
            result[name] = (fields[7], sum(fields))
    return result


class ProcStatSampler(threading.Thread):
    """Reads /proc/stat every interval seconds while a run is measured"""

    def __init__(self, interval: float = 1.0, path: str = '/proc/stat'):
        super().__init__(daemon=True)
        self.interval = interval
        self.path = path
        self.samples: list[tuple[float, dict[str, tuple[int, int]]]] = []
        self._halt = threading.Event()

    def run(self):
        while not self._halt.is_set():
            try:
                self.samples.append((time.time(), read_proc_stat(self.path)))
            except OSError:
                pass
            self._halt.wait(self.interval)

    def stop(self):
        self._halt.set()
        if self.is_alive():
            self.join(timeout=5)
        try:
            self.samples.append((time.time(), read_proc_stat(self.path)))
        except OSError:
            pass


def steal_fraction(samples, start: float, end: float, cpu: str = 'cpu') -> float | None:
    """Share of CPU time lost to steal between start and end, from the samples around them"""
    if len(samples) < 2 or end <= start:
        return None
    before = [s for s in samples if s[0] <= start]
    after = [s for s in samples if s[0] >= end]
    first = before[-1] if before else samples[0]
    last = after[0] if after else samples[-1]
    if last[0] <= first[0] or cpu not in first[1] or cpu not in last[1]:
        return None
    steal = last[1][cpu][0] - first[1][cpu][0]
    total = last[1][cpu][1] - first[1][cpu][1]
    return steal / total if total > 0 else None


def _influx_time(query: str) -> float | None:
    response = httpx.get(f'{config.INFLUX_URL}/query',
                         params={'db': config.INFLUX_DB, 'q': query, 'epoch': 'ms'}, timeout=30)
    response.raise_for_status()
    results = response.json().get('results', [{}])
    series = results[0].get('series') if results else None
    if not series:
        return None
    return series[0]['values'][0][0] / 1000


def sustain_window(run_label: str) -> tuple[float, float] | None:
    """First and last request of the sustain phase of the run (k6 tags run_id and phase)"""
    where = f"WHERE \"run_id\" = '{run_label}' AND \"phase\" = 'sustain'"
    start = _influx_time(f'SELECT first("value") FROM "http_req_duration" {where}')
    end = _influx_time(f'SELECT last("value") FROM "http_req_duration" {where}')
    if start is None or end is None or end <= start:
        return None
    return start, end


def _prometheus_range(query: str, start: float, end: float, step: int = 5) -> list[float]:
    response = httpx.get(f'{config.PROMETHEUS_URL}/api/v1/query_range',
                         params={'query': query, 'start': start, 'end': end, 'step': step}, timeout=30)
    response.raise_for_status()
    result = response.json()['data']['result']
    if not result:
        return []
    return [float(value) for _, value in result[0]['values']]


def container_usage(container: str, start: float, end: float) -> dict | None:
    """CPU (cores, 1.0 = one full CPU) and memory working set (bytes) of a container
    in the window, from points every 5 s (CPU as a 15 s rate)"""
    cpu = _prometheus_range(f'sum(rate(container_cpu_usage_seconds_total{{name="{container}"}}[15s]))', start, end)
    memory = _prometheus_range(f'max(container_memory_working_set_bytes{{name="{container}"}})', start, end)
    if not cpu and not memory:
        return None
    return {
        'cpu_avg': sum(cpu) / len(cpu) if cpu else None,
        'cpu_max': max(cpu) if cpu else None,
        'mem_avg': sum(memory) / len(memory) if memory else None,
        'mem_max': max(memory) if memory else None,
        'samples': len(cpu),
    }
