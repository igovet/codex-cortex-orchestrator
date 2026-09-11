# `phase2-cli-v1` evaluation fixture bundle

This directory is an offline, disposable protocol surface for the six-family
Phase 2 CLI evaluation. It contains no runtime payload, model policy, live
launcher, listener, scorer, reveal operation, or arm mapping.

The six frozen task families are:

| Task | Family | Independent acceptance oracle |
| --- | --- | --- |
| F-01 | factual investigation | exact `FACTS.md` facts and protected-file bytes |
| F-02 | bug fix/regression | retry de-duplication and conflict behavior |
| F-03 | multi-file feature | status module, test, documentation, and behavior |
| F-04 | test authoring | executable unittest module and unchanged source |
| F-05 | documentation/API correction | exact documentation statements |
| F-06 | security-sensitive change | raw-token non-disclosure and event retention |

Each prompt is a separate UTF-8 file. `trees/F-XX/` is the clean starting
snapshot. Reset is deterministic and non-overwriting:

```bash
python3 tests/fixtures/phase2_cli_v1/reset.py --task F-01 --destination /tmp/p2-f01
python3 tests/fixtures/phase2_cli_v1/oracle.py F-01 /tmp/p2-f01
```

The clean snapshot intentionally fails its completion oracle until the task is
implemented. A successful task oracle exits `0` and emits a JSON receipt; any
missing or incorrect acceptance property exits `1`. `USER-NOTE.txt` is protected
in every tree and must remain byte-for-byte unchanged.

`manifest.json` freezes prompt, tree, oracle, reset, and ledger-schema SHA-256
values. `manifest_tools.py` verifies those values and the tree hash definition.
`ledger_schema.json` defines opaque run and pair identifiers, condition-neutral
task identity, complete test receipts, UTC timestamps, null-safe cost/latency,
diagnostics excluded from quality, and later blinded scoring inputs. It contains
no arm/condition/randomization label or baseline/candidate mapping.

Run focused offline checks from the repository root:

```bash
python3 -m pytest -q tests/test_phase2_cli_fixture.py
```
