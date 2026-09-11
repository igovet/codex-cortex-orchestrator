# Phase 2 controller fixture

This is a private, offline contract fixture. It does not launch Codex, a live
helper, tmux, a browser, a matrix, or a scoring operation, and it does not
import the product runtime. The fixture models only the repaired boundaries:

1. A disposable repository has a deterministic baseline commit and a successful
   `git log -1` receipt while its content tree remains byte-for-byte unchanged.
2. The sealed `row_id` is the sole source for `cells/<row_id>/project`; an
   injected path mismatch is recorded as a pre-launch failure.
3. A pending wait produces an audit exit of 1 and a stopped cleanup receipt;
   exactly one terminal wait is required for an accepting audit. Missing,
   duplicate, and nonterminal waits fail closed.
4. The audit wrapper is fail-closed for nonzero, pending, malformed, or path
   mismatch receipts.

Run with an explicitly new disposable directory to preserve reviewer evidence:

```text
python3 tests/fixtures/phase2_controller/controller_fixture.py --output /tmp/phase2-controller-fixture
```

The output directory is mode `0700`, receipt/snapshot files are mode `0600`,
and contains `manifest.json`, `receipts/`, `snapshots/`, and the disposable
`cells/<row_id>/project` tree. The command refuses an existing output path.
