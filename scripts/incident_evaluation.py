#!/usr/bin/env python3
"""Offline evaluator of normalized, source-backed incident observations.

This evaluates a supplied trace, never controls a running task or verifies the
authenticity of its source. A synthetic trace is not a live Codex qualification.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/cortex/scripts'))
from cortex_runtime.incident_quality import evaluate_trace


def qualify(events):
    quality = evaluate_trace(events)
    proof = any(
        type(row) is dict and row.get('kind') == 'outcome'
        and row.get('boundary') == 'user' and row.get('completed') is True
        and row.get('artifact_reconciled') is True
        and row.get('deployed_revision_verified') is True
        for row in events[:1024]
    ) if type(events) is list else False
    metrics = quality['metrics']
    process = (metrics.get('incident_owners') == 1 and metrics.get('agents') == 2
               and metrics.get('implementations') == 1 and metrics.get('rollouts') == 1
               and metrics.get('reviews') == 1)
    passed = quality['verified'] and not quality['signals'] and proof and process
    return dict(quality=quality, outcome_evidence_present=proof,
                process_targets_met=process, evaluation='pass' if passed else 'fail',
                source_authenticity='not_verified_by_evaluator')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace', type=Path, help='JSON array of normalized observations')
    args = parser.parse_args()
    try:
        with args.trace.open('rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('oversized trace')
        result = qualify(json.loads(raw))
    except (OSError, ValueError, TypeError, RecursionError):
        result = {'evaluation': 'unverified', 'reason': 'unreadable_or_invalid_trace'}
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result['evaluation'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
