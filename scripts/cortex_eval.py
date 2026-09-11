#!/usr/bin/env python3
"""Offline fixtures and outcome scoring; never launches or steers a Codex host.

Keep controls outside the worker project. Host transport, acceptance and evidence
review remain operator-owned. Missing measurements never count as zero or success.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CONFIGURATIONS = ('baseline', 'evidence', 'hypotheses', 'reuse', 'routing', 'combined')
BASELINE = 'cc786ae2fbd04cf1e9c29cfb34cf721de6ad6b8663f2d05f809baf2bee158698'
PILOT_CONFIGURATIONS = ('baseline', 'compact_no_hooks', 'full_hooks')
PILOT_CASES = ('stable-unique', 'retry-dedup', 'cancel-sort', 'resume-pagination')
PILOT_BASELINE_COMMIT = '1a0988bdee5a0fe943e74df1746d2ae8ad1b161b'
PILOT_BASELINE_PAYLOAD = 'fd0b4e63ad8eea97'
PHASE01_CONTRACT_PATH = ROOT / 'tests/fixtures/phase01_eval/contract-v1.json'
PHASE01_WORKLOAD_PATH = ROOT / 'tests/fixtures/phase01_eval/workloads-v1.json'
PHASE01_SUITE_VERSION = 'phase01-v1'
PHASE01_CONTRACT_SHA256 = 'b24115dd5f4cf1edfdb24858c07ad871b1c370c30fc79f1c1b7e2ba7f02fa7b5'
PHASE01_WORKLOAD_SHA256 = 'e479f54c682a55146be3cdc9433025ea51dfec17cd8101b58aa4da716058941f'
PHASE01_ARMS = ('baseline', 'candidate')
PHASE01_REPEATS = (1, 2, 3)
PHASE01_SCENARIOS = (
    dict(scenario_id='F-01', family='false-visible-requirement',
         primary_metric='unsupported_completion_rate', direction='lower',
         guardrails=('critical_user_boundary_regressions', 'protocol_pass',
                      'protected_content_preserved')),
    dict(scenario_id='D-01', family='misleading-symptom',
         primary_metric='discriminating_check_rate', direction='higher',
         guardrails=('root_cause_success', 'failed_fix_count', 'protocol_pass')),
    dict(scenario_id='R-01', family='shared-surface',
         primary_metric='unsafe_fanout_conflict_rate', direction='lower',
         guardrails=('duplicate_dispatch_report_count', 'resource_conflicts',
                      'protocol_pass')),
)
PHASE01_TOKEN_FIELDS = ('input_tokens', 'cached_input_tokens',
                        'cache_write_input_tokens', 'output_tokens',
                        'reasoning_output_tokens', 'total_tokens')
PHASE01_COUNT_FIELDS = ('tool_count', 'read_count', 'write_count',
                        'dispatch_count', 'report_count')
PHASE01_SCORE_FIELDS = (
    'requirement_count', 'satisfied_count', 'critical_omission_count',
    'completion_claim_count', 'supported_claim_count',
    'explicitly_unrun_check_count', 'facts_hypotheses_separated',
    'discriminating_check_before_mutation', 'root_cause_success',
    'failed_fix_count', 'fanout_count', 'unsafe_fanout_conflict_count',
    'duplicate_dispatch_report_count', 'protocol_pass', 'claimed_complete',
    'protected_content_preserved', 'resource_conflicts',
)
PHASE01_METRIC_FIELDS = {
    'F-01': ('completion_claim_count', 'supported_claim_count'),
    'D-01': ('discriminating_check_before_mutation',),
    'R-01': ('fanout_count', 'unsafe_fanout_conflict_count'),
}
PHASE01_GUARDRAILS = {
    'F-01': ('critical_omission_count', 'completion_claim_count',
             'supported_claim_count', 'protected_content_preserved'),
    'D-01': ('root_cause_success', 'failed_fix_count'),
    'R-01': ('unsafe_fanout_conflict_count', 'duplicate_dispatch_report_count',
             'resource_conflicts'),
}
PHASE01_DECISION_RULE = (
    'First reject stopped or protocol-failing arms. Otherwise recommend promotion only when the '
    'candidate primary metric improves in all three families and at least two of three paired repeats '
    'per family, with no critical requirement regression, false-completion increase, unsafe-fanout/conflict '
    'increase, duplicate-report increase, protected-content failure or resource-conflict increase. If the target is unchanged and median total tokens or wall time '
    'rises above 20 percent, recommend revision or replication. This is a coordinator-owned recommendation, '
    'not automatic acceptance.'
)


def case(name, split, family, brief, checks, source='def solve(*args):\n    raise NotImplementedError\n', steering=None, initial_checks=None):
    return dict(name=name, split=split, family=family, brief=brief, checks=checks,
                source=source, steering=steering, initial_checks=initial_checks or checks)


# Expected outputs are independent of any candidate implementation. No model judge.
CASES = [
    case('stable-unique', 'tuning', 'simple',
         'Implement solve(values): return unique strings in first-occurrence order, case-sensitive; do not mutate input.',
         [([['b', 'a', 'b', 'A']], ['b', 'a', 'A']), ([[]], [])]),
    case('intervals', 'holdout', 'simple',
         'Implement solve(intervals): merge overlapping or touching closed integer intervals, sorted by start. Reject reversed intervals with ValueError. Preserve input.',
         [([[[3, 5], [1, 3], [8, 9]]], [[1, 5], [8, 9]]), ([[]], []), ([[[4, 2]]], {'error': 'ValueError'})]),
    case('retry-dedup', 'tuning', 'debugging',
         'Fix solve(events), where events are [id, amount]. Count each id once; identical retry is ignored, conflicting amount for the same id raises ValueError. Amounts may be negative or zero. Preserve input.',
         [([[['x', 5], ['x', 5], ['y', -2]]], 3), ([[['x', 0], ['x', 1]]], {'error': 'ValueError'})],
         'def solve(events):\n    return sum(amount for _, amount in events)\n'),
    case('cache-expiry', 'holdout', 'debugging',
         'Fix solve(operations). Operations are [put,key,value,now,ttl] or [get,key,now]. Return get results; missing/expired is null. Expiry is inclusive (now >= expiry). ttl is nonnegative. Overwrite resets expiry. A stored null is a valid value.',
         [([[['put', 'a', 8, 0, 2], ['get', 'a', 2]]], [None]),
          ([[['put', 'a', 8, 0, 2], ['put', 'a', 9, 1, 5], ['get', 'a', 3], ['get', 'a', 6]]], [9, None]),
          ([[['put', 'a', 8, 0, -1]]], {'error': 'ValueError'})],
         'def solve(operations):\n    cache = {}\n    out = []\n    for op in operations:\n        if op[0] == "put": cache[op[1]] = (op[2], op[3] + op[4])\n        else:\n            value, expiry = cache.get(op[1], (None, float("inf")))\n            out.append(value if op[2] <= expiry else None)\n    return out\n'),
    case('money-filter', 'tuning', 'contracts',
         'Implement solve(rows, currency, minimum). Each row is [id,currency,decimal-string]. Validate the WHOLE input, including excluded currencies: unique ids, finite decimal amounts. Raise ValueError on invalid input. Return sorted ids matching currency and amount >= minimum. Use exact decimal arithmetic; preserve input.',
         [([[['a', 'EUR', '0.30000000000000000001'], ['b', 'EUR', '0.3']], 'EUR', '0.30000000000000000001'], ['a']),
          ([[['x', 'USD', 'NaN']], 'EUR', '0'], {'error': 'ValueError'}),
          ([[['x', 'USD', '1'], ['x', 'EUR', '1']], 'EUR', '0'], {'error': 'ValueError'})]),
    case('csv-roundtrip', 'holdout', 'contracts',
         'Implement solve(csv_text): parse CSV columns id,text; return rows as [id,text], preserving Unicode, commas, quotes and embedded newlines. Require that exact header and two fields per row, nonempty unique id; reject malformed quoting with ValueError.',
         [(['id,text\n1,"a,b"\n2,"Привет\nмир"\n'], [['1', 'a,b'], ['2', 'Привет\nмир']]),
          (['id,text\n1,x\n1,y\n'], {'error': 'ValueError'}), (['id,text\n1,"unfinished'], {'error': 'ValueError'})]),
    case('threshold-change', 'tuning', 'steering',
         'Implement solve(values, threshold): return numeric values strictly above threshold, preserving order and input.',
         [([[1, 2, 3, 2], 2], [2, 3, 2]), ([[], 0], [])],
         steering='Change the threshold to inclusive: values equal to it must also be returned. Keep order and input preservation.',
         initial_checks=[([[1, 2, 3, 2], 2], [3]), ([[], 0], [])]),
    case('cancel-sort', 'holdout', 'steering',
         'Implement solve(values): remove duplicate strings and return them sorted; case-sensitive and input unchanged.',
         [([['z', 'a', 'z', 'B']], ['z', 'a', 'B']), ([[]], [])],
         steering='Cancel sorting. Keep first-occurrence order instead; retain deduplication, case sensitivity and input preservation.',
         initial_checks=[([['z', 'a', 'z', 'B']], ['B', 'a', 'z']), ([[]], [])]),
    case('resume-pagination', 'tuning', 'recovery',
         'Implement solve(values, offset, limit): return that slice; reject negative offset or limit with ValueError. Preserve input.',
         [([[1, 2, 3], 1, 2], {'items': [2, 3], 'total': 3}),
          ([[1], -1, 1], {'error': 'ValueError'}), ([[1], 0, 0], {'items': [], 'total': 1})],
         steering='Return an object with items (the same slice) and total (the original count). Keep all prior validation and preservation rules.',
         initial_checks=[([[1, 2, 3], 1, 2], [2, 3]), ([[1], -1, 1], {'error': 'ValueError'})]),
    case('resume-unicode', 'holdout', 'recovery',
         'Implement solve(text): split on whitespace, return nonempty words preserving order and Unicode spelling.',
         [(['Привет\nмир Привет'], ['Привет', 'мир']), ([' A a A '], ['A', 'a']), ([''], [])],
         steering='Also remove exact duplicate words, retaining first occurrence. Keep case sensitivity, whitespace splitting and Unicode preservation.',
         initial_checks=[(['Привет\nмир Привет'], ['Привет', 'мир', 'Привет'])]),
    case('version-order', 'tuning', 'debugging',
         'Fix solve(versions): sort dotted nonnegative integer versions numerically, treating missing components as zero and preserving order for equivalent versions. Reject empty components and non-ASCII digits with ValueError.',
         [([['1.10', '1.2', '1', '1.0']], ['1', '1.0', '1.2', '1.10']),
          ([['1..2']], {'error': 'ValueError'}), ([['١']], {'error': 'ValueError'})],
         'def solve(versions):\n    return sorted(versions)\n'),
    case('dependency-cycle', 'holdout', 'contracts',
         'Implement solve(graph): graph maps node names to prerequisites. Return topological order, choosing the alphabetically first currently ready node at EACH step. Include referenced-only nodes. Reject cycles with ValueError. Preserve input.',
         [([{'a': ['b'], 'c': []}], ['b', 'a', 'c']),
          ([{'a': ['b'], 'b': ['a']}], {'error': 'ValueError'}), ([{}], [])]),
]


def selected(name):
    return next(c for c in CASES if c['name'] == name)


def prepare(name, directory, configuration, attempt):
    """Never overwrite an existing trial. Controls stay beside, not inside, project."""
    c = selected(name)
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    project = directory / 'project'
    project.mkdir()
    (project / 'solution.py').write_text(c['source'])
    (project / 'USER-NOTE.txt').write_text('Protected user content — do not modify.\n')
    (project / 'README.md').write_text('# Fixture\nPython 3 standard library only. Entry point: solution.solve.\n')
    prompt = ('$cortex:orchestrator ' + c['brief'] + '\nUse solution.py, add meaningful tests and document behavior in README.md. '
              'Use Python 3 standard library only. Do not change USER-NOTE.txt.\n')
    (directory / 'prompt.txt').write_text(prompt)
    if c['steering']:
        (directory / 'steering.txt').write_text(c['steering'] + '\n')
    record = dict(case=name, split=c['split'], family=c['family'], configuration=configuration,
                  attempt=attempt, baseline_sha256=BASELINE, status='prepared',
                  fixture_sha256=hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest(),
                  protected_sha256=hashlib.sha256((project / 'USER-NOTE.txt').read_bytes()).hexdigest())
    (directory / 'trial.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


def grade(directory, phase='final'):
    record = json.loads((directory / 'trial.json').read_text())
    c = selected(record['case'])
    checks = c['initial_checks'] if phase == 'initial' else c['checks']
    program = '''import copy, importlib.util, json, sys
spec = importlib.util.spec_from_file_location("candidate", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
results = []
for args, expected in json.load(sys.stdin):
    before = copy.deepcopy(args)
    try: actual = module.solve(*args)
    except Exception as error: actual = {"error": type(error).__name__}
    results.append(actual == expected and args == before)
print(json.dumps(results))
'''
    try:
        result = subprocess.run([sys.executable, '-I', '-B', '-c', program,
                                 str((directory / 'project/solution.py').resolve())],
                                input=json.dumps(checks), text=True, capture_output=True, timeout=10)
        outcomes = json.loads(result.stdout) if result.returncode == 0 else []
        passed = len(outcomes) == len(checks) and all(x is True for x in outcomes)
    except (subprocess.TimeoutExpired, ValueError):
        outcomes, passed = [], False
    protected = directory / 'project/USER-NOTE.txt'
    preserved = protected.is_file() and hashlib.sha256(protected.read_bytes()).hexdigest() == record['protected_sha256']
    return dict(case=c['name'], phase=phase, checks=len(checks), passed_checks=sum(x is True for x in outcomes),
                protected_preserved=preserved, functional_success=passed and preserved)


def compare(records):
    """Conservative descriptive screen; small/incomplete samples cannot prove gain."""
    required = {(c['name'], i) for c in CASES for i in range(1, 4)}
    groups = {}
    for row in records:
        if (row.get('configuration') not in CONFIGURATIONS or type(row.get('attempt')) is not int
                or (row.get('case'), row.get('attempt')) not in required):
            raise ValueError('unknown configuration or trial')
        for field in ('functional_success','protocol_pass','claimed_complete'):
            if row.get(field) is not None and type(row[field]) is not bool:
                raise ValueError('invalid outcome')
        for field in ('tokens', 'seconds'):
            value = row.get(field)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value <= 0):
                raise ValueError('invalid measurement')
        group = groups.setdefault(row['configuration'], {})
        key = (row['case'], row['attempt'])
        if key in group:
            raise ValueError('duplicate trial')
        group[key] = row
    reports = {}
    base = groups.get('baseline', {})
    for name in CONFIGURATIONS[1:]:
        group = groups.get(name, {})
        missing = len(required - group.keys()) + len(required - base.keys())
        if missing:
            reports[name] = dict(status='unverified', missing_trials=missing)
            continue
        needed = ('tokens', 'seconds', 'functional_success', 'protocol_pass', 'claimed_complete', 'repeated_reads', 'payload_sha256', 'host', 'fixture_sha256', 'model_settings_sha256')
        if any(any(row.get(field) is None for field in needed) for row in [*base.values(), *group.values()]):
            reports[name] = dict(status='unverified', reason='missing measurements')
            continue
        if (any(base[k]['fixture_sha256'] != group[k]['fixture_sha256'] or base[k]['host'] != group[k]['host']
                or base[k]['model_settings_sha256'] != group[k]['model_settings_sha256'] for k in required)
                or len({r['payload_sha256'] for r in group.values()}) != 1
                or any(r['payload_sha256'] != BASELINE for r in base.values())):
            reports[name] = dict(status='unverified', reason='incomparable fixtures, hosts, settings or payloads')
            continue
        if any(not row['protocol_pass'] for row in group.values()):
            reports[name] = dict(status='rejected', reason='protocol failure')
            continue
        partitions = {}
        for split in ('tuning', 'holdout'):
            keys = {(c['name'], i) for c in CASES if c['split'] == split and c['family'] != 'simple' for i in range(1, 4)}
            before = sum(not base[k]['functional_success'] for k in keys)
            after = sum(not group[k]['functional_success'] for k in keys)
            tokens = statistics.median(group[k]['tokens'] for k in keys) / statistics.median(base[k]['tokens'] for k in keys)
            seconds = statistics.median(group[k]['seconds'] for k in keys) / statistics.median(base[k]['seconds'] for k in keys)
            partitions[split] = dict(baseline_failures=before, candidate_failures=after,
                                    token_ratio=tokens, duration_ratio=seconds,
                                    threshold_met=((before > 0 and after <= before * .8 and max(tokens, seconds) <= 1.25)
                                                   or (after <= before and max(tokens, seconds) <= .8)))
        regressions = sum(base[k]['functional_success'] and not group[k]['functional_success'] for k in required)
        false_complete = sum(group[k]['claimed_complete'] and not group[k]['functional_success'] for k in required)
        baseline_false = sum(base[k]['claimed_complete'] and not base[k]['functional_success'] for k in required)
        simple = {(c['name'], i) for c in CASES if c['family']=='simple' for i in range(1,4)}
        simple_cost = dict(token_ratio=statistics.median(group[k]['tokens'] for k in simple)/statistics.median(base[k]['tokens'] for k in simple),
                           duration_ratio=statistics.median(group[k]['seconds'] for k in simple)/statistics.median(base[k]['seconds'] for k in simple))
        reports[name] = dict(status='needs_replication' if all(p['threshold_met'] for p in partitions.values()) and not regressions and false_complete <= baseline_false else 'not_demonstrated',
                             partitions=partitions, simple_cost=simple_cost, regressions=regressions, false_completions=false_complete)
    return reports


def record_trial(directory, observations):
    """Merge manually reviewed, metadata-only host measurements with fresh grading."""
    record = json.loads((directory / 'trial.json').read_text())
    allowed = {'tokens', 'seconds', 'protocol_pass', 'claimed_complete', 'repeated_reads',
               'payload_sha256', 'host', 'model_settings_sha256', 'steering_observed', 'resume_observed'}
    if set(observations) != allowed:
        raise ValueError('observation fields must match the documented metadata contract')
    for name in ('tokens', 'seconds', 'repeated_reads'):
        value = observations[name]
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0 or name != 'repeated_reads' and value == 0):
            raise ValueError('invalid measurement')
    for name in ('protocol_pass', 'claimed_complete', 'steering_observed', 'resume_observed'):
        if observations[name] is not None and type(observations[name]) is not bool:
            raise ValueError('invalid observation')
    for name in ('payload_sha256', 'model_settings_sha256'):
        value = observations[name]
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('invalid digest')
    if observations['host'] not in {'cli', 'desktop'}:
        raise ValueError('invalid host')
    c = selected(record['case'])
    eligible = ((not c['steering'] or observations['steering_observed'] is True)
                and (c['family'] != 'recovery' or observations['resume_observed'] is True))
    result = {**record, **grade(directory), **observations, 'status': 'measured' if eligible else 'incomplete'}
    if not eligible: result['functional_success'] = None
    path = directory / 'result.json'
    with path.open('x') as stream: json.dump(result, stream, indent=2)
    return result


def pilot_prepare(name, directory, configuration):
    """Prepare one member of the fixed 3-by-4 pilot without changing the old suite."""
    if name not in PILOT_CASES or configuration not in PILOT_CONFIGURATIONS:
        raise ValueError('unknown pilot case or configuration')
    record=prepare(name,directory,configuration,1)
    record.update(suite='hooks-pilot-v1',baseline_commit=PILOT_BASELINE_COMMIT,
                  baseline_sha256=PILOT_BASELINE_PAYLOAD)
    (directory/'trial.json').write_text(json.dumps(record,indent=2)+'\n')
    return record


def pilot_adopt(directory, configuration):
    """Add a pilot overlay to an identical historical fixture without rewriting it."""
    if configuration not in PILOT_CONFIGURATIONS:raise ValueError('unknown pilot configuration')
    record=json.loads((directory/'trial.json').read_text())
    if record.get('case') not in PILOT_CASES or record.get('attempt')!=1:
        raise ValueError('historical fixture is not a pilot member')
    expected=hashlib.sha256(json.dumps(selected(record['case']),sort_keys=True).encode()).hexdigest()
    if record.get('fixture_sha256')!=expected:raise ValueError('historical fixture differs from pilot case')
    overlay={**record,'configuration':configuration,'suite':'hooks-pilot-v1',
             'baseline_commit':PILOT_BASELINE_COMMIT,'baseline_sha256':PILOT_BASELINE_PAYLOAD}
    with (directory/'pilot-trial.json').open('x') as stream:json.dump(overlay,stream,indent=2)
    return overlay


def _pilot_usage(value):
    if value is None:return None,None
    if not isinstance(value,dict) or value.get('status') not in {'complete','unavailable'}:
        raise ValueError('invalid usage observation')
    if value['status']=='unavailable':
        if value.get('totals') is not None:raise ValueError('unavailable usage has totals')
        return None,None
    totals=value.get('totals');participants=value.get('participants')
    fields=('input_tokens','cached_input_tokens','cache_write_input_tokens',
            'output_tokens','reasoning_output_tokens','total_tokens')
    if (not isinstance(totals,dict) or set(totals)!=set(fields)
            or any(type(totals[field]) is not int or totals[field]<0 for field in fields)
            or not isinstance(participants,list) or not participants):
        raise ValueError('invalid usage totals')
    safe=[]
    for row in participants:
        if not isinstance(row,dict) or not isinstance(row.get('role'),str):
            raise ValueError('invalid participant usage')
        tokens=row.get('tokens')
        if (not isinstance(tokens,dict) or set(tokens)!=set(fields)
                or any(type(tokens[field]) is not int or tokens[field]<0 for field in fields)):
            raise ValueError('invalid participant tokens')
        safe.append(dict(role=row['role'],model=row.get('model'),reasoning_effort=row.get('reasoning_effort'),
                         responses=row.get('responses'),tokens=tokens))
    calculated={field:sum(row['tokens'][field] for row in safe) for field in fields}
    if calculated!=totals:raise ValueError('participant usage does not match totals')
    return totals,safe


def pilot_record(directory, observations):
    """Write reviewed pilot outcomes; missing observations remain explicit nulls."""
    metadata=(directory/'pilot-trial.json')
    record=json.loads((metadata if metadata.is_file() else directory/'trial.json').read_text())
    if record.get('suite')!='hooks-pilot-v1':raise ValueError('not a hooks pilot fixture')
    allowed={'usage','wall_seconds','protocol_pass','claimed_complete','lost_requirements',
             'recovery_success','payload_sha256','host','coordinator_model','coordinator_effort',
             'steering_observed','resume_observed'}
    if set(observations)!=allowed:raise ValueError('pilot observation fields must match the documented contract')
    for name in ('protocol_pass','claimed_complete','recovery_success','steering_observed','resume_observed'):
        if observations[name] is not None and type(observations[name]) is not bool:
            raise ValueError('invalid pilot boolean')
    wall=observations['wall_seconds']
    if wall is not None and (type(wall) not in (int,float) or not math.isfinite(wall) or wall<=0):
        raise ValueError('invalid pilot wall time')
    lost=observations['lost_requirements']
    if lost is not None and (type(lost) is not int or lost<0):raise ValueError('invalid lost requirement count')
    payload=observations['payload_sha256']
    if (not isinstance(payload,str) or len(payload) not in {16,64}
            or any(char not in '0123456789abcdef' for char in payload)):
        raise ValueError('invalid pilot payload digest')
    if observations['host'] not in {'cli','desktop'}:raise ValueError('invalid pilot host')
    if not isinstance(observations['coordinator_model'],str) or not observations['coordinator_model']:
        raise ValueError('actual coordinator model is required')
    if observations['coordinator_effort'] not in {'low','medium','high','xhigh','max','ultra'}:
        raise ValueError('actual coordinator effort is required')
    totals,participants=_pilot_usage(observations['usage'])
    coordinators=([row for row in participants if row['role']=='coordinator'] if participants else [])
    if coordinators and not any(row.get('model')==observations['coordinator_model']
                                and row.get('reasoning_effort')==observations['coordinator_effort']
                                for row in coordinators):
        raise ValueError('recorded coordinator settings do not match native usage')
    c=selected(record['case'])
    eligible=((not c['steering'] or observations['steering_observed'] is True)
              and (c['family']!='recovery' or observations['resume_observed'] is True))
    graded=grade(directory) if eligible else {**grade(directory),'functional_success':None}
    correctness=graded['functional_success']
    claimed=observations['claimed_complete']
    false_completion=(claimed and not correctness if claimed is not None and correctness is not None else None)
    required=[correctness,observations['lost_requirements'],false_completion,
              observations['protocol_pass'],wall,totals]
    if c['family']=='recovery':required.append(observations['recovery_success'])
    result={**record,**graded,'correctness':correctness,'false_completion':false_completion,
            'participant_tokens':participants,'tokens':totals,
            **{key:value for key,value in observations.items() if key!='usage'},
            'status':'measured' if all(value is not None for value in required) else 'incomplete'}
    path=directory/'pilot-result.json'
    with path.open('x') as stream:json.dump(result,stream,indent=2)
    return result


def pilot_compare(records):
    """Describe the 12-run pilot; absent or unknown observations never become zero."""
    expected={(configuration,case_name) for configuration in PILOT_CONFIGURATIONS for case_name in PILOT_CASES}
    indexed={}
    for row in records:
        key=(row.get('configuration'),row.get('case'))
        if row.get('suite')!='hooks-pilot-v1' or key not in expected:raise ValueError('unknown pilot result')
        if key in indexed:raise ValueError('duplicate pilot result')
        indexed[key]=row
    configurations={}
    for configuration in PILOT_CONFIGURATIONS:
        rows=[indexed.get((configuration,case_name)) for case_name in PILOT_CASES]
        complete=all(row is not None and row.get('status')=='measured' for row in rows)
        measured=[row for row in rows if row is not None]
        def values(name):return [row.get(name) for row in measured]
        tokens=[row.get('tokens') for row in measured]
        configurations[configuration]=dict(
            status='complete' if complete else 'unverified',runs_present=len(measured),runs_required=4,
            correctness=(sum(value is True for value in values('correctness')) if complete else None),
            lost_requirements=(sum(values('lost_requirements')) if complete else None),
            false_completions=(sum(value is True for value in values('false_completion')) if complete else None),
            recovery_success=(next((row.get('recovery_success') for row in measured if row.get('case')=='resume-pagination'),None) if complete else None),
            protocol_passes=(sum(value is True for value in values('protocol_pass')) if complete else None),
            median_wall_seconds=(statistics.median(values('wall_seconds')) if complete else None),
            median_total_tokens=(statistics.median(row['total_tokens'] for row in tokens) if complete else None),
            median_cached_input_tokens=(statistics.median(row['cached_input_tokens'] for row in tokens) if complete else None))
    matrix=[]
    for configuration in PILOT_CONFIGURATIONS:
        for case_name in PILOT_CASES:
            row=indexed.get((configuration,case_name))
            matrix.append(dict(configuration=configuration,case=case_name,
                status=row.get('status') if row else 'unrun',
                correctness=row.get('correctness') if row else None,
                lost_requirements=row.get('lost_requirements') if row else None,
                false_completion=row.get('false_completion') if row else None,
                recovery_success=row.get('recovery_success') if row else None,
                protocol_pass=row.get('protocol_pass') if row else None,
                wall_seconds=row.get('wall_seconds') if row else None,
                tokens=row.get('tokens') if row else None))
    return dict(suite='hooks-pilot-v1',configurations=configurations,runs=matrix)


def _validate_phase01_contract(value):
    """Require the complete frozen manifest to match evaluator semantics."""
    expected_scenarios = [
        dict(scenario_id=scenario['scenario_id'], family=scenario['family'], split='holdout',
             primary_metric=scenario['primary_metric'], direction=scenario['direction'],
             guardrails=list(scenario['guardrails']))
        for scenario in PHASE01_SCENARIOS
    ]
    expected = {
        'suite_version': PHASE01_SUITE_VERSION,
        'scenario_families': expected_scenarios,
        'arms': list(PHASE01_ARMS),
        'paired_repeats': len(PHASE01_REPEATS),
        'blind_scoring': {
            'join_key': 'blind_join_key',
            'scorer_inputs': ['suite_version', 'scenario_id', 'family', 'split', 'attempt',
                              'fixture_sha256', 'starting_tree_sha256', 'scorecard', 'cost', 'stop'],
            'excluded_from_scorer_inputs': ['arm', 'configuration', 'baseline_payload_sha256',
                                            'candidate_payload_sha256'],
            'score_outcomes_separately_from_protocol': True,
        },
        'scorecard': {
            'f': ['requirement_count', 'satisfied_count', 'critical_omission_count',
                  'completion_claim_count', 'supported_claim_count', 'explicitly_unrun_check_count',
                  'protected_content_preserved'],
            'd': ['facts_hypotheses_separated', 'discriminating_check_before_mutation',
                  'root_cause_success', 'failed_fix_count'],
            'r': ['fanout_count', 'unsafe_fanout_conflict_count', 'duplicate_dispatch_report_count',
                  'resource_conflicts'],
        },
        'stop_conditions': ['invariant_violation', 'protocol_failure', 'audit_failure',
                            'protected_fixture_violation', 'command_receipt_missing', 'open_session',
                            'arm_or_fixture_mismatch', 'host_unavailable'],
        'cost_accounting': {
            'token_fields': list(PHASE01_TOKEN_FIELDS),
            'wall_seconds_source': 'coordinator_task_lifecycle_or_null',
            'observable_counts': list(PHASE01_COUNT_FIELDS),
            'currency': 'not_claimed_without_a_pinned_rate_table',
        },
        'missingness': 'Unavailable observations are null with unavailable_reason; null is never zero and makes the affected comparison unverified.',
        'decision_rule': PHASE01_DECISION_RULE,
    }
    if value != expected:
        raise ValueError('phase-0 contract does not match evaluator semantics')


def phase01_contract():
    """Return the checked-in, integrity-locked phase-0 contract."""
    raw = PHASE01_CONTRACT_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PHASE01_CONTRACT_SHA256:
        raise ValueError('phase-0 contract integrity mismatch')
    value = json.loads(raw)
    _validate_phase01_contract(value)
    return value


def phase01_workloads():
    """Return the integrity-locked, scenario-specific Phase 2 workload manifest."""
    raw = PHASE01_WORKLOAD_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PHASE01_WORKLOAD_SHA256:
        raise ValueError('phase-0 workload manifest integrity mismatch')
    value = json.loads(raw)
    if value.get('manifest_version') != 'phase01-workloads-v1' or value.get('suite_version') != PHASE01_SUITE_VERSION:
        raise ValueError('invalid phase-0 workload manifest version')
    if value.get('source_revision') != 8:
        raise ValueError('phase-0 workload manifest source revision mismatch')
    identities = value.get('payload_identities')
    if (not isinstance(identities, dict) or set(identities) != set(PHASE01_ARMS)
            or identities['baseline'] == identities['candidate']):
        raise ValueError('phase-0 workload payload identities are invalid')
    for arm in PHASE01_ARMS:
        _digest(identities[arm], f'{arm}_payload_sha256', allow_none=False)
    scenarios = value.get('scenarios')
    if not isinstance(scenarios, list) or [row.get('scenario_id') for row in scenarios] != ['F-01', 'D-01', 'R-01']:
        raise ValueError('phase-0 workload scenarios are invalid')
    protected_sha256 = hashlib.sha256('Protected user content — do not modify.\n'.encode()).hexdigest()
    for row, frozen in zip(scenarios, PHASE01_SCENARIOS):
        required = ('scenario_id', 'family', 'prompt', 'expected_user_boundary_requirements',
                    'independent_checks', 'protected_paths', 'fixture_seed', 'prompt_sha256',
                    'fixture_sha256_by_repeat')
        if any(field not in row for field in required):
            raise ValueError(f"phase-0 workload {row.get('scenario_id')} is incomplete")
        if row['scenario_id'] != frozen['scenario_id'] or row['family'] != frozen['family']:
            raise ValueError('phase-0 workload scenario mismatch')
        if not isinstance(row['prompt'], str) or not row['prompt'].startswith('$cortex:orchestrator '):
            raise ValueError(f"phase-0 workload {row['scenario_id']} prompt is invalid")
        if hashlib.sha256((row['prompt'] + '\n').encode()).hexdigest() != row['prompt_sha256']:
            raise ValueError(f"phase-0 workload {row['scenario_id']} prompt hash mismatch")
        if row['protected_paths'] != ['USER-NOTE.txt'] or not row['fixture_seed']:
            raise ValueError(f"phase-0 workload {row['scenario_id']} fixture boundary is invalid")
        if not isinstance(row['expected_user_boundary_requirements'], list) or not row['expected_user_boundary_requirements']:
            raise ValueError(f"phase-0 workload {row['scenario_id']} requirements are invalid")
        if not isinstance(row['independent_checks'], list) or not row['independent_checks']:
            raise ValueError(f"phase-0 workload {row['scenario_id']} checks are invalid")
        hashes = row['fixture_sha256_by_repeat']
        if set(hashes) != {'1', '2', '3'}:
            raise ValueError(f"phase-0 workload {row['scenario_id']} repeat hashes are incomplete")
        for repeat in PHASE01_REPEATS:
            material = json.dumps(dict(scenario_id=row['scenario_id'], family=row['family'],
                                       fixture_seed=row['fixture_seed'], repeat=repeat,
                                       protected_sha256=protected_sha256),
                                  sort_keys=True, separators=(',', ':')).encode()
            if hashlib.sha256(material).hexdigest() != hashes[str(repeat)]:
                raise ValueError(f"phase-0 workload {row['scenario_id']} fixture hash mismatch")
    return value


def _phase01_scenario(value):
    for scenario in PHASE01_SCENARIOS:
        if value in (scenario['scenario_id'], scenario['family']):
            return scenario
    raise ValueError('unknown phase-0 scenario family')


def _phase01_workload(value):
    for workload in phase01_workloads()['scenarios']:
        if value in (workload['scenario_id'], workload['family']):
            return workload
    raise ValueError('unknown phase-0 workload scenario')


def _digest(value, name, allow_none=True):
    if value is None and allow_none:
        return
    if not isinstance(value, str) or len(value) != 64 or any(char not in '0123456789abcdef' for char in value):
        raise ValueError(f'{name} must be a lowercase SHA-256 digest or null')


def _phase01_trial_key(record):
    return (record['scenario_id'], record['attempt'], record['arm'])


def _phase01_score_input(record, measured):
    """Build the scorer packet; arm/configuration identity is deliberately absent."""
    scorecard = {name: measured.get(name) for name in PHASE01_SCORE_FIELDS
                 if name not in {'protocol_pass', 'claimed_complete'}}
    cost = {name: measured.get(name) for name in (*PHASE01_TOKEN_FIELDS, 'wall_seconds', *PHASE01_COUNT_FIELDS)}
    stop = {name: measured.get(name) for name in ('stop_reason', 'invariant_violation', 'unavailable_reason')}
    return dict(suite_version=record['suite_version'], scenario_id=record['scenario_id'],
                family=record['family'], split=record['split'], attempt=record['attempt'],
                fixture_sha256=record['fixture_sha256'],
                starting_tree_sha256=record['starting_tree_sha256'], scorecard=scorecard,
                cost=cost, stop=stop)


def phase01_prepare(scenario, directory, arm, attempt, payload_sha256=None,
                    model_settings_sha256=None, host='cli',
                    baseline_payload_sha256=None, candidate_payload_sha256=None):
    """Create one non-overwriting held-out trial envelope and isolated fixture."""
    selected_scenario = _phase01_scenario(scenario)
    if arm not in PHASE01_ARMS or attempt not in PHASE01_REPEATS:
        raise ValueError('phase-0 arm or repeat is invalid')
    if host not in {'cli', 'desktop'}:
        raise ValueError('phase-0 host is invalid')
    _digest(payload_sha256, 'payload_sha256')
    _digest(model_settings_sha256, 'model_settings_sha256')
    _digest(baseline_payload_sha256, 'baseline_payload_sha256')
    _digest(candidate_payload_sha256, 'candidate_payload_sha256')
    workloads = phase01_workloads()
    frozen_identities = workloads['payload_identities']
    if baseline_payload_sha256 is not None and baseline_payload_sha256 != frozen_identities['baseline']:
        raise ValueError('baseline payload does not match frozen phase-0 identity')
    if candidate_payload_sha256 is not None and candidate_payload_sha256 != frozen_identities['candidate']:
        raise ValueError('candidate payload does not match frozen phase-0 identity')
    if (baseline_payload_sha256 is not None and candidate_payload_sha256 is not None
            and baseline_payload_sha256 == candidate_payload_sha256):
        raise ValueError('baseline and candidate payload identities must differ')
    expected_arm_payload = baseline_payload_sha256 if arm == 'baseline' else candidate_payload_sha256
    if expected_arm_payload is not None and payload_sha256 is not None and payload_sha256 != expected_arm_payload:
        raise ValueError('payload does not match the selected frozen phase-0 arm')
    workload = _phase01_workload(selected_scenario['scenario_id'])
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    project = directory / 'project'
    project.mkdir()
    protected = project / 'USER-NOTE.txt'
    protected.write_text('Protected user content — do not modify.\n')
    (project / 'README.md').write_text('# Held-out fixture\nUse the ordinary product prompt; do not modify USER-NOTE.txt.\n')
    prompt = workload['prompt'] + '\n'
    (directory / 'prompt.txt').write_text(prompt)
    fixture_material = json.dumps(dict(scenario_id=workload['scenario_id'], family=workload['family'],
                                       fixture_seed=workload['fixture_seed'], repeat=attempt,
                                       protected_sha256=hashlib.sha256(protected.read_bytes()).hexdigest()),
                                  sort_keys=True, separators=(',', ':')).encode()
    fixture_sha256 = hashlib.sha256(fixture_material).hexdigest()
    if fixture_sha256 != workload['fixture_sha256_by_repeat'][str(attempt)]:
        raise ValueError('phase-0 generated fixture hash does not match frozen workload')
    starting_tree_sha256 = hashlib.sha256(b'README.md\nUSER-NOTE.txt\n').hexdigest()
    prompt_sha256 = hashlib.sha256(prompt.encode()).hexdigest()
    blind_join_key = hashlib.sha256(
        f'{PHASE01_SUITE_VERSION}:{selected_scenario["scenario_id"]}:{attempt}:{fixture_sha256}:{starting_tree_sha256}:{model_settings_sha256 or "null"}'.encode()
    ).hexdigest()
    record = dict(suite_version=PHASE01_SUITE_VERSION, scenario_id=selected_scenario['scenario_id'],
                  family=selected_scenario['family'], split='holdout', attempt=attempt, arm=arm,
                  fixture_sha256=fixture_sha256, starting_tree_sha256=starting_tree_sha256,
                  prompt_sha256=prompt_sha256, protected_sha256=hashlib.sha256(protected.read_bytes()).hexdigest(),
                  baseline_payload_sha256=payload_sha256 if arm == 'baseline' else None,
                  candidate_payload_sha256=payload_sha256 if arm == 'candidate' else None,
                  frozen_baseline_payload_sha256=frozen_identities['baseline'],
                  frozen_candidate_payload_sha256=frozen_identities['candidate'],
                  workload_manifest_sha256=PHASE01_WORKLOAD_SHA256,
                  workload_version=workloads['manifest_version'],
                  model_settings_sha256=model_settings_sha256, host=host,
                  blind_join_key=blind_join_key, status='prepared')
    (directory / 'phase01-trial.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


PHASE01_OBSERVATION_FIELDS = set(PHASE01_TOKEN_FIELDS + PHASE01_COUNT_FIELDS + PHASE01_SCORE_FIELDS + (
    'wall_seconds', 'wall_source', 'stop_reason', 'invariant_violation',
    'unavailable_reason', 'scorer_id', 'blindness', 'adjudication_status',
    'payload_sha256', 'baseline_payload_sha256', 'candidate_payload_sha256',
    'fixture_sha256', 'starting_tree_sha256', 'prompt_sha256',
    'model_settings_sha256', 'host', 'blind_join_key', 'frozen_baseline_payload_sha256',
    'frozen_candidate_payload_sha256', 'workload_manifest_sha256', 'workload_version'))


def _phase01_observation(observations):
    if not isinstance(observations, dict):
        raise ValueError('phase-0 observations must be an object')
    observations = dict(observations)
    tokens = observations.pop('tokens', None)
    if tokens is not None:
        if not isinstance(tokens, dict):
            raise ValueError('phase-0 tokens must be an object')
        unknown = set(tokens) - set(PHASE01_TOKEN_FIELDS)
        if unknown:
            raise ValueError('unknown phase-0 token field')
        observations.update(tokens)
    known_envelope = {'suite_version', 'scenario_id', 'family', 'split', 'attempt', 'arm',
                      'protected_sha256', 'status', 'blind_score'}
    unknown = set(observations) - PHASE01_OBSERVATION_FIELDS - known_envelope
    if unknown:
        raise ValueError(f'unknown phase-0 observation field: {sorted(unknown)[0]}')
    result = {field: observations.get(field) for field in PHASE01_OBSERVATION_FIELDS}
    for field in PHASE01_TOKEN_FIELDS + PHASE01_COUNT_FIELDS + ('requirement_count', 'satisfied_count',
            'critical_omission_count', 'completion_claim_count', 'supported_claim_count',
            'explicitly_unrun_check_count', 'failed_fix_count', 'fanout_count',
            'unsafe_fanout_conflict_count', 'duplicate_dispatch_report_count', 'resource_conflicts'):
        value = result[field]
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f'invalid phase-0 count: {field}')
    if result['wall_seconds'] is not None and (type(result['wall_seconds']) not in (int, float)
                                               or not math.isfinite(result['wall_seconds'])
                                               or result['wall_seconds'] <= 0):
        raise ValueError('invalid phase-0 wall time')
    for field in ('facts_hypotheses_separated', 'discriminating_check_before_mutation',
                  'root_cause_success', 'protocol_pass', 'claimed_complete',
                  'protected_content_preserved'):
        if result[field] is not None and type(result[field]) is not bool:
            raise ValueError(f'invalid phase-0 boolean: {field}')
    for field in ('payload_sha256', 'baseline_payload_sha256', 'candidate_payload_sha256',
                  'fixture_sha256', 'starting_tree_sha256', 'prompt_sha256',
                  'model_settings_sha256', 'blind_join_key'):
        _digest(result[field], field)
    if result['host'] is not None and result['host'] not in {'cli', 'desktop'}:
        raise ValueError('invalid phase-0 host')
    if result['blindness'] is not None and result['blindness'] is not True:
        raise ValueError('phase-0 scorer must confirm blindness')
    return result


def phase01_record(directory, observations):
    """Persist one reviewed score/cost packet; unavailable fields stay null."""
    directory = Path(directory)
    trial_path = directory / 'phase01-trial.json'
    if not trial_path.is_file():
        raise ValueError('missing phase-0 trial envelope')
    record = json.loads(trial_path.read_text())
    if record.get('suite_version') != PHASE01_SUITE_VERSION or record.get('split') != 'holdout':
        raise ValueError('invalid phase-0 trial envelope')
    selected_scenario = _phase01_scenario(record.get('scenario_id'))
    measured = _phase01_observation(observations)
    for identity in ('scenario_id', 'family', 'attempt', 'arm'):
        if identity in observations and observations[identity] != record[identity]:
            raise ValueError(f'phase-0 identity mismatch: {identity}')
    for field in ('fixture_sha256', 'starting_tree_sha256', 'prompt_sha256', 'model_settings_sha256', 'blind_join_key', 'host'):
        if measured[field] is not None and measured[field] != record[field]:
            raise ValueError(f'phase-0 identity mismatch: {field}')
    if measured['blindness'] is not True:
        raise ValueError('phase-0 scorer must confirm blindness')
    payload = measured['payload_sha256']
    expected_payload = record['baseline_payload_sha256'] if record['arm'] == 'baseline' else record['candidate_payload_sha256']
    if payload is not None and expected_payload is not None and payload != expected_payload:
        raise ValueError('phase-0 arm payload mismatch')
    blind_score = _phase01_score_input(record, measured)
    identity_fields = ('payload_sha256', 'model_settings_sha256', 'fixture_sha256',
                       'starting_tree_sha256', 'prompt_sha256', 'host')
    identity_missing = any(measured[field] is None for field in identity_fields)
    expected_payload_missing = expected_payload is None
    stopped = (measured['stop_reason'] is not None or measured['invariant_violation'] is not None
               or measured['protocol_pass'] is False or identity_missing or expected_payload_missing)
    if identity_missing and measured['stop_reason'] is None:
        measured['stop_reason'] = 'arm_identity_missing'
    elif expected_payload_missing and measured['stop_reason'] is None:
        measured['stop_reason'] = 'expected_payload_identity_missing'
    essential = ('protocol_pass', 'claimed_complete', 'wall_seconds', *PHASE01_TOKEN_FIELDS)
    status = 'stopped' if stopped else ('measured' if all(measured[field] is not None for field in essential) else 'incomplete')
    result = {**record, **measured, 'blind_score': blind_score, 'status': status}
    path = directory / 'phase01-result.json'
    with path.open('x') as stream:
        json.dump(result, stream, indent=2)
    return result


def _phase01_metric(row, scenario):
    if any(row.get(field) is None for field in PHASE01_METRIC_FIELDS[scenario['scenario_id']]):
        return None
    if scenario['scenario_id'] == 'F-01':
        claims = row['completion_claim_count']
        return (max(0, claims - row['supported_claim_count']) / claims) if claims else 0.0
    if scenario['scenario_id'] == 'D-01':
        return 1.0 if row['discriminating_check_before_mutation'] is True else 0.0
    fanout = row['fanout_count']
    return (row['unsafe_fanout_conflict_count'] / fanout) if fanout else 0.0


def _phase01_guardrail_regressions(baseline, candidate, scenario):
    """Return all frozen guardrail regressions; null never becomes zero."""
    required = PHASE01_GUARDRAILS[scenario['scenario_id']]
    if any(baseline.get(field) is None or candidate.get(field) is None for field in required):
        return None
    regressions = []
    if scenario['scenario_id'] == 'F-01':
        if candidate['critical_omission_count'] > baseline['critical_omission_count']:
            regressions.append('critical_omission_count')
        if _phase01_metric(candidate, scenario) > _phase01_metric(baseline, scenario):
            regressions.append('false_completion_rate')
        if candidate['protected_content_preserved'] is not True:
            regressions.append('protected_content_preserved')
    elif scenario['scenario_id'] == 'D-01':
        if candidate['root_cause_success'] < baseline['root_cause_success']:
            regressions.append('root_cause_success')
        if candidate['failed_fix_count'] > baseline['failed_fix_count']:
            regressions.append('failed_fix_count')
    else:
        if candidate['unsafe_fanout_conflict_count'] > baseline['unsafe_fanout_conflict_count']:
            regressions.append('unsafe_fanout_conflict_count')
        if candidate['duplicate_dispatch_report_count'] > baseline['duplicate_dispatch_report_count']:
            regressions.append('duplicate_dispatch_report_count')
        if candidate['resource_conflicts'] > baseline['resource_conflicts']:
            regressions.append('resource_conflicts')
    return regressions


def _phase01_cost(row):
    if any(row.get(field) is None for field in (*PHASE01_TOKEN_FIELDS, 'wall_seconds')):
        return None
    return dict(tokens=row['total_tokens'], wall_seconds=row['wall_seconds'])


def phase01_compare(records):
    """Blindly summarize 3 families × 3 paired repeats; never accept or impute nulls."""
    expected = {(scenario['scenario_id'], attempt, arm)
                for scenario in PHASE01_SCENARIOS for attempt in PHASE01_REPEATS for arm in PHASE01_ARMS}
    indexed = {}
    for row in records:
        if not isinstance(row, dict) or row.get('suite_version') != PHASE01_SUITE_VERSION:
            raise ValueError('unknown phase-0 result')
        key = (row.get('scenario_id'), row.get('attempt'), row.get('arm'))
        if key not in expected:
            raise ValueError('unknown phase-0 trial')
        if key in indexed:
            raise ValueError('duplicate phase-0 trial')
        _phase01_scenario(row['scenario_id'])
        if row.get('status') in {'measured', 'stopped'}:
            _phase01_observation(row)
            # A caller cannot promote a row by forging status='measured' around
            # an explicit protocol failure or stop reason.
            row = dict(row)
            if (row.get('status') == 'measured'
                    and (row.get('protocol_pass') is False
                         or any(row.get(field) is not None for field in
                                ('stop_reason', 'invariant_violation', 'unavailable_reason')))):
                row['status'] = 'stopped'
        indexed[key] = row
    pairs = []
    identity_fields = ('payload_sha256', 'model_settings_sha256', 'fixture_sha256',
                       'starting_tree_sha256', 'prompt_sha256', 'host')
    for scenario in PHASE01_SCENARIOS:
        for attempt in PHASE01_REPEATS:
            baseline = indexed.get((scenario['scenario_id'], attempt, 'baseline'))
            candidate = indexed.get((scenario['scenario_id'], attempt, 'candidate'))
            pair_status = 'unrun' if baseline is None or candidate is None else 'unverified'
            pair = dict(scenario_id=scenario['scenario_id'], family=scenario['family'], attempt=attempt,
                        status=pair_status, baseline_metric=None, candidate_metric=None,
                        baseline_cost=None, candidate_cost=None, cost_delta=None,
                        stop_reason=None, guardrail_regressions=[])
            if baseline is not None and candidate is not None:
                if baseline.get('blind_join_key') != candidate.get('blind_join_key'):
                    pair['stop_reason'] = 'blind_join_mismatch'
                elif any(baseline.get(field) is None or candidate.get(field) is None for field in identity_fields):
                    pair['stop_reason'] = 'arm_identity_missing'
                elif any(baseline.get(field) != candidate.get(field) for field in
                         ('fixture_sha256', 'starting_tree_sha256', 'prompt_sha256', 'model_settings_sha256', 'host')):
                    pair['stop_reason'] = 'paired_identity_mismatch'
                elif (baseline.get('baseline_payload_sha256') is not None
                      and candidate.get('candidate_payload_sha256') is not None
                      and baseline['baseline_payload_sha256'] == candidate['candidate_payload_sha256']):
                    pair['stop_reason'] = 'baseline_candidate_payload_not_distinct'
                elif baseline.get('status') == 'stopped' or candidate.get('status') == 'stopped':
                    pair['stop_reason'] = baseline.get('stop_reason') or candidate.get('stop_reason') or 'protocol_or_invariant_failure'
                elif baseline.get('status') != 'measured' or candidate.get('status') != 'measured':
                    pair['stop_reason'] = 'incomplete_observation'
                else:
                    guardrail_regressions = _phase01_guardrail_regressions(baseline, candidate, scenario)
                    if guardrail_regressions is None:
                        pair['stop_reason'] = 'missing_guardrail_observation'
                    elif guardrail_regressions:
                        pair['guardrail_regressions'] = guardrail_regressions
                        pair['stop_reason'] = 'guardrail_regression'
                    else:
                        pair['baseline_metric'] = _phase01_metric(baseline, scenario)
                        pair['candidate_metric'] = _phase01_metric(candidate, scenario)
                        pair['baseline_cost'] = _phase01_cost(baseline)
                        pair['candidate_cost'] = _phase01_cost(candidate)
                        if pair['baseline_metric'] is not None and pair['candidate_metric'] is not None and pair['baseline_cost'] is not None and pair['candidate_cost'] is not None:
                            pair['cost_delta'] = dict(token_ratio=pair['candidate_cost']['tokens'] / pair['baseline_cost']['tokens'],
                                                      wall_ratio=pair['candidate_cost']['wall_seconds'] / pair['baseline_cost']['wall_seconds'])
                            pair['status'] = 'measured'
                        else:
                            pair['stop_reason'] = 'missing_score_or_cost'
            pairs.append(pair)
    families = {}
    for scenario in PHASE01_SCENARIOS:
        family_runs = [row for row in pairs if row['scenario_id'] == scenario['scenario_id']]
        measured = [row for row in family_runs if row['status'] == 'measured']
        improved = []
        for row in measured:
            improved.append((row['candidate_metric'] < row['baseline_metric']) if scenario['direction'] == 'lower'
                            else (row['candidate_metric'] > row['baseline_metric']))
        families[scenario['family']] = dict(status='measured' if len(measured) == 3 else 'unverified',
                                             repeats_present=len(measured), repeats_required=3,
                                             baseline_metric=(statistics.median(row['baseline_metric'] for row in measured) if measured else None),
                                             candidate_metric=(statistics.median(row['candidate_metric'] for row in measured) if measured else None),
                                             improved_repeats=(sum(improved) if measured else None),
                                             median_token_ratio=(statistics.median(row['cost_delta']['token_ratio'] for row in measured) if measured else None),
                                             median_wall_ratio=(statistics.median(row['cost_delta']['wall_ratio'] for row in measured) if measured else None),
                                             guardrail_regressions=sorted({item for row in family_runs for item in row['guardrail_regressions']}))
    complete = all(value['status'] == 'measured' for value in families.values())
    guardrail_failure = any(pair['guardrail_regressions'] for pair in pairs)
    promoted = (complete and not guardrail_failure
                and all(value['improved_repeats'] >= 2 for value in families.values()))
    if complete:
        for value in families.values():
            if value['median_token_ratio'] > 1.2 or value['median_wall_ratio'] > 1.2:
                promoted = False
    decision = 'promote_candidate' if promoted else ('revise_or_replicate' if complete or guardrail_failure else 'unverified')
    runs = []
    for pair in pairs:
        for arm in PHASE01_ARMS:
            source = indexed.get((pair['scenario_id'], pair['attempt'], arm))
            runs.append(dict(scenario_id=pair['scenario_id'], family=pair['family'],
                             attempt=pair['attempt'], arm=arm,
                             status=pair['status'], metric=(pair['baseline_metric'] if arm == 'baseline' else pair['candidate_metric']),
                             cost=(pair['baseline_cost'] if arm == 'baseline' else pair['candidate_cost']),
                             stop_reason=pair['stop_reason']))
    return dict(suite_version=PHASE01_SUITE_VERSION, arms=list(PHASE01_ARMS), paired_repeats=3,
                families=families, pairs=pairs, runs=runs, decision=decision,
                decision_owner='coordinator', acceptance_automatic=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list')
    commands.add_parser('pilot-list')
    p = commands.add_parser('prepare')
    p.add_argument('case', choices=[c['name'] for c in CASES]); p.add_argument('directory', type=Path)
    p.add_argument('--configuration', choices=CONFIGURATIONS, required=True)
    p.add_argument('--attempt', type=int, choices=(1, 2, 3), required=True)
    p = commands.add_parser('grade'); p.add_argument('directory', type=Path)
    p.add_argument('--phase', choices=('initial', 'final'), default='final')
    p = commands.add_parser('compare'); p.add_argument('records', type=Path)
    p = commands.add_parser('record'); p.add_argument('directory', type=Path); p.add_argument('observations', type=Path)
    p=commands.add_parser('pilot-prepare');p.add_argument('case',choices=PILOT_CASES);p.add_argument('directory',type=Path);p.add_argument('--configuration',choices=PILOT_CONFIGURATIONS,required=True)
    p=commands.add_parser('pilot-adopt');p.add_argument('directory',type=Path);p.add_argument('--configuration',choices=PILOT_CONFIGURATIONS,required=True)
    p=commands.add_parser('pilot-record');p.add_argument('directory',type=Path);p.add_argument('observations',type=Path)
    p=commands.add_parser('pilot-compare');p.add_argument('records',type=Path)
    commands.add_parser('phase01-contract')
    commands.add_parser('phase01-workloads')
    p=commands.add_parser('phase01-prepare');p.add_argument('scenario');p.add_argument('directory',type=Path)
    p.add_argument('--arm',choices=PHASE01_ARMS,required=True);p.add_argument('--attempt',type=int,choices=PHASE01_REPEATS,required=True)
    p.add_argument('--payload-sha256');p.add_argument('--model-settings-sha256');p.add_argument('--host',choices=('cli','desktop'),default='cli')
    p.add_argument('--baseline-payload-sha256');p.add_argument('--candidate-payload-sha256')
    p=commands.add_parser('phase01-record');p.add_argument('directory',type=Path);p.add_argument('observations',type=Path)
    p=commands.add_parser('phase01-compare');p.add_argument('records',type=Path)
    args = parser.parse_args()
    if args.command == 'list':
        result = [dict(name=c['name'], split=c['split'], family=c['family'], resume=c['family']=='recovery') for c in CASES]
    elif args.command=='pilot-list':
        result=dict(suite='hooks-pilot-v1',configurations=PILOT_CONFIGURATIONS,
                    cases=[dict(name=name,family=selected(name)['family'],steering=bool(selected(name)['steering']),resume=selected(name)['family']=='recovery') for name in PILOT_CASES],runs=12)
    elif args.command == 'prepare':result = prepare(args.case, args.directory, args.configuration, args.attempt)
    elif args.command == 'grade':result = grade(args.directory, args.phase)
    elif args.command == 'record':result = record_trial(args.directory, json.loads(args.observations.read_text()))
    elif args.command=='pilot-prepare':result=pilot_prepare(args.case,args.directory,args.configuration)
    elif args.command=='pilot-adopt':result=pilot_adopt(args.directory,args.configuration)
    elif args.command=='pilot-record':result=pilot_record(args.directory,json.loads(args.observations.read_text()))
    elif args.command=='pilot-compare':result=pilot_compare(json.loads(args.records.read_text()))
    elif args.command=='phase01-contract':result=phase01_contract()
    elif args.command=='phase01-workloads':result=phase01_workloads()
    elif args.command=='phase01-prepare':
        frozen = phase01_workloads()['payload_identities']
        result=phase01_prepare(args.scenario,args.directory,args.arm,args.attempt,args.payload_sha256,
                               args.model_settings_sha256,args.host,
                               args.baseline_payload_sha256 or frozen['baseline'],
                               args.candidate_payload_sha256 or frozen['candidate'])
    elif args.command=='phase01-record':result=phase01_record(args.directory,json.loads(args.observations.read_text()))
    elif args.command=='phase01-compare':result=phase01_compare(json.loads(args.records.read_text()))
    else:result = compare(json.loads(args.records.read_text()))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
