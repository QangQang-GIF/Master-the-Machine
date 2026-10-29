import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mtm_competition.scheduling.native import NativeTaskCoordinator, NativeCoordinationError


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def coordinator(tmp_path):
    repo = tmp_path / 'checkout'
    repo.mkdir()
    git(repo, 'init')
    git(repo, 'config', 'user.email', 'test@example.invalid')
    git(repo, 'config', 'user.name', 'Test')
    (repo / 'source').mkdir()
    (repo / 'source' / 'main.py').write_text('print(1)\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'baseline')
    c = NativeTaskCoordinator(tmp_path / 'state', repo, repo / 'source', 'source', 'MATCH')
    return c


def projection(c, *items, status='RUNNING', now=1_000_000):
    c.register_projection({'status': status, 'server_epoch': now, 'remaining_seconds': 2400, 'items': list(items)})


def item(key='BUG-01', **kw):
    return {'key': key, 'type': key.split('-')[0], 'status': 'OPEN', **kw}


def start(c, key, details=None):
    c.register_started(key, details or {})


def result(key='BUG-01', **kw):
    return {'task_id': key, 'status': 'READY_FOR_REVIEW', 'skill_ids': ['mtm-bug-worker'],
            'changed_files': ['main.py'], 'test_results': [{'command': 'pytest', 'passed': True, 'exit_code': 0}],
            'evidence_paths': [], 'elapsed_seconds': 2, **kw}


def test_reserves_capacity_for_urgent_task(coordinator):
    c = coordinator
    projection(c, item(), item('TASK-01'), item('PI-01'))
    assert {t['key'] for t in c.plan_tasks()['selected']} == {'PI-01', 'BUG-01'}
    start(c, 'BUG-01'); c.claim_task('BUG-01', 'normal')
    assert [t['key'] for t in c.plan_tasks()['selected']] == ['PI-01']
    start(c, 'PI-01'); c.claim_task('PI-01', 'urgent')
    assert c.plan_tasks()['availableSlots'] == 0
    assert c.plan_tasks()['selected'] == []


def test_requires_start_and_does_not_duplicate_claim(coordinator):
    c = coordinator
    projection(c, item())
    with pytest.raises(NativeCoordinationError, match='Start Work'):
        c.claim_task('BUG-01', 'worker')
    start(c, 'BUG-01')
    claim = c.claim_task('BUG-01', 'worker')
    assert Path(claim['matchRoot']).is_dir()
    assert claim['taskEnvelope']['model'] == 'gpt-6-luna'
    assert claim['taskEnvelope']['reasoning_effort'] == 'low'
    with pytest.raises(NativeCoordinationError):
        c.claim_task('BUG-01', 'other')


def test_two_coordinators_cannot_oversubscribe(coordinator):
    c = coordinator
    projection(c, item(), item('TASK-01'))
    start(c, 'BUG-01'); start(c, 'TASK-01')
    other = NativeTaskCoordinator(c.db_path.parent, c.checkout_root, c.match_root, 'source', 'MATCH')
    def claim(args):
        coord, key = args
        try:
            coord.claim_task(key, key)
            return True
        except NativeCoordinationError:
            return False
    with ThreadPoolExecutor(2) as pool:
        claimed = list(pool.map(claim, [(c, 'BUG-01'), (other, 'TASK-01')]))
    assert sum(claimed) == 1
    assert len(c.plan_tasks()['active']) == 1


@pytest.mark.parametrize('status', ['PAUSED', 'FINISHED', 'UNKNOWN'])
def test_pause_end_and_unknown_prevent_claims(coordinator, status):
    projection(coordinator, item(), status=status)
    start(coordinator, 'BUG-01')
    assert coordinator.plan_tasks()['selected'] == []
    with pytest.raises(NativeCoordinationError):
        coordinator.claim_task('BUG-01', 'worker')


def test_official_clock_required_and_deadline_excludes_closed(coordinator):
    c = coordinator
    c.register_projection({'status': 'RUNNING', 'items': [item()]})
    assert not c.plan_tasks()['selected']
    projection(c, item(deadline_epoch=999_999), item('TASK-01', status='CLOSED'), item('PI-01', deadline_epoch=1_000_150))
    assert [t['key'] for t in c.plan_tasks()['selected']] == ['PI-01']


def test_nearest_urgent_deadline_precedes_score(coordinator):
    c = coordinator
    projection(c, item(deadline_epoch=1_000_500, points=1000), item('CR-01', deadline_epoch=1_000_200, points=1))
    assert [t['key'] for t in c.plan_tasks()['selected']] == ['CR-01', 'BUG-01']


def test_dependency_and_path_overlap(coordinator):
    c = coordinator
    projection(c, item(claimed_paths=['main.py']), item('PI-01', claimed_paths=['main.py']), item('CR-01', dependencies=['TASK-01']))
    assert [t['key'] for t in c.plan_tasks()['selected']] == ['PI-01']
    start(c, 'PI-01'); c.claim_task('PI-01', 'urgent')
    assert not c.plan_tasks()['selected']


def test_result_rejects_missing_evidence_and_unproven_tests(coordinator):
    c = coordinator
    projection(c, item())
    start(c, 'BUG-01', {'evidence_required': ['answers/BUG-01/evidence/tests.log']})
    claim = c.claim_task('BUG-01', 'worker')
    with pytest.raises(NativeCoordinationError, match='Required evidence'):
        c.record_worker_result('BUG-01', result())
    path = Path(claim['matchRoot']) / 'answers/BUG-01/evidence/tests.log'
    path.parent.mkdir(parents=True)
    path.write_text('passed')
    ev = ['answers/BUG-01/evidence/tests.log']
    with pytest.raises(NativeCoordinationError, match='exit_code'):
        c.record_worker_result('BUG-01', result(evidence_paths=ev, test_results=[{'command':'pytest','passed': True}]))
    c.record_worker_result('BUG-01', result(evidence_paths=ev))
    assert c.plan_tasks()['readyToIntegrate'][0]['phase'] == 'READY_TO_INTEGRATE'
    assert c.plan_tasks()['availableSlots'] == 2


@pytest.mark.parametrize('relative', ['../escape', '/tmp/escape', 'C:\\escape'])
def test_result_path_confinement(coordinator, relative):
    c = coordinator
    projection(c, item()); start(c, 'BUG-01'); c.claim_task('BUG-01', 'worker')
    with pytest.raises(NativeCoordinationError, match='path'):
        c.record_worker_result('BUG-01', result(changed_files=[relative]))


def test_symlink_evidence_escape(coordinator, tmp_path):
    c = coordinator
    projection(c, item()); start(c, 'BUG-01'); claim = c.claim_task('BUG-01', 'worker')
    path = Path(claim['matchRoot']) / 'answers/BUG-01/evidence'
    path.mkdir(parents=True)
    outside = tmp_path / 'outside'; outside.write_text('not evidence')
    (path / 'file').symlink_to(outside)
    with pytest.raises(NativeCoordinationError, match='escapes'):
        c.record_worker_result('BUG-01', result(evidence_paths=['answers/BUG-01/evidence/file']))


def test_lock_survives_restart_until_receipt(coordinator):
    c = coordinator
    projection(c, item(), item('PI-01'))
    for key in ['BUG-01', 'PI-01']:
        start(c, key); c.claim_task(key, key); c.record_worker_result(key, result(key))
    c.begin_integration('BUG-01'); c.end_integration('BUG-01')
    other = NativeTaskCoordinator(c.db_path.parent, c.checkout_root, c.match_root, 'source', 'MATCH')
    with pytest.raises(NativeCoordinationError, match='lock'):
        other.begin_integration('PI-01')
    with pytest.raises(NativeCoordinationError, match='receipt'):
        other.mark_submitted('BUG-01', '')
    other.mark_submitted('BUG-01', 'actual-server-receipt')
    other.mark_submitted('BUG-01', 'actual-server-receipt')
    other.begin_integration('PI-01')
    assert not other.plan_tasks()['selected']


def test_identity_and_folder_confinement(coordinator):
    c = coordinator
    with pytest.raises(NativeCoordinationError, match='folder'):
        NativeTaskCoordinator(c.db_path.parent, c.checkout_root, c.match_root, '../source', 'OTHER')
    with pytest.raises(NativeCoordinationError, match='identity'):
        NativeTaskCoordinator(c.db_path.parent, c.checkout_root.parent / 'other', c.checkout_root.parent / 'other/source', 'source', 'MATCH')


def test_start_inputs_materialized_in_new_worktree(coordinator):
    c = coordinator
    projection(c, item())
    source = c.match_root / 'challenge'
    source.mkdir()
    (source / 'BUG-01-extra.md').write_text('current announced scenario')
    (source / 'BUG-99-secret.md').write_text('other task: do not copy')
    details = {'submitTemplate': {'status':'DONE'}, 'minimumVerificationScenarios': '# BUG-01 scenarios',
               'files': {'challenge/shared-seed.py':'print(1)'}}
    start(c, 'BUG-01', details)
    claim = c.claim_task('BUG-01', 'worker')
    root = Path(claim['matchRoot'])
    assert json.loads((root / 'answers/BUG-01/submit.json').read_text()) == details['submitTemplate']
    assert (root / 'challenge/shared-seed.py').read_text() == 'print(1)'
    assert (root / 'challenge/BUG-01-extra.md').is_file()
    assert not (root / 'challenge/BUG-99-secret.md').exists()
    assert claim['taskEnvelope']['mvs'] is None
    assert claim['taskEnvelope']['metadata']['start_details']['minimumVerificationScenarios'].startswith('#')


@pytest.mark.parametrize('path', ['../escape', '/tmp/escape', '.env', '.git/config', 'exam-info.json'])
def test_unsafe_start_inputs_rejected_before_worktree_creation(coordinator, path):
    c = coordinator
    projection(c, item())
    start(c, 'BUG-01', {'files': {path: 'forbidden'}})
    with pytest.raises(NativeCoordinationError):
        c.claim_task('BUG-01', 'worker')
    assert not (c.workers_root / 'BUG-01').exists()


def test_reconcile_retains_old_worktree_and_requires_stopped_worker(coordinator):
    c = coordinator
    projection(c, item()); start(c, 'BUG-01')
    first = c.claim_task('BUG-01', 'first')
    with pytest.raises(NativeCoordinationError, match='stopped'):
        c.reconcile_worker('BUG-01', confirmed_stopped=False, reason='not stopped', requeue=True)
    assert c.plan_tasks()['active']
    c.reconcile_worker('BUG-01', confirmed_stopped=True, reason='Native tool confirmed stopped', requeue=True)
    second = c.claim_task('BUG-01', 'second')
    assert first['worktreeRoot'] != second['worktreeRoot']
    assert Path(first['worktreeRoot']).is_dir()
    assert c.task_details('BUG-01')['data']['prior_attempts'][0]['worker'] == 'first'


def test_paused_clock_is_frozen_and_closed_integration_rejected(coordinator):
    c = coordinator
    projection(c, item(deadline_epoch=1_000_200))
    start(c, 'BUG-01'); c.claim_task('BUG-01', 'worker'); c.record_worker_result('BUG-01', result())
    projection(c, item(deadline_epoch=1_000_200, status='CLOSED'), now=1_000_201)
    with pytest.raises(NativeCoordinationError, match='officially open|deadline'):
        c.begin_integration('BUG-01')
    projection(c, item(), status='PAUSED')
    with c._db() as db:
        first = c._clock(db)
    time.sleep(.01)
    with c._db() as db:
        second = c._clock(db)
    assert first == second


def test_envelope_matches_contract(coordinator):
    import jsonschema
    c = coordinator
    projection(c, item()); start(c, 'BUG-01')
    envelope = c.claim_task('BUG-01', 'worker')['taskEnvelope']
    schema = json.loads((Path(__file__).parents[1] / 'schemas/task_envelope.json').read_text())
    jsonschema.validate(envelope, schema)


def test_stale_official_clock_rejects_submit_and_claim(coordinator):
    c = coordinator
    projection(c, item()); start(c, 'BUG-01')
    with c._db() as db:
        c._set_meta(db, 'clock', [1_000_000, time.monotonic() - 500])
    with pytest.raises(NativeCoordinationError, match='stale'):
        c.assert_can_submit('BUG-01')
    assert not c.plan_tasks()['selected']


@pytest.mark.parametrize('path', ['.env', '.agents/SKILL.md', 'exam-info.json', 'node_modules/package/index.js', 'C:/escape'])
def test_worker_cannot_claim_protected_changes(coordinator, path):
    c = coordinator
    projection(c, item()); start(c, 'BUG-01'); c.claim_task('BUG-01', 'worker')
    with pytest.raises(NativeCoordinationError, match='protected|contained'):
        c.record_worker_result('BUG-01', result(changed_files=[path]))


def test_materialization_failure_retains_honest_state(coordinator, monkeypatch):
    c = coordinator
    projection(c, item()); start(c, 'BUG-01')
    original = c._materialize_start_files
    def fail_after_git(key, details, worker_match, validate_only=False):
        if not validate_only:
            raise OSError('local write failed')
        return original(key, details, worker_match, validate_only=True)
    monkeypatch.setattr(c, '_materialize_start_files', fail_after_git)
    with pytest.raises(OSError, match='local write failed'):
        c.claim_task('BUG-01', 'worker')
    state = c.task_details('BUG-01')
    assert state['phase'] == 'NEEDS_ATTENTION'
    assert Path(state['worktreeRoot']).is_dir()
    assert not c.plan_tasks()['selected']


def test_parent_directory_claim_overlaps_child_file(coordinator):
    c = coordinator
    projection(c, item(claimed_paths=['app']), item('PI-01', claimed_paths=['app/core.py']))
    assert len(c.plan_tasks()['selected']) == 1


def complete(c, key, worker):
    start(c, key)
    c.claim_task(key, worker)
    c.record_worker_result(key, result(key))


def test_ready_results_rank_pi_then_nearest_deadline(coordinator):
    c = coordinator
    projection(c, item(), item('CR-01', deadline_epoch=1_001_000), item('PI-01'))
    complete(c, 'BUG-01', 'one')
    complete(c, 'CR-01', 'two')
    complete(c, 'PI-01', 'three')
    plan = c.plan_tasks()
    assert [r['key'] for r in plan['readyToIntegrate']] == ['PI-01', 'CR-01', 'BUG-01']
    assert plan['readyToIntegrate'][0]['taskStatus'] == 'OPEN'
    assert plan['readyToIntegrate'][1]['slackSeconds'] > 0
    assert plan['readyToIntegrate'][1]['deadlineEpoch'] == 1_001_000


def test_ready_task_closed_by_server_is_terminal_with_artifacts_preserved(coordinator):
    c = coordinator
    projection(c, item())
    complete(c, 'BUG-01', 'one')
    before = c.task_details('BUG-01')
    projection(c, item(status='CLOSED'))
    plan = c.plan_tasks()
    assert plan['readyToIntegrate'] == []
    assert plan['skippedReady'][0]['phase'] == 'CLOSED'
    after = c.task_details('BUG-01')
    assert after['phase'] == 'CLOSED'
    assert after['workerResult'] == before['workerResult']
    assert Path(after['worktreeRoot']).is_dir()


def test_ready_expired_task_skipped_but_ready_can_submit_inside_reserve(coordinator):
    c = coordinator
    projection(c, item(deadline_epoch=1_000_200), item('PI-01'))
    complete(c, 'BUG-01', 'one')
    complete(c, 'PI-01', 'two')
    c.register_projection({'status': 'RUNNING', 'server_epoch': 1_000_201,
                           'remaining_seconds': 50, 'items': []})
    plan = c.plan_tasks()
    assert [r['key'] for r in plan['readyToIntegrate']] == ['PI-01']
    assert plan['selected'] == []
    assert c.task_details('BUG-01')['phase'] == 'CLOSED'


def test_paused_ready_omitted_from_dispatch_preserving_diagnostics(coordinator):
    c = coordinator
    projection(c, item()); complete(c, 'BUG-01', 'one')
    projection(c, item(), status='PAUSED')
    plan = c.plan_tasks()
    assert not plan['readyToIntegrate']
    assert plan['readyDiagnostics'][0]['key'] == 'BUG-01'
    assert c.task_details('BUG-01')['phase'] == 'READY_TO_INTEGRATE'


def test_closed_live_integration_keeps_lock_until_explicit_recovery(coordinator):
    c = coordinator
    projection(c, item(), item('PI-01'))
    complete(c, 'BUG-01', 'one'); complete(c, 'PI-01', 'two')
    c.begin_integration('BUG-01')
    projection(c, item(status='CLOSED'), item('PI-01'))
    plan = c.plan_tasks()
    assert plan['integrationOwner'] == 'BUG-01'
    assert plan['integrationBlocked'][0]['key'] == 'BUG-01'
    assert plan['readyToIntegrate'] == []
    assert c.task_details('BUG-01')['phase'] == 'INTEGRATING'
    with pytest.raises(NativeCoordinationError, match='lock'):
        c.begin_integration('PI-01')
    c.end_integration('BUG-01', success=False)
    assert [r['key'] for r in c.plan_tasks()['readyToIntegrate']] == ['PI-01']


def test_stale_clock_does_not_terminalize_ready_result(coordinator):
    c = coordinator
    projection(c, item(deadline_epoch=1_000_200)); complete(c, 'BUG-01', 'one')
    with c._db() as db:
        c._set_meta(db, 'clock', [1_000_500, time.monotonic() - 500])
    plan = c.plan_tasks()
    assert not plan['readyToIntegrate']
    assert c.task_details('BUG-01')['phase'] == 'READY_TO_INTEGRATE'


def test_overdue_worker_retains_slot_until_confirmed_native_stop(coordinator):
    c = coordinator
    projection(c, item(), item('TASK-01'))
    start(c, 'BUG-01'); c.claim_task('BUG-01', 'worker', estimated_seconds=10)
    c.register_projection({'status': 'RUNNING', 'server_epoch': 1_000_011,
                           'remaining_seconds': 2389, 'items': []})
    plan = c.plan_tasks()
    assert plan['overdueWorkers'][0]['workerId'] == 'worker'
    assert 'budget exhausted' in plan['overdueWorkers'][0]['reasons'][0]
    assert plan['active'][0]['key'] == 'BUG-01'
    assert plan['availableSlots'] == 1
    assert not plan['selected']
    with pytest.raises(NativeCoordinationError, match='stopped'):
        c.reconcile_worker('BUG-01', confirmed_stopped=False, reason='budget exceeded', requeue=True)


def test_overdue_budget_excludes_official_pause_duration(coordinator):
    c = coordinator
    projection(c, item()); start(c, 'BUG-01'); c.claim_task('BUG-01', 'worker', estimated_seconds=10)
    c.register_projection({'status': 'PAUSED', 'server_epoch': 1_000_005,
                           'remaining_seconds': 2395, 'items': []})
    assert not c.plan_tasks()['overdueWorkers']
    c.register_projection({'status': 'RUNNING', 'server_epoch': 1_000_500,
                           'remaining_seconds': 2395, 'items': []})
    assert not c.plan_tasks()['overdueWorkers']
    assert c.task_details('BUG-01')['data']['lease']['budget_seconds'] == 10
