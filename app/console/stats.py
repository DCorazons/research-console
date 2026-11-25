"""Validity of a run and the summary of valid runs across blocks."""

import math
import sqlite3

from . import config

METRICS = ('p95_ms', 'avg_ms', 'med_ms', 'max_ms', 'throughput', 'error_rate', 'peak_memory_avg',
           'peak_memory_max', 'app_cpu_avg', 'app_mem_max', 'db_cpu_avg', 'db_mem_max')

KIND_ORDER = {'single': 0, 'load': 1, 'stress': 2}
SCENARIO_ORDER = {'single': 0, 'read': 1, 'write': 2}
IMPL_ORDER = {'ar': 0, 'repo': 1, 'qb': 2}


def validity(k6_cpu_max: float | None, steal_all: float | None, steal_cpu1: float | None,
             cpu_limit: float | None = None, steal_limit: float | None = None) -> tuple[bool, list[str]]:
    cpu_limit = config.K6_CPU_LIMIT if cpu_limit is None else cpu_limit
    steal_limit = config.STEAL_LIMIT if steal_limit is None else steal_limit
    reasons = []
    if k6_cpu_max is None:
        reasons.append('CPU k6 tidak terbaca dari Prometheus')
    elif k6_cpu_max >= cpu_limit:
        reasons.append(f'CPU k6 maksimum {k6_cpu_max:.0%} >= {cpu_limit:.0%}')
    if steal_all is None or steal_cpu1 is None:
        reasons.append('steal tidak terbaca dari /proc/stat')
    else:
        if steal_all >= steal_limit:
            reasons.append(f'steal rata-rata {steal_all:.1%} >= {steal_limit:.0%}')
        if steal_cpu1 >= steal_limit:
            reasons.append(f'steal CPU 1 {steal_cpu1:.1%} >= {steal_limit:.0%}')
    return not reasons, reasons


def mean_sd(values: list[float]) -> tuple[float | None, float | None]:
    values = [v for v in values if v is not None]
    if not values:
        return None, None
    mean = sum(values) / len(values)
    if len(values) < 2:
        return mean, None
    return mean, math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def valid_rows(conn: sqlite3.Connection, suite: str, endpoint: str | None = 'all') -> list[sqlite3.Row]:
    """One row per valid run and endpoint, with the app and database resources"""
    sql = """
        SELECT r.id AS run_id, r.label, r.plan_id, r.block, r.impl, r.kind, r.scenario, r.vus, e.*,
               app.cpu_avg AS app_cpu_avg, app.mem_max AS app_mem_max,
               db.cpu_avg AS db_cpu_avg, db.mem_max AS db_mem_max
        FROM runs r
        JOIN run_endpoints e ON e.run_id = r.id
        LEFT JOIN run_resources app ON app.run_id = r.id AND app.service = 'app'
        LEFT JOIN run_resources db ON db.run_id = r.id AND db.service = 'mariadb'
        WHERE r.suite = ? AND r.status = 'done' AND r.valid = 1
    """
    params: list = [suite]
    if endpoint is not None:
        sql += ' AND e.endpoint = ?'
        params.append(endpoint)
    return conn.execute(sql + ' ORDER BY r.plan_id, r.seq, r.attempt', params).fetchall()


def aggregate(conn: sqlite3.Connection, suite: str, endpoint: str | None = 'all') -> list[dict]:
    """Mean and sample standard deviation across blocks, per kind, scenario, VUs,
    implementation, and endpoint"""
    groups: dict[tuple, list[sqlite3.Row]] = {}
    for row in valid_rows(conn, suite, endpoint):
        key = (row['kind'], row['scenario'], row['vus'], row['impl'], row['endpoint'])
        groups.setdefault(key, []).append(row)

    result = []
    for (kind, scenario, vus, impl, ep), rows in groups.items():
        item = {'kind': kind, 'scenario': scenario, 'vus': vus, 'impl': impl, 'endpoint': ep, 'n': len(rows),
                'blocks': sorted({row['block'] for row in rows})}
        for metric in METRICS:
            item[f'{metric}_mean'], item[f'{metric}_sd'] = mean_sd([row[metric] for row in rows])
        result.append(item)

    result.sort(key=lambda g: (KIND_ORDER.get(g['kind'], 9), SCENARIO_ORDER.get(g['scenario'], 9), g['vus'] or 0,
                               g['endpoint'] != 'all', g['endpoint'], IMPL_ORDER.get(g['impl'], 9)))
    return result
