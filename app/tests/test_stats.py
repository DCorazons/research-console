import json
import math

from console import charts, metrics, plan, stats, summary


def test_validity():
    assert stats.validity(0.30, 0.0, 0.01, cpu_limit=0.8, steal_limit=0.05) == (True, [])
    valid, reasons = stats.validity(0.85, 0.06, 0.0, cpu_limit=0.8, steal_limit=0.05)
    assert not valid and len(reasons) == 2
    valid, reasons = stats.validity(None, None, None, cpu_limit=0.8, steal_limit=0.05)
    assert not valid and 'CPU k6' in reasons[0]


def test_mean_sd():
    assert stats.mean_sd([]) == (None, None)
    assert stats.mean_sd([5.0]) == (5.0, None)
    mean, sd = stats.mean_sd([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
    assert mean == 5.0
    assert math.isclose(sd, 2.138089935299395)


def test_summary_rows(tmp_path):
    path = tmp_path / 's.json'
    path.write_text(json.dumps({'scenario': 'read-web', 'sustain': [
        {'endpoint': 'all', 'requests': 100, 'throughput': 1.5, 'error_rate': 0, 'avg_ms': 10, 'med_ms': 9,
         'p95_ms': 20, 'max_ms': 30, 'peak_memory_avg': 3e6, 'peak_memory_max': 4e6},
        {'endpoint': 'GET courses', 'requests': 50},
    ]}))
    rows = summary.rows(summary.load(str(path)))
    assert rows[0]['p95_ms'] == 20 and rows[1]['endpoint'] == 'GET courses' and rows[1]['p95_ms'] is None


def test_proc_stat_and_steal(tmp_path):
    stat = tmp_path / 'stat'
    stat.write_text('cpu  100 0 50 800 0 0 0 50 10 0\ncpu0 50 0 25 400 0 0 0 25 0 0\ncpu1 50 0 25 400 0 0 0 25 0 0\n'
                    'intr 1 2 3\n')
    parsed = metrics.read_proc_stat(str(stat))
    assert parsed['cpu'] == (50, 1000) and parsed['cpu1'] == (25, 500)

    samples = [(0.0, {'cpu': (0, 0)}), (10.0, {'cpu': (5, 1000)}), (20.0, {'cpu': (25, 2000)}),
               (30.0, {'cpu': (25, 3000)})]
    assert metrics.steal_fraction(samples, 10.0, 20.0) == 20 / 1000
    # A window between samples takes the samples just outside it
    assert metrics.steal_fraction(samples, 12.0, 18.0) == 20 / 1000
    assert metrics.steal_fraction(samples, 5.0, 25.0) == 25 / 3000
    assert metrics.steal_fraction(samples[:1], 0, 10) is None


def _finished_run(conn, plan_id, seq, p95, valid=1):
    run = conn.execute('SELECT * FROM runs WHERE plan_id = ? AND seq = ?', (plan_id, seq)).fetchone()
    conn.execute("UPDATE runs SET status = ?, valid = ? WHERE id = ?", ('done' if valid else 'invalid', valid, run['id']))
    conn.execute('INSERT INTO run_endpoints (run_id, endpoint, requests, throughput, p95_ms, peak_memory_avg) '
                 "VALUES (?, 'all', 100, 20, ?, 3000000)", (run['id'], p95))
    conn.execute("INSERT INTO run_resources (run_id, service, container, cpu_avg) VALUES (?, 'app', 'x', 0.8)",
                 (run['id'],))
    return run


def test_aggregate_uses_only_valid_runs_across_blocks(conn):
    plan_id = plan.create(conn, 2, stress=False)
    per_block = 21
    # seq 2 = block 1, AR, load read 25; in block 2 AR comes third, so its load read 25 is seq 21 + 14 + 2
    _finished_run(conn, plan_id, 2, 100.0)
    _finished_run(conn, plan_id, per_block + 14 + 2, 140.0)
    _finished_run(conn, plan_id, 3, 999.0, valid=0)

    groups = stats.aggregate(conn, 'web')
    assert len(groups) == 1
    group = groups[0]
    assert (group['kind'], group['scenario'], group['vus'], group['impl']) == ('load', 'read', 25, 'ar')
    assert group['n'] == 2 and group['blocks'] == [1, 2]
    assert group['p95_ms_mean'] == 120.0
    assert math.isclose(group['p95_ms_sd'], math.sqrt(800))
    assert group['app_cpu_avg_mean'] == 0.8


def test_chart_draws_every_series():
    svg = charts.line_chart({'ar': [(25, 100.0, 10.0), (50, 200.0, None)], 'repo': [(25, 90.0, None)]}, 'VU', 'p95')
    assert svg.startswith('<svg') and svg.count('<polyline') == 2 and 'Active Record' in svg
    assert 'Belum ada' in charts.line_chart({}, 'VU', 'p95')
