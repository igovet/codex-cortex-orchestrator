"""Bounded advisory incident observations. Never used to authorize execution."""
from itertools import islice

SIGNALS = (
    'premature_root_cause_claim', 'unchecked_downstream_path',
    'production_first_dynamic_test', 'repeated_live_failure_without_rebaseline',
    'duplicate_verification_without_delta', 'pipeline_churn',
    'unjustified_agent_expansion', 'eta_without_complete_critical_path',
    'unchanged_wait_message',
)


def evaluate_trace(events):
    """Evaluate normalized observations, not free text or inferred model intent.

    At most 1024 observations are inspected. Incomplete/invalid input is unverified.
    Callers retain original evidence separately; this output contains no raw text.
    """
    result = {'advisory_only': True, 'verified': False, 'signals': [], 'metrics': {}}
    try:
        rows = list(islice(iter(events), 1025))
        if len(rows) > 1024 or any(type(row) is not dict for row in rows):
            return result
        found, identities, failure_classes, owners = set(), set(), set(), set()
        metrics = dict(agents=0, pipeline_editions=0, reviews=0, rollouts=0,
                       implementations=0, wait_messages=0, rebaselines=0)
        for row in rows:
            kind = row.get('kind')
            if type(kind) is not str or len(kind) > 64:
                return result
            if row.get('definitive_cause_claim') is True and row.get('confidence') != 'end_to_end_root_cause':
                found.add('premature_root_cause_claim')
            if kind == 'patch':
                metrics['implementations'] += 1
                if row.get('full_path_covered') is not True:
                    found.add('unchecked_downstream_path')
            if kind in ('deploy', 'production_test', 'activation'):
                if kind == 'deploy':
                    metrics['rollouts'] += 1
                if row.get('rehearsal_passed') is not True:
                    found.add('production_first_dynamic_test')
                if len(failure_classes) >= 2:
                    found.add('repeated_live_failure_without_rebaseline')
            if kind == 'live_failure' and row.get('previously_unknown') is True:
                category = row.get('failure_class')
                if type(category) is not str or not category or len(category) > 128:
                    return result
                failure_classes.add(category)
            if kind == 'rebaseline':
                if row.get('full_path_covered') is True and row.get('snapshot_refreshed') is True:
                    failure_classes.clear()
                    metrics['rebaselines'] += 1
            if kind in ('review', 'verification'):
                identity = tuple(row.get(k) for k in
                                 ('artifact_revision', 'acceptance_boundary', 'check_identity'))
                if any(type(v) is not str or not v or len(v) > 512 for v in identity):
                    return result
                if identity in identities and not (row.get('new_evidence') is True or row.get('rerun_reason_present') is True):
                    found.add('duplicate_verification_without_delta')
                identities.add(identity)
                metrics['reviews'] += kind == 'review'
            if kind == 'pipeline_edition':
                metrics['pipeline_editions'] += 1
                if row.get('material_delta') is False:
                    found.add('pipeline_churn')
            if kind == 'agent':
                metrics['agents'] += 1
                owner = row.get('owner')
                if type(owner) is not str or not owner or len(owner) > 128:
                    return result
                if row.get('role') == 'incident_owner':
                    owners.add(owner)
                if row.get('timeout_only') is True or (metrics['agents'] > 2 and row.get('independent_gap') is not True):
                    found.add('unjustified_agent_expansion')
                if metrics['agents'] >= 4 and row.get('decomposition_reconsidered') is not True:
                    found.add('unjustified_agent_expansion')
            if kind == 'eta' and row.get('critical_path_complete') is not True:
                found.add('eta_without_complete_critical_path')
            if kind == 'wait_message':
                metrics['wait_messages'] += 1
                if row.get('state_changed') is not True:
                    found.add('unchanged_wait_message')
        if metrics['pipeline_editions'] > 5:
            found.add('pipeline_churn')
        if len(owners) > 1:
            found.add('unjustified_agent_expansion')
        metrics['incident_owners'] = len(owners)
        result.update(verified=True, signals=sorted(found), metrics=metrics)
    except Exception:
        # Diagnostic failure must not change the caller's execution outcome.
        pass
    return result
