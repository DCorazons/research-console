from console import db, plan


def test_block_holds_every_run_of_every_implementation():
    runs = plan.build(1, load_vus=(25, 50, 75), stress_vus=(75, 125, 175, 225, 275))
    # 3 implementations x (1 single + 2 scenarios x 3 levels) + 2 x 2 scenarios x 5 levels
    assert len(runs) == 3 * 7 + 2 * 2 * 5
    assert [r.seq for r in runs] == list(range(1, len(runs) + 1))
    assert {r.impl for r in runs if r.kind == 'stress'} == {'ar', 'repo'}


def test_implementation_order_rotates_by_block():
    assert plan.impl_order(1) == ('ar', 'repo', 'qb')
    assert plan.impl_order(2) == ('repo', 'qb', 'ar')
    assert plan.impl_order(3) == ('qb', 'ar', 'repo')
    assert plan.impl_order(4) == ('ar', 'repo', 'qb')
    assert plan.stress_order(1) == ('ar', 'repo')
    assert plan.stress_order(2) == ('repo', 'ar')


def test_runs_of_an_implementation_are_consecutive():
    runs = plan.build(2, load_vus=(25, 50, 75), stress_vus=(75, 125))
    block2 = [r for r in runs if r.block == 2]
    first_part = [r.impl for r in block2 if r.kind != 'stress']
    assert first_part == ['repo'] * 7 + ['qb'] * 7 + ['ar'] * 7
    assert [r.impl for r in block2 if r.kind == 'stress'] == ['repo'] * 4 + ['ar'] * 4
    first = [(r.kind, r.scenario, r.vus) for r in block2[:7]]
    assert first == [('single', 'single', None), ('load', 'read', 25), ('load', 'read', 50), ('load', 'read', 75),
                     ('load', 'write', 25), ('load', 'write', 50), ('load', 'write', 75)]


def test_suites_pick_their_scripts_and_stress_can_be_left_out():
    runs = plan.build(1, suite='api', stress=False, load_vus=(25,))
    assert {r.script for r in runs} == {'single.js', 'read.js', 'write.js'}
    assert all(r.kind != 'stress' for r in runs)
    assert {r.script for r in plan.build(1, load_vus=(25,), stress_vus=(75,))} == {
        'single-web.js', 'read-web.js', 'write-web.js'}


def test_labels():
    assert plan.label(3, 2, 15, 'repo', 'load', 'write', 50, 1) == 'p3-b02-015-repo-load-write-50-a1'
    assert plan.label(3, 2, 1, 'ar', 'single', 'single', None, 2) == 'p3-b02-001-ar-single-a2'


def test_create_and_requeue_keep_the_place_in_the_plan(conn):
    plan_id = plan.create(conn, 1, first_block=4, suite='web', stress=False)
    rows = conn.execute('SELECT * FROM runs WHERE plan_id = ? ORDER BY seq', (plan_id,)).fetchall()
    assert len(rows) == 21
    assert rows[0]['block'] == 4 and rows[0]['impl'] == 'ar' and rows[0]['status'] == 'queued'

    second = rows[1]
    db.update_run(conn, second['id'], status='invalid')
    db.update_run(conn, rows[0]['id'], status='done')
    with db.transaction(conn):
        retry_id = plan.requeue(conn, second)
    retry = conn.execute('SELECT * FROM runs WHERE id = ?', (retry_id,)).fetchone()
    assert retry['seq'] == second['seq'] and retry['attempt'] == 2
    assert retry['label'].endswith('-a2')
    # The retry runs before the rest of the plan
    assert db.next_queued(conn)['id'] == retry_id
