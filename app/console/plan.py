"""Block plan of the experiment (proposal 3.2.4, block design).

A block holds every run of every implementation. Inside a block:

  1. single and load runs, implementation by implementation, in an order rotated by
     block number over AR, Repo, QB (block 1 AR-Repo-QB, block 2 Repo-QB-AR,
     block 3 QB-AR-Repo, block 4 again AR-Repo-QB, ...);
  2. stress runs of AR and Repo only (the Query Builder reference does not take part
     in stress testing), alternating which one goes first (odd blocks AR first).

Each implementation runs: single, load read at every load level, load write at every
load level, then (in part 2) stress read and stress write at every stress level.
Stress levels are fixed steps, the same for both implementations; the saturation
point is read from the results, the run is not stopped at it.
"""

import sqlite3
import time
from dataclasses import dataclass

from . import config
from .db import transaction

IMPLS = ('ar', 'repo', 'qb')
STRESS_IMPLS = ('ar', 'repo')
SCENARIOS = ('read', 'write')

SCRIPTS = {
    'web': {'single': 'single-web.js', 'read': 'read-web.js', 'write': 'write-web.js'},
    'api': {'single': 'single.js', 'read': 'read.js', 'write': 'write.js'},
}


@dataclass(frozen=True)
class PlannedRun:
    block: int
    seq: int
    impl: str
    kind: str
    scenario: str
    script: str
    vus: int | None


def impl_order(block: int) -> tuple[str, ...]:
    shift = (block - 1) % len(IMPLS)
    return IMPLS[shift:] + IMPLS[:shift]


def stress_order(block: int) -> tuple[str, ...]:
    return STRESS_IMPLS if block % 2 == 1 else STRESS_IMPLS[::-1]


def build(blocks: int, first_block: int = 1, suite: str = 'web', stress: bool = True,
          load_vus: tuple[int, ...] | None = None, stress_vus: tuple[int, ...] | None = None) -> list[PlannedRun]:
    if suite not in SCRIPTS:
        raise ValueError(f'unknown suite {suite}')
    if blocks < 1 or first_block < 1:
        raise ValueError('blocks and first_block must be at least 1')
    load_vus = load_vus or config.LOAD_VUS
    stress_vus = stress_vus or config.STRESS_VUS
    scripts = SCRIPTS[suite]

    runs: list[PlannedRun] = []

    def add(block, impl, kind, scenario, vus):
        runs.append(PlannedRun(block, len(runs) + 1, impl, kind, scenario, scripts[scenario], vus))

    for block in range(first_block, first_block + blocks):
        for impl in impl_order(block):
            add(block, impl, 'single', 'single', None)
            for scenario in SCENARIOS:
                for vus in load_vus:
                    add(block, impl, 'load', scenario, vus)
        if stress:
            for impl in stress_order(block):
                for scenario in SCENARIOS:
                    for vus in stress_vus:
                        add(block, impl, 'stress', scenario, vus)
    return runs


def label(plan_id: int, block: int, seq: int, impl: str, kind: str, scenario: str, vus: int | None,
          attempt: int) -> str:
    """RUN_ID of k6 and the base name of the summary and log files"""
    what = 'single' if kind == 'single' else f'{kind}-{scenario}-{vus}'
    return f'p{plan_id}-b{block:02d}-{seq:03d}-{impl}-{what}-a{attempt}'


def create(conn: sqlite3.Connection, blocks: int, first_block: int = 1, suite: str = 'web',
           stress: bool = True, note: str | None = None) -> int:
    runs = build(blocks, first_block, suite, stress)
    with transaction(conn):
        cursor = conn.execute('INSERT INTO plans (created_at, suite, first_block, blocks, stress, note) '
                              'VALUES (?, ?, ?, ?, ?, ?)', (time.time(), suite, first_block, blocks, int(stress), note))
        plan_id = cursor.lastrowid
        conn.executemany(
            'INSERT INTO runs (plan_id, suite, block, seq, attempt, impl, kind, scenario, script, vus, label) '
            'VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)',
            [(plan_id, suite, r.block, r.seq, r.impl, r.kind, r.scenario, r.script, r.vus,
              label(plan_id, r.block, r.seq, r.impl, r.kind, r.scenario, r.vus, 1)) for r in runs])
    return plan_id


def requeue(conn: sqlite3.Connection, run: sqlite3.Row) -> int:
    """Queues the same run again as the next attempt; it keeps its place (seq) in the plan"""
    attempt = conn.execute('SELECT MAX(attempt) FROM runs WHERE plan_id = ? AND seq = ?',
                           (run['plan_id'], run['seq'])).fetchone()[0] + 1
    cursor = conn.execute(
        'INSERT INTO runs (plan_id, suite, block, seq, attempt, impl, kind, scenario, script, vus, label) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (run['plan_id'], run['suite'], run['block'], run['seq'], attempt, run['impl'], run['kind'],
         run['scenario'], run['script'], run['vus'],
         label(run['plan_id'], run['block'], run['seq'], run['impl'], run['kind'], run['scenario'], run['vus'],
               attempt)))
    return cursor.lastrowid
