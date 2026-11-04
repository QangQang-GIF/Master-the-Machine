"""Lane mode: 3 normal slots + 2 reserved slots that only PI and Heavy items may use."""
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


def test_reserved_lane_only_for_pi_and_heavy(lanes):
    project(lanes, item('BUG-01'), item('BUG-02'), item('CR-01'), item('CR-02'), item('TASK-04'))
    plan = lanes.plan_tasks()
    # Five free slots but only three normal ones: ordinary items never take reserved slots.
    assert keys(plan) == ['BUG-01', 'BUG-02', 'CR-01']
    assert {t['laneSlot'] for t in plan['selected']} == {'normal'}


def test_pi_and_heavy_dispatch_immediately_while_normals_busy(lanes):
    project(lanes, item('BUG-01'), item('BUG-02'), item('CR-01'), item('CR-02'))
    for k in ('BUG-01', 'BUG-02', 'CR-01'):
        run(lanes, k)
    assert keys(lanes.plan_tasks()) == []
    project(lanes, item('PI-01'), item('TASK-05', title='[Heavy Task] Rebuild reporting'))
    plan = lanes.plan_tasks()
    assert keys(plan) == ['PI-01', 'TASK-05']
    assert all(t['laneSlot'] == 'reserved' for t in plan['selected'])
    assert plan['selected'][1]['heavy'] is True


def test_pi_precedes_heavy_and_reserved_cap(lanes):
    project(lanes, item('TASK-05', title='[Heavy Task] A'), item('TASK-06', title='[heavy task] B'), item('PI-01'))
    plan = lanes.plan_tasks()
    assert [t['key'] for t in plan['selected'] if t['laneSlot'] == 'reserved'] == ['PI-01', 'TASK-05']
    run(lanes, 'PI-01'); run(lanes, 'TASK-05')
    assert 'TASK-06' not in keys(lanes.plan_tasks())
    with pytest.raises(NativeCoordinationError):
        run(lanes, 'TASK-06')


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


def test_imminent_deadline_borrows_idle_reserved_slot(lanes):
    project(lanes, item('CR-01'), item('CR-02'), item('CR-03'))
    for k in ('CR-01', 'CR-02', 'CR-03'):
        run(lanes, k)
    project(lanes, item('TASK-03', deadline_epoch=NOW + 170))
    plan = lanes.plan_tasks()
    assert keys(plan) == ['TASK-03']
    assert plan['selected'][0]['laneSlot'] == 'reserved'
    claim = run(lanes, 'TASK-03')
    assert claim['taskEnvelope']['lane'] == 'reserved'
    # The borrowed slot counts against the reserved lane.
    assert lanes.plan_tasks()['lanes']['reserved']['running'] == 1


def test_no_borrow_when_pi_or_heavy_waiting(lanes):
    project(lanes, item('CR-01'), item('CR-02'), item('CR-03'))
    for k in ('CR-01', 'CR-02', 'CR-03'):
        run(lanes, k)
    project(lanes, item('TASK-03', deadline_epoch=NOW + 170), item('PI-01'), item('TASK-05', title='[Heavy Task] X'))
    plan = lanes.plan_tasks()
    assert keys(plan) == ['PI-01', 'TASK-05']


def test_borrow_can_be_disabled(tmp_path):
    c = make(tmp_path, allow_reserved_borrow=False)
    project(c, item('CR-01'), item('CR-02'), item('CR-03'))
    for k in ('CR-01', 'CR-02', 'CR-03'):
        run(c, k)
    project(c, item('TASK-03', deadline_epoch=NOW + 170))
    assert keys(c.plan_tasks()) == []


def test_heavy_by_operator_key_and_budget_cap(tmp_path):
    c = make(tmp_path, heavy_keys=['CR-09'], heavy_max_seconds=300)
    project(c, item('CR-09'))
    plan = c.plan_tasks()
    assert plan['selected'][0]['laneSlot'] == 'reserved'
    claim = run(c, 'CR-09')
    assert claim['taskEnvelope']['heavy'] is True
    assert claim['taskEnvelope']['time_budget_seconds'] <= 300


def test_heavy_by_difficulty_threshold(tmp_path):
    c = make(tmp_path, heavy_min_difficulty=3.5)
    project(c, item('CR-01', title='Add aging report with screenshot', minimumVerificationScenarios=[{'id': f'TC-{i}'} for i in range(6)]),
            item('BUG-03', title='Fix status precedence'))
    lanes_by_key = {t['key']: t['laneSlot'] for t in c.plan_tasks()['selected']}
    assert lanes_by_key == {'CR-01': 'reserved', 'BUG-03': 'normal'}


def test_invalid_lane_capacity_rejected(tmp_path):
    with pytest.raises(NativeCoordinationError):
        make(tmp_path, max_workers=4, normal_workers=3, reserved_workers=2)


def test_legacy_mode_unchanged(tmp_path):
    c = make(tmp_path, max_workers=4, normal_workers=3, reserved_workers=None)
    project(c, item('BUG-01'), item('PI-01'))
    plan = c.plan_tasks()
    assert 'lanes' not in plan
    assert set(keys(plan)) == {'BUG-01', 'PI-01'}
