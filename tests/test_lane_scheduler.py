"""V2 lane mode: 3 normal slots (Normal + Heavy) + 2 reserved slots that only PI may use and nobody may borrow."""
import subprocess

import pytest

from mtm_competition.scheduling.native import NativeTaskCoordinator, NativeCoordinationError

NOW = 1_000_000


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def make(tmp_path, **kw):
    repo = tmp_path / 'checkout'
    repo.mkdir()
    git(repo, 'init')
    git(repo, 'config', 'user.email', 'test@example.invalid')
    git(repo, 'config', 'user.name', 'Test')
    (repo / 'source').mkdir()
    (repo / 'source' / 'main.py').write_text('print(1)\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'baseline')
    opts = dict(max_workers=5, normal_workers=3, reserved_workers=2)
    opts.update(kw)
    return NativeTaskCoordinator(tmp_path / 'state', repo, repo / 'source', 'source', 'MATCH', **opts)


@pytest.fixture
def lanes(tmp_path):
    return make(tmp_path)


def project(c, *items):
    c.register_projection({'status': 'RUNNING', 'server_epoch': NOW, 'remaining_seconds': 2400, 'items': list(items)})


def item(key, **kw):
    return {'key': key, 'type': key.split('-')[0], 'status': 'OPEN', **kw}


def run(c, key):
    c.register_started(key, {})
    return c.claim_task(key, f'w-{key}')


def keys(plan):
    return [t['key'] for t in plan['selected']]


def test_reserved_lane_only_for_pi(lanes):
    project(lanes, item('BUG-01'), item('BUG-02'), item('CR-01'), item('CR-02'), item('TASK-04'))
    plan = lanes.plan_tasks()
    # Five free slots but only three normal ones: ordinary items never take reserved slots.
    assert keys(plan) == ['BUG-01', 'BUG-02', 'CR-01']
    assert {t['laneSlot'] for t in plan['selected']} == {'normal'}


def test_pi_dispatches_immediately_while_normals_busy_but_heavy_waits(lanes):
    project(lanes, item('BUG-01'), item('BUG-02'), item('CR-01'), item('CR-02'))
    for k in ('BUG-01', 'BUG-02', 'CR-01'):
        run(lanes, k)
    assert keys(lanes.plan_tasks()) == []
    project(lanes, item('PI-01'), item('TASK-05', title='[Heavy Task] Rebuild reporting'))
    plan = lanes.plan_tasks()
    # V2: Heavy is no longer allowed to use the PI reserve; only PI starts.
    assert keys(plan) == ['PI-01']
    assert plan['selected'][0]['laneSlot'] == 'reserved'
    with pytest.raises(NativeCoordinationError):
        run(lanes, 'TASK-05')


def test_heavy_uses_normal_lane_and_second_pi_uses_second_reserved_slot(lanes):
    project(lanes, item('TASK-05', title='[Heavy Task] A'), item('PI-01'), item('PI-02'))
    plan = lanes.plan_tasks()
    by_key = {t['key']: t['laneSlot'] for t in plan['selected']}
    assert by_key == {'PI-01': 'reserved', 'PI-02': 'reserved', 'TASK-05': 'normal'}
    assert plan['selected'][-1]['heavy'] is True


def test_pi_may_expand_into_idle_normal_slot_but_total_cap_holds(lanes):
    project(lanes, item('PI-01'), item('PI-02'), item('PI-03'), item('CR-01'))
    plan = lanes.plan_tasks()
    slots = {t['key']: t['laneSlot'] for t in plan['selected']}
    assert slots['PI-01'] == slots['PI-02'] == 'reserved'
    assert slots['PI-03'] == 'normal'  # reserve full -> idle normal slot
    for k in ('PI-01', 'PI-02', 'PI-03'):
        run(lanes, k)
    assert len(lanes.plan_tasks()['active']) <= 5


def test_arrival_order_not_score(lanes):
    project(lanes, item('CR-01', points=10))
    project(lanes, item('BUG-01', points=500))
    project(lanes, item('TASK-01', points=1))
    project(lanes, item('CR-02', points=900))
    # No ROI: first three by arrival.
    assert keys(lanes.plan_tasks()) == ['CR-01', 'BUG-01', 'TASK-01']


def test_deadline_items_first_in_normal_lane(lanes):
    project(lanes, item('CR-01'), item('CR-02'), item('CR-03'))
    project(lanes, item('TASK-03', deadline_epoch=NOW + 480))
    assert keys(lanes.plan_tasks())[0] == 'TASK-03'


def test_imminent_deadline_item_can_never_borrow_pi_reserve(lanes):
    project(lanes, item('CR-01'), item('CR-02'), item('CR-03'))
    for k in ('CR-01', 'CR-02', 'CR-03'):
        run(lanes, k)
    project(lanes, item('TASK-03', deadline_epoch=NOW + 170))
    assert keys(lanes.plan_tasks()) == []
    with pytest.raises(NativeCoordinationError):
        run(lanes, 'TASK-03')


def test_only_pi_selected_when_pi_and_heavy_waiting(lanes):
    project(lanes, item('CR-01'), item('CR-02'), item('CR-03'))
    for k in ('CR-01', 'CR-02', 'CR-03'):
        run(lanes, k)
    project(lanes, item('TASK-03', deadline_epoch=NOW + 170), item('PI-01'), item('TASK-05', title='[Heavy Task] X'))
    assert keys(lanes.plan_tasks()) == ['PI-01']


def test_borrow_flag_is_ignored(tmp_path):
    c = make(tmp_path, allow_reserved_borrow=True)
    assert c.allow_reserved_borrow is False
    project(c, item('CR-01'), item('CR-02'), item('CR-03'))
    for k in ('CR-01', 'CR-02', 'CR-03'):
        run(c, k)
    project(c, item('TASK-03', deadline_epoch=NOW + 170))
    assert keys(c.plan_tasks()) == []


def test_heavy_by_operator_key_and_budget_cap(tmp_path):
    c = make(tmp_path, heavy_keys=['CR-09'], heavy_max_seconds=300)
    project(c, item('CR-09'))
    plan = c.plan_tasks()
    assert plan['selected'][0]['laneSlot'] == 'normal'
    claim = run(c, 'CR-09')
    assert claim['taskEnvelope']['heavy'] is True
    assert claim['taskEnvelope']['time_budget_seconds'] <= 300


def test_heavy_by_difficulty_threshold(tmp_path):
    c = make(tmp_path, heavy_min_difficulty=3.5)
    project(c, item('CR-01', title='Add aging report with screenshot', minimumVerificationScenarios=[{'id': f'TC-{i}'} for i in range(6)]),
            item('BUG-03', title='Fix status precedence'))
    sel = {t['key']: t for t in c.plan_tasks()['selected']}
    assert sel['CR-01']['heavy'] is True and sel['BUG-03']['heavy'] is False
    assert {t['laneSlot'] for t in sel.values()} == {'normal'}


def test_invalid_lane_capacity_rejected(tmp_path):
    with pytest.raises(NativeCoordinationError):
        make(tmp_path, max_workers=4, normal_workers=3, reserved_workers=2)


def test_legacy_mode_unchanged(tmp_path):
    c = make(tmp_path, max_workers=4, normal_workers=3, reserved_workers=None)
    project(c, item('BUG-01'), item('PI-01'))
    plan = c.plan_tasks()
    assert 'lanes' not in plan
    assert set(keys(plan)) == {'BUG-01', 'PI-01'}
