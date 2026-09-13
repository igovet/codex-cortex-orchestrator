"""Sanitized behavioral fixture; not evidence of real Codex behavior or production."""
from copy import deepcopy
import pytest
from plugins.cortex.scripts.cortex_runtime.incident_quality import evaluate_trace
from plugins.cortex.scripts.cortex_runtime.capability_policy import advisory_replanning_metadata


class Incident:
    def __init__(self):
        self.identity = {'git': 'abc', 'image': 'sha256:def', 'module': 'v7'}
        self.receipts = {'base': {'epoch': 0}, 'base:attempt:2': {'epoch': 2}}
        self.epoch = 2
        self.active_receipt = 'base'
        self.admission = 'CANARY_ONLY'
        self.activation_present = False
        self.key_type = 'string'
        self.calls = []
        self.ledger = {}
        self.mutations = 0

    def snapshot(self):
        errors = []
        if self.receipts.get(self.active_receipt, {}).get('epoch') != self.epoch:
            errors.append('membership')
        if not self.activation_present:
            errors.append('activation')
        if self.key_type != 'stream':
            errors.append('key_type')
        return {'identity': deepcopy(self.identity), 'receipts': deepcopy(self.receipts),
                'epoch': self.epoch, 'admission': self.admission, 'errors': errors}

    def repair(self):
        # One cohesive change covers every mismatch discovered by snapshot.
        self.active_receipt = 'base:attempt:2'
        self.activation_present = True
        self.key_type = 'stream'

    def activate(self):
        if self.snapshot()['errors']:
            return False
        self.admission = 'normal'
        return True

    def perform(self, key, payload, identity, operation='open'):
        if identity != self.identity:
            return 'tamper'
        if key in self.ledger:
            return 'replay' if self.ledger[key] == payload else 'conflict'
        if self.snapshot()['errors']:
            return 'invalid_contract'
        if operation == 'open' and self.admission != 'normal':
            return 'denied'
        self.ledger[key] = payload
        self.mutations += 1
        self.calls.append(operation)
        return 'executed'

    def rollback(self):
        self.admission = 'DENY'


def test_one_snapshot_and_rehearsal_expose_full_path_before_rollout():
    x = Incident()
    before = deepcopy(x.__dict__)
    assert x.snapshot()['errors'] == ['membership', 'activation', 'key_type']
    assert x.__dict__ == before  # snapshot is read-only
    assert not x.activate()
    assert x.perform('a', 1, x.identity) == 'invalid_contract'
    assert x.mutations == 0 and not x.calls
    x.repair()
    assert x.perform('a', 1, x.identity) == 'denied'  # canary is not normal
    assert x.activate()
    assert x.perform('a', 1, x.identity) == 'executed'
    assert x.perform('a', 1, x.identity) == 'replay'
    assert x.perform('a', 2, x.identity) == 'conflict'
    assert x.perform('b', 1, {'git': 'abc', 'image': 'v7', 'module': 'v7'}) == 'tamper'
    assert x.calls == ['open'] and x.mutations == 1
    x.rollback()
    assert x.perform('b', 1, x.identity) == 'denied'
    assert x.perform('reduce', 1, x.identity, 'reduce') == 'executed'
    assert x.perform('close', 1, x.identity, 'close') == 'executed'
    assert x.calls == ['open', 'reduce', 'close']


@pytest.mark.parametrize('defect', ['schema', 'activation', 'key_type', 'epoch'])
def test_each_downstream_defect_prevents_provider_and_mutation(defect):
    x = Incident()
    x.repair()
    assert x.activate()
    if defect == 'schema':
        x.receipts[x.active_receipt] = {}
    elif defect == 'activation':
        x.activation_present = False
    elif defect == 'key_type':
        x.key_type = 'hash'
    else:
        x.epoch = 0
    assert x.perform('a', 1, x.identity) == 'invalid_contract'
    assert not x.calls and x.mutations == 0


def review(**extra):
    return dict(kind='review', artifact_revision='a', acceptance_boundary='user',
                check_identity='integration', **extra)


def test_observed_process_metrics_and_positive_trace():
    trace = [dict(kind='agent', owner='owner', role='incident_owner'),
             dict(kind='agent', owner='verifier', role='verifier'),
             dict(kind='patch', full_path_covered=True), review(),
             dict(kind='deploy', rehearsal_passed=True),
             dict(kind='activation', rehearsal_passed=True)]
    result = evaluate_trace(trace)
    assert result['verified'] and result['signals'] == []
    assert result['metrics']['incident_owners'] == 1
    assert result['metrics']['rollouts'] == 1
    assert result['metrics']['implementations'] == 1
    assert result['advisory_only'] is True


def test_duplicate_review_needs_delta_and_one_unchanged_message_is_failure():
    assert evaluate_trace([review(), review()])['signals'] == ['duplicate_verification_without_delta']
    assert evaluate_trace([review(), review(new_evidence=True)])['signals'] == []
    assert evaluate_trace([dict(kind='wait_message', state_changed=False)])['signals'] == ['unchanged_wait_message']


def test_rebaseline_must_follow_two_different_unknown_failures_before_next_deploy():
    failures = [dict(kind='live_failure', previously_unknown=True, failure_class=c)
                for c in ('schema', 'activation')]
    deploy = dict(kind='deploy', rehearsal_passed=True)
    reset = dict(kind='rebaseline', full_path_covered=True, snapshot_refreshed=True)
    signal = 'repeated_live_failure_without_rebaseline'
    assert signal in evaluate_trace([reset, *failures, deploy])['signals']
    assert signal not in evaluate_trace([*failures, reset, deploy])['signals']


def test_quality_signals_never_gate_metadata_or_accept_user_claims_as_proof():
    rows = [dict(kind='patch', full_path_covered=False, definitive_cause_claim=True,
                 confidence='isolated_root_cause'),
            dict(kind='production_test', rehearsal_passed=False),
            dict(kind='eta', critical_path_complete=False),
            dict(kind='agent', owner='new', timeout_only=True),
            dict(kind='pipeline_edition', material_delta=False)]
    result = advisory_replanning_metadata(incident_trace=rows, incident_decision={'confidence': 'hypothesis'})
    assert len(result['incident_quality']['signals']) == 6
    assert result['automatic_routing'] is False and result['acceptance_gate'] is False
    assert 'accepted' not in result['incident_quality']


def test_invalid_and_unbounded_diagnostics_do_not_raise_or_report_verified():
    assert not evaluate_trace([None])['verified']
    assert not evaluate_trace([{'kind': 'wait'}] * 1025)['verified']
    assert not evaluate_trace([dict(kind='review', artifact_revision=[])])['verified']
    def broken():
        raise RuntimeError('private failure')
        yield
    result = evaluate_trace(broken())
    assert not result['verified'] and 'private' not in str(result)


def test_offline_evaluation_requires_user_outcome_and_flags_process_failures():
    from scripts.incident_evaluation import qualify
    trace = [dict(kind='agent', owner='owner', role='incident_owner'),
             dict(kind='agent', owner='reviewer', role='verifier'),
             dict(kind='patch', full_path_covered=True), review(),
             dict(kind='deploy', rehearsal_passed=True)]
    assert qualify(trace)['evaluation'] == 'fail'
    outcome = dict(kind='outcome', boundary='user', completed=True,
                   artifact_reconciled=True, deployed_revision_verified=True)
    assert qualify([*trace, outcome])['evaluation'] == 'pass'
    assert qualify([*trace, outcome, dict(kind='wait_message')])['evaluation'] == 'fail'
    for boundary in ('ci', 'deployment', 'DENY'):
        assert qualify([*trace, {**outcome, 'boundary': boundary}])['evaluation'] == 'fail'
