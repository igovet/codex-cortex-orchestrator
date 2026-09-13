"""Current advisory depth reaches native callers and recovery without gating work."""
import json

import pytest
from jsonschema import validate

from cortex_runtime.contracts import BY_NAME
from cortex_runtime.governance import governance_snapshot
from cortex_runtime.hook_storage import HookStorage
from cortex_runtime.hooks import restoration
from cortex_runtime.store import Store


@pytest.fixture
def bound(tmp_path):
    store = Store(tmp_path / '.codex/cortex', project_root=tmp_path)
    result = store.call('create_task', {'project_root': str(tmp_path), 'request_key': 'create'},
                        'parent', original_request='A bounded product change')
    return store, tmp_path, result


def choose(store, mode, key=None, **extra):
    return store.call('set_governance', dict(mode=mode, rationale='Risk warrants this depth.',
                                           request_key=key or mode, **extra), 'parent')


def test_latest_depth_is_public_task_wide_and_survives_replay_restart(bound):
    store, root, created = bound
    assert created['governance']['status'] == 'unset'
    validate(created, BY_NAME['create_task']['outputSchema'])
    first = choose(store, 'minimal')
    latest = choose(store, 'full')
    expected = dict(status='selected', mode='full', governance_id=latest['governance_id'],
                    report_id=latest['report_id'])
    assert latest['governance'] == expected
    replay = choose(store, 'minimal')
    assert replay['replayed'] is True
    assert replay['governance_id'] == first['governance_id']
    assert replay['governance'] == expected
    restarted = Store(store.directory, project_root=root)
    child = restarted.call('create_draft', {'template': 'general'}, 'child', 'parent')
    assert child['governance'] == expected
    validate(child, BY_NAME['create_draft']['outputSchema'])
    other = restarted.call('create_task', {'project_root': str(root), 'request_key': 'other'},
                           'other', original_request='Unrelated work')
    assert other['governance']['status'] == 'unset'
    listing = restarted.call('list_reports', {}, 'parent')
    assert listing['governance'] == expected
    validate(listing, BY_NAME['list_reports']['outputSchema'])


def test_recovery_changes_with_depth_and_contains_no_rationale_body(bound):
    store, root, _ = bound
    hooks = HookStorage(store)
    context = hooks.context('parent', str(root))
    before = hooks.snapshot(context)
    selected = store.call('set_governance', dict(mode='full', rationale='DO_NOT_INJECT_PRIVATE_RATIONALE',
                                               request_key='full'), 'parent')
    after = hooks.snapshot(context)
    assert after['state_key'] != before['state_key']
    text = restoration(after)
    assert 'Current advisory governance: full' in text
    assert selected['report_id'] in text
    assert 'DO_NOT_INJECT_PRIVATE_RATIONALE' not in text
    choose(store, 'light')
    assert hooks.snapshot(context)['governance']['mode'] == 'light'


def test_normal_state_is_separate_from_depth_and_resumes_same_choice(bound):
    store, root, _ = bound
    choose(store, 'full', key='pause', state='normal')
    assert HookStorage(store).context('parent', str(root)) is None
    read = store.call('list_reports', {}, 'parent')
    assert read['governance']['mode'] == 'full'
    assert read['binding']['state'] == 'normal'
    resumed = choose(store, 'full', key='resume', state='cortex')
    assert resumed['binding']['state'] == 'cortex'
    assert resumed['governance']['mode'] == 'full'
    assert HookStorage(store).context('parent', str(root)) is not None


def test_unavailable_advisory_projection_does_not_block_public_work(bound):
    store, _, _ = bound
    with store.connection() as db:
        db.execute('ALTER TABLE governance RENAME TO unavailable_governance')
    draft = store.call('create_draft', {'template': 'general'}, 'parent')
    assert draft['draft_id']
    assert draft['governance'] == dict(status='unavailable', mode=None, governance_id=None, report_id=None)
    validate(draft, BY_NAME['create_draft']['outputSchema'])


@pytest.mark.parametrize('row', [('g_0123456789ab', 'unexpected', 'r_0123456789ab'),
                                 ('private text', 'full', 'r_0123456789ab')])
def test_malformed_projection_is_value_free(row):
    class DB:
        def execute(self, *_):
            return self
        def fetchone(self):
            return row
    result = governance_snapshot(DB(), 'task')
    assert result['status'] == 'unavailable'
    assert 'private' not in json.dumps(result)
