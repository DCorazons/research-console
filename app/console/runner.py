"""The runner: takes queued runs one by one while the queue is not paused.

    python -m console.runner

Only this container has the Docker socket. It never listens on a port; the web
container talks to it through the control table and the run flags in SQLite.
"""

import json
import signal
import sys
import time

from . import checks, config, db, procedure
from .config import IMPLEMENTATIONS

IDLE_CHECK_EVERY_S = 60


def recover(conn) -> None:
    """A run left 'running' means the runner stopped in the middle of it"""
    for run in conn.execute("SELECT * FROM runs WHERE status = 'running'").fetchall():
        db.update_run(conn, run['id'], status='failed', error='runner berhenti di tengah run', ended_at=time.time())
        db.log(conn, run['id'], 'runner berhenti di tengah run; stack dihentikan, antrean dijeda', 'error')
        impl = IMPLEMENTATIONS[run['impl']]
        procedure.subprocess.run(impl.compose('down'), capture_output=True, timeout=180)
        procedure.subprocess.run(['docker', 'rm', '-f', f'research-k6-{run["id"]}'], capture_output=True, timeout=60)
        db.set_control(conn, 'paused', '1')


def idle_checks(conn) -> None:
    results = checks.preflight(list(IMPLEMENTATIONS.values()))
    db.set_control(conn, 'preflight', json.dumps({'at': time.time(), 'results': results}))


def main() -> None:
    # docker stop sends SIGTERM: leave through the finally blocks so the stack is stopped
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    conn = db.connect()
    db.init(conn)
    recover(conn)
    db.log(conn, None, f'runner mulai (data {config.DATA_DIR}, hasil {config.RESULTS_DIR})')
    last_check = 0.0
    while True:
        db.set_control(conn, 'runner_heartbeat', time.time())
        run = None if db.is_paused(conn) else db.next_queued(conn)
        if run is None:
            if time.time() - last_check > IDLE_CHECK_EVERY_S or db.get_control(conn, 'check_now') == '1':
                db.set_control(conn, 'check_now', '0')
                idle_checks(conn)
                last_check = time.time()
            time.sleep(2)
            continue
        db.set_control(conn, 'current_run', run['id'])
        procedure.execute(conn, run)
        db.set_control(conn, 'current_run', None)
        last_check = 0.0


if __name__ == '__main__':
    main()
