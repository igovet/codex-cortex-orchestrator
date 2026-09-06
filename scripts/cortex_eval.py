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
    else:result = compare(json.loads(args.records.read_text()))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
