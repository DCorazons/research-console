"""SQLite store shared by the web and runner containers (WAL, one file).

Times are Unix seconds (UTC). The runner owns the runs while they run; the web
only creates plans and sets control flags (paused, cancel_requested).
"""

import os
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY,
    created_at REAL NOT NULL,
    suite TEXT NOT NULL,
    first_block INTEGER NOT NULL,
    blocks INTEGER NOT NULL,
    stress INTEGER NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    plan_id INTEGER NOT NULL REFERENCES plans(id),
    suite TEXT NOT NULL,
    block INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    impl TEXT NOT NULL,
    kind TEXT NOT NULL,
    scenario TEXT NOT NULL,
    script TEXT NOT NULL,
    vus INTEGER,
    label TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'queued',
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    tag TEXT,
    git_commit TEXT,
    started_at REAL,
    k6_started_at REAL,
    k6_ended_at REAL,
    sustain_start REAL,
    sustain_end REAL,
    sustain_estimated INTEGER,
    ended_at REAL,
    k6_cpu_avg REAL,
    k6_cpu_max REAL,
    steal_all REAL,
    steal_cpu1 REAL,
    valid INTEGER,
    invalid_reason TEXT,
    error TEXT,
    summary_path TEXT,
    log_path TEXT
);
CREATE INDEX IF NOT EXISTS runs_queue ON runs (status, plan_id, seq, attempt);

CREATE TABLE IF NOT EXISTS run_endpoints (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    endpoint TEXT NOT NULL,
    requests INTEGER,
    throughput REAL,
    error_rate REAL,
    avg_ms REAL,
    med_ms REAL,
    p95_ms REAL,
    max_ms REAL,
    peak_memory_avg REAL,
    peak_memory_max REAL,
    PRIMARY KEY (run_id, endpoint)
);

CREATE TABLE IF NOT EXISTS run_resources (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    service TEXT NOT NULL,
    container TEXT NOT NULL,
    cpu_avg REAL,
    cpu_max REAL,
    mem_avg REAL,
    mem_max REAL,
    samples INTEGER,
    PRIMARY KEY (run_id, service)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    run_id INTEGER,
    at REAL NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_run ON events (run_id, id);

CREATE TABLE IF NOT EXISTS control (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

RUN_COLUMNS = (
    'status', 'cancel_requested', 'tag', 'git_commit', 'started_at', 'k6_started_at', 'k6_ended_at',
    'sustain_start', 'sustain_end', 'sustain_estimated', 'ended_at', 'k6_cpu_avg', 'k6_cpu_max',
    'steal_all', 'steal_cpu1', 'valid', 'invalid_reason', 'error', 'summary_path', 'log_path',
)


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=15000')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO control (key, value) VALUES ('paused', '1')")


@contextmanager
def transaction(conn: sqlite3.Connection):
    conn.execute('BEGIN IMMEDIATE')
    try:
        yield conn
    except BaseException:
        conn.execute('ROLLBACK')
        raise
    conn.execute('COMMIT')


def get_control(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute('SELECT value FROM control WHERE key = ?', (key,)).fetchone()
    return default if row is None else row['value']


def set_control(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute('INSERT INTO control (key, value) VALUES (?, ?) '
                 'ON CONFLICT(key) DO UPDATE SET value = excluded.value', (key, None if value is None else str(value)))


def is_paused(conn: sqlite3.Connection) -> bool:
    return get_control(conn, 'paused', '1') == '1'


def log(conn: sqlite3.Connection, run_id: int | None, message: str, level: str = 'info') -> None:
    conn.execute('INSERT INTO events (run_id, at, level, message) VALUES (?, ?, ?, ?)',
                 (run_id, time.time(), level, message))


def update_run(conn: sqlite3.Connection, run_id: int, **fields) -> None:
    unknown = set(fields) - set(RUN_COLUMNS)
    if unknown:
        raise ValueError(f'unknown run columns: {sorted(unknown)}')
    if fields:
        assignments = ', '.join(f'{name} = ?' for name in fields)
        conn.execute(f'UPDATE runs SET {assignments} WHERE id = ?', (*fields.values(), run_id))


def next_queued(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM runs WHERE status = 'queued' "
                        'ORDER BY plan_id, seq, attempt, id LIMIT 1').fetchone()
