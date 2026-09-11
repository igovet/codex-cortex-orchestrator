import hashlib
import json
import runpy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
EVAL = runpy.run_path(str(ROOT / 'scripts/cortex_eval.py'))


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def measured(**changes):
    value = dict(input_tokens=10, cached_input_tokens=4, cache_write_input_tokens=1,
                 output_tokens=3, reasoning_output_tokens=1, total_tokens=13,
                 wall_seconds=2.0, protocol_pass=True, claimed_complete=True,
                 completion_claim_count=1, supported_claim_count=1,
                 discriminating_check_before_mutation=True, fanout_count=1,
                 unsafe_fanout_conflict_count=0, blindness=True,
                 protected_content_preserved=True, resource_conflicts=0,
                 root_cause_success=True, failed_fix_count=0,
                 duplicate_dispatch_report_count=0,
                 tool_count=1, read_count=1, write_count=1, dispatch_count=1,
                 report_count=1, unavailable_reason=None, stop_reason=None,
                 invariant_violation=None)
    value.update(changes)
    return value


def test_contract_freezes_three_held_out_families_and_blind_join():
    contract = EVAL['phase01_contract']()
    assert contract['suite_version'] == 'phase01-v1'
    assert [row['scenario_id'] for row in contract['scenario_families']] == ['F-01', 'D-01', 'R-01']
    assert contract['arms'] == ['baseline', 'candidate']
    assert contract['paired_repeats'] == 3
    assert 'arm' in contract['blind_scoring']['excluded_from_scorer_inputs']
    assert 'candidate_payload_sha256' in contract['blind_scoring']['excluded_from_scorer_inputs']
    assert 'currency' in contract['cost_accounting']


def test_workload_manifest_is_hashed_and_scenario_specific():
    manifest = EVAL['phase01_workloads']()
    assert manifest['manifest_version'] == 'phase01-workloads-v1'
    assert manifest['source_revision'] == 8
    assert set(manifest['payload_identities']) == {'baseline', 'candidate'}
    assert manifest['payload_identities']['baseline'] == 'cc786ae2fbd04cf1e9c29cfb34cf721de6ad6b8663f2d05f809baf2bee158698'
    assert manifest['payload_identities']['candidate'] == '6d67370022e398307af4e7fe4a06cf85c47ca2c4aaa42401baa3377909df65bf'
    assert manifest['payload_identities']['baseline'] != manifest['payload_identities']['candidate']
    for scenario in manifest['scenarios']:
        assert scenario['prompt'].startswith('$cortex:orchestrator ')
        assert scenario['expected_user_boundary_requirements']
        assert scenario['independent_checks']
        assert scenario['protected_paths'] == ['USER-NOTE.txt']
        assert set(scenario['fixture_sha256_by_repeat']) == {'1', '2', '3'}


def test_workload_revision_changes_only_allowed_identity_fields():
    manifest = EVAL['phase01_workloads']()
    historical = json.loads(json.dumps(manifest))
    historical['source_revision'] = 6
    historical['payload_identities']['candidate'] = '3a5fcd64460d4c274d142b6dd793dd77c0b5c2c0c8de1feb2583a28743b214c9'
    frozen_control_sha256 = '6448bbe1d060223a07324125cc917cbeb4fd13200447ae2e06107fc02ccaa3d0'
    canonical = json.dumps(historical, sort_keys=True, separators=(',', ':')).encode()
    assert hashlib.sha256(canonical).hexdigest() == frozen_control_sha256


def test_prepare_can_verify_both_frozen_payload_identities(tmp_path):
    identities = EVAL['phase01_workloads']()['payload_identities']
    trial = tmp_path / 'trial'
    result = EVAL['phase01_prepare'](
        'D-01', trial, 'candidate', 2, identities['candidate'], digest('settings'),
        baseline_payload_sha256=identities['baseline'],
        candidate_payload_sha256=identities['candidate'])
    assert result['frozen_baseline_payload_sha256'] == identities['baseline']
    assert result['frozen_candidate_payload_sha256'] == identities['candidate']
    with pytest.raises(ValueError, match='frozen phase-0 identity'):
        EVAL['phase01_prepare'](
            'D-01', tmp_path / 'wrong', 'candidate', 2, identities['candidate'],
            digest('settings'), baseline_payload_sha256=digest('wrong'))


def test_phase01_prepare_is_non_overwriting_and_records_blind_identity(tmp_path):
    trial = tmp_path / 'trial'
    result = EVAL['phase01_prepare']('F-01', trial, 'baseline', 1,
                                     digest('baseline'), digest('settings'))
    assert result['split'] == 'holdout'
    assert len(result['blind_join_key']) == 64
    assert (trial / 'phase01-trial.json').is_file()
    with pytest.raises(FileExistsError):
        EVAL['phase01_prepare']('F-01', trial, 'baseline', 1,
                                digest('baseline'), digest('settings'))


def test_phase01_record_keeps_nulls_and_blinds_score_packet(tmp_path):
    trial = tmp_path / 'trial'
    prepared = EVAL['phase01_prepare']('F-01', trial, 'baseline', 1,
                                        digest('baseline'), digest('settings'))
    result = EVAL['phase01_record'](trial, measured(
        payload_sha256=prepared['baseline_payload_sha256'],
        fixture_sha256=prepared['fixture_sha256'],
        starting_tree_sha256=prepared['starting_tree_sha256'],
        prompt_sha256=prepared['prompt_sha256'],
        model_settings_sha256=prepared['model_settings_sha256'],
        blind_join_key=prepared['blind_join_key'], host=prepared['host']))
    assert result['status'] == 'measured'
    assert 'arm' not in result['blind_score']
    assert result['candidate_payload_sha256'] is None
    assert result['unavailable_reason'] is None


def test_phase01_metrics_use_only_the_family_scorecard_fields():
    assert EVAL['_phase01_metric'](
        {'completion_claim_count': 2, 'supported_claim_count': 1},
        {'scenario_id': 'F-01'}) == 0.5
    assert EVAL['_phase01_metric'](
        {'discriminating_check_before_mutation': True},
        {'scenario_id': 'D-01'}) == 1.0
    assert EVAL['_phase01_metric'](
        {'fanout_count': 2, 'unsafe_fanout_conflict_count': 1},
        {'scenario_id': 'R-01'}) == 0.5


def complete_rows(overrides=None):
    overrides = overrides or {}
    rows = []
    payloads = {'baseline': digest('baseline-payload'), 'candidate': digest('candidate-payload')}
    for scenario in ('F-01', 'D-01', 'R-01'):
        for attempt in (1, 2, 3):
            for arm in ('baseline', 'candidate'):
                row = measured(
                suite_version='phase01-v1', scenario_id=scenario,
                    family={'F-01': 'false-visible-requirement', 'D-01': 'misleading-symptom',
                            'R-01': 'shared-surface'}[scenario], split='holdout', attempt=attempt,
                    arm=arm, status='measured', payload_sha256=payloads[arm],
                    model_settings_sha256=digest('settings'), fixture_sha256=digest('fixture'),
                    starting_tree_sha256=digest('tree'), prompt_sha256=digest('prompt'), host='cli',
                    blind_join_key=digest(f'{scenario}:{attempt}'),
                    baseline_payload_sha256=payloads['baseline'] if arm == 'baseline' else None,
                    candidate_payload_sha256=payloads['candidate'] if arm == 'candidate' else None,
                    critical_omission_count=1 if arm == 'baseline' else 0,
                    completion_claim_count=2, supported_claim_count=1 if arm == 'baseline' else 2,
                    discriminating_check_before_mutation=(arm == 'candidate'),
                    root_cause_success=(arm == 'candidate'),
                    fanout_count=2, unsafe_fanout_conflict_count=1 if arm == 'baseline' else 0,
                )
                row.update(overrides.get((scenario, attempt, arm), {}))
                rows.append(row)
    return rows


def test_phase01_guardrail_regressions_block_promotion():
    rows = complete_rows(
        {('F-01', 1, 'candidate'): {'critical_omission_count': 3},
         ('D-01', 1, 'candidate'): {'failed_fix_count': 1},
         ('R-01', 1, 'candidate'): {'resource_conflicts': 1}})
    result = EVAL['phase01_compare'](rows)
    assert result['decision'] == 'revise_or_replicate'
    assert 'critical_omission_count' in result['families']['false-visible-requirement']['guardrail_regressions']
    assert 'failed_fix_count' in result['families']['misleading-symptom']['guardrail_regressions']
    assert 'resource_conflicts' in result['families']['shared-surface']['guardrail_regressions']


def test_phase01_pair_requires_all_immutable_arm_identity(tmp_path):
    rows = complete_rows()
    rows[0]['payload_sha256'] = None
    result = EVAL['phase01_compare'](rows)
    pair = next(row for row in result['pairs'] if row['scenario_id'] == 'F-01' and row['attempt'] == 1)
    assert pair['status'] == 'unverified'
    assert pair['stop_reason'] == 'arm_identity_missing'


def test_phase01_record_marks_missing_arm_identity_stopped(tmp_path):
    trial = tmp_path / 'trial'
    EVAL['phase01_prepare']('R-01', trial, 'candidate', 1,
                             digest('candidate'), digest('settings'))
    result = EVAL['phase01_record'](trial, measured())
    assert result['status'] == 'stopped'
    assert result['stop_reason'] == 'arm_identity_missing'
    assert result['payload_sha256'] is None


def test_phase01_record_rejects_unexpected_payload_as_measured(tmp_path):
    trial = tmp_path / 'trial'
    prepared = EVAL['phase01_prepare']('F-01', trial, 'candidate', 1,
                                       None, digest('settings'))
    result = EVAL['phase01_record'](trial, measured(
        payload_sha256=digest('caller-forged-payload'),
        fixture_sha256=prepared['fixture_sha256'],
        starting_tree_sha256=prepared['starting_tree_sha256'],
        prompt_sha256=prepared['prompt_sha256'],
        model_settings_sha256=prepared['model_settings_sha256'],
        blind_join_key=prepared['blind_join_key'], host=prepared['host']))
    assert result['status'] == 'stopped'
    assert result['stop_reason'] == 'expected_payload_identity_missing'
    assert result['candidate_payload_sha256'] is None


def test_phase01_compare_stops_forged_measured_protocol_failure():
    rows = complete_rows({('F-01', 1, 'candidate'): {'protocol_pass': False}})
    result = EVAL['phase01_compare'](rows)
    pair = next(row for row in result['pairs'] if row['scenario_id'] == 'F-01' and row['attempt'] == 1)
    assert pair['status'] == 'unverified'
    assert pair['stop_reason'] == 'protocol_or_invariant_failure'
    assert result['decision'] != 'promote_candidate'


def test_phase01_compare_stops_forged_measured_stop_reason():
    rows = complete_rows({('D-01', 1, 'candidate'): {'stop_reason': 'audit_failure'}})
    result = EVAL['phase01_compare'](rows)
    pair = next(row for row in result['pairs'] if row['scenario_id'] == 'D-01' and row['attempt'] == 1)
    assert pair['status'] == 'unverified'
    assert pair['stop_reason'] == 'audit_failure'
    assert result['decision'] != 'promote_candidate'


def test_phase01_contract_rejects_manifest_drift(monkeypatch, tmp_path):
    manifest = tmp_path / 'contract.json'
    value = EVAL['phase01_contract']()
    value['paired_repeats'] = 4
    manifest.write_text(json.dumps(value))
    monkeypatch.setitem(EVAL['phase01_contract'].__globals__, 'PHASE01_CONTRACT_PATH', manifest)
    with pytest.raises(ValueError, match='integrity mismatch'):
        EVAL['phase01_contract']()


def test_phase01_compare_has_18_explicit_unrun_cells_and_never_imputes_cost():
    result = EVAL['phase01_compare']([])
    assert len(result['runs']) == 18
    assert all(row['status'] == 'unrun' for row in result['runs'])
    assert result['decision'] == 'unverified'
    assert all(value['baseline_metric'] is None for value in result['families'].values())


def test_phase01_record_rejects_nonblind_or_identity_mismatch(tmp_path):
    trial = tmp_path / 'trial'
    prepared = EVAL['phase01_prepare']('D-01', trial, 'candidate', 2,
                                        digest('candidate'), digest('settings'))
    with pytest.raises(ValueError, match='scorer'):
        EVAL['phase01_record'](trial, measured(blindness=False))
    with pytest.raises(ValueError, match='identity mismatch'):
        EVAL['phase01_record'](trial, measured(blindness=True,
            fixture_sha256=digest('wrong')))
