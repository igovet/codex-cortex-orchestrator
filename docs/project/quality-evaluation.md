# Format 11 instruction and hook pilot

The current pilot freezes baseline commit
`1a0988bdee5a0fe943e74df1746d2ae8ad1b161b`, payload
`fd0b4e63ad8eea97cd5bd39ce893675db5e2b0cc6fbcdc2c91fff4a35bf99345`.
It compares three source configurations: `baseline`, `compact_no_hooks` (compact
protocol without an active hooks manifest) and `full_hooks`. Each configuration
runs `stable-unique`, `retry-dedup`, `cancel-sort` and `resume-pagination` once:
12 ordinary end-to-end runs in total. The cancellation case changes one requirement
while preserving the others; the recovery case resumes the same native task.

Keep the same host and explicit Luna/high coordinator setting throughout this
pilot. By the user’s live-dev policy all native test workers also use Luna, at
medium or high effort. Heavy models are excluded, including from worker overrides. An isolated helper override
does not alter the user's saved model configuration. Freeze each actual payload
before its run, record its complete hash and never infer the ablation from its label.
All preparation/installations use that checkout's `scripts/cortex-dev` launcher.
Earlier Astra runs were stopped/excluded when the user changed the live model
policy; retain their diagnostics separately and restart the 12-run comparison on Luna.

The comparison uses one frozen compact/full pair after preliminary prompt tuning.
Earlier payload attempts remain separate evidence, including their failures. An
operator-intervened baseline retry had no complete native lifecycle duration; its
pre-intervention cutoff is not substituted for that missing measurement. A fresh
run of the same baseline case supplies the comparison record, while both attempts
remain retained and the intervention is disclosed.

One full-hooks cancellation attempt delivered the change during active initial
work, before an initial result had completed and been graded. It remains an
additional functional steering check. A fresh run with both completed phases
supplies the fair comparator; its earlier single-lifecycle duration is not used
to claim a speed improvement over the baseline's two completed phases.

```bash
python3 -B scripts/cortex_eval.py pilot-list
python3 -B scripts/cortex_eval.py pilot-prepare stable-unique /tmp/cortex-trial --configuration baseline
./scripts/cortex-live-smoke start --workdir /tmp/cortex-trial/project --model gpt-5.6-luna --effort high
```

Confirm trust, composer and matching initialization receipt before sending the saved
ordinary product prompt. Grade initial results before cancellation/recovery steering.
Keep controls and complete calls/events/audit receipts outside the worker project.
Review every call and result; preserve failed runs and resume segments. Follow the
exact transport procedure in [verification](verification.md).

`usage` reads all coordinator/worker native response receipts, deduplicates response
identity, and records input, cached input, cache-write input, output, reasoning and
total tokens separately. Wall time uses coordinator task lifecycle durations where
available, with a labeled response-span fallback. Missing telemetry remains null.
It is not valid to treat cached and uncached tokens as equal-priced input.

`pilot-record DIRECTORY OBSERVATIONS.json` combines independent grading with reviewed
protocol, lost requirements, claimed completion, recovery, steering/resume and usage
observations. It strips private thread identities from safe aggregates. False
completion is a claimed completion with failed functional checks; semantic omissions
not covered by those checks still need the separate lost-requirements review.
`pilot-adopt` can add a pilot overlay to an identical historical prepared fixture
without rewriting its original control file. `pilot-compare` reports all 12 cells,
including explicit nulls for missing runs, and refuses inconsistent trial identities.

Evaluate correctness, recovery and protocol separately. Measure handler subprocess
p95 against 100 ms and total hook overhead against 5% of task time. Compare compact
protocol with full hooks to isolate their additional effect. The compact configuration
also includes the storage and source-capture repairs, so its comparison with baseline
measures the combined replacement and cannot isolate prompt shortening alone. This small pilot can
find defects or nominate a larger comparison, not prove a general quality gain.
Current measured results and unrun checks belong in [release readiness](../release-readiness.md).

## Completed format 11 pilot (2026-09-06)

The fixed comparison is complete: four scenarios per configuration, 12 runs and
33 native participants. Every coordinator used Luna/high; every worker used
Luna/medium or Luna/high. Initial phases were completed and graded before cancellation
or recovery steering. All three recovery cases included visible manual compaction,
normal exit, same-task resume and the final product change.

| Configuration | Correct results | Protocol passes | Median total tokens | Median cached input | Median seconds |
| --- | --- | --- | --- | --- | --- |
| baseline | 4/4 | 1/4 | 3,032,297 | 2,827,392 | 489.384 |
| compact without hooks | 4/4 | 0/4 | 1,582,209 | 1,436,160 | 247.927 |
| full hooks | 4/4 | 0/4 | 1,662,731.5 | 1,513,728 | 267.526 |

Total tokens include cached input and all participants; they are not a monetary
cost estimate. [Per-run and per-participant measurements](hooks-pilot-results.json)
retain input, cached input, cache writes, output and reasoning output separately.
The full-hooks median used 45.2% fewer total tokens and 45.3% less time than baseline.
Against compact without hooks, its medians were 5.1% more tokens and 7.9% more time.
Those differences include model decisions and recovery work; they do not measure
Python hook overhead or establish causality from four unreplicated pairs.

No requirement loss or false completion was detected by the independent checks and
review. Protocol defects remain: missing model-facing command receipts, invalid
Cortex calls, and coordinator project work that belonged to a worker. In the compact
recovery run, the coordinator directly implemented the follow-up; in the full-hooks
recovery run, it delegated implementation but still repeated project verification.
Recovered project command failures remain visible separately. These outcomes do not
qualify the v4 protocol for release or demonstrate a general quality gain.

The frozen full-hooks payload is
`fd022bfc005647faab9ddde3ec5c493ea00773ce28b9389cf2ed355039796375`; compact is
`badf86046265b0360bc814593527d228f3feaff80fd72c036c5075985ff4101e`.
All 235 comparable source files match. The only differences are the active hooks
manifest and the generated `version` property, verified by comparing the remaining
plugin manifest fields. Thus this pair isolates source configuration, while baseline
comparison includes the storage/source repairs as well as instruction changes.

The operator-intervened baseline retry, earlier full-hooks tuning candidates and the
early-cancellation functional check remain excluded, with their failures and reasons
retained in the aggregate. The fair baseline retry and two-phase full-hooks cancellation
replace them in the fixed roster. Prompt-only candidates v5 through v9 are qualified
separately; their efficiency and host protocol status are not inferred from these v4
measurements. V9 subsequently passed consecutive bounded CLI/Desktop qualification
on one unchanged payload, with Luna/high coordinators and Luna/medium workers.
CLI used 1,309,671 total tokens (1,216,512 cached input) in 207.747 seconds; Desktop
used 1,314,999 (1,208,320 cached input) in 155.101 seconds. Both product checks and
Cortex audits passed. Two recovered project patch errors remain in the CLI trace;
Desktop had no tool errors. These two documentation scenarios do not replace the
12-run roster or establish a v9 efficiency gain. See
[exact qualification evidence and limits](../release-readiness.md).

---

## Historical comparative outcome evaluation

The baseline is commit `17ace1ce2f7e3c5bb3dcf2b2b16424a16db7d7d9`, payload
`cc786ae2fbd04cf1e9c29cfb34cf721de6ad6b8663f2d05f809baf2bee158698`.
No measured improvement is inferred from package tests or a clean protocol audit.

`scripts/cortex_eval.py` prepares 12 deterministic Python tasks, six tuning and six
held-out. They cover simple work, misleading bug symptoms, interacting contracts,
steering and restart. Grading runs the finished implementation against independent
input/output checks in a bounded subprocess and checks a protected user file.
Tests live outside the worker project. This is a small diagnostic suite, not a
general coding-intelligence benchmark; static checks do not assess README quality
or the full quality of agent-authored tests. An operator reviews those separately.

## Procedure

1. Keep the baseline payload frozen. Use a source checkout of that revision and
   only its `scripts/cortex-dev` launcher to prepare a baseline; never manually
   copy into a stable cache. Do not run candidates concurrently against the shared
   isolated home. Preserve private baseline results before changing the candidate.
2. Run three trials per task/configuration with fresh identical fixtures. Compare
   `baseline`, `evidence`, `hypotheses`, `reuse`, `routing`, then `combined`.
   Configuration labels do not toggle runtime policy: each isolated source candidate
   must actually contain only that change and have its hash recorded. There is no
   runtime flag, compatibility route or workflow engine for these experiments.
3. Use only the ordinary CLI/Desktop helpers. Observe the composer, trust screen
   and matching seven-tool initialization receipt before sending the product prompt.
   For steering tasks, finish and grade the initial request before sending the saved
   steering text. For recovery tasks, exit/stop, resume the same workdir with
   `--resume-last`, confirm the existing task, then send steering. Never expose the
   grader or teach the orchestration strategy in the workload.
4. Capture complete calls/events and audit, including failed trials. Review every
   call's necessity and evidence. Record tokens across coordinator and workers,
   elapsed task time (including required follow-ups), repeated source reads, protocol
   outcome and whether the coordinator claimed completion. Missing measurements are
   null, never zero. Keep raw evidence private; commit only safe aggregates.
5. Freeze choices using the tuning half before opening held-out outcomes. Change
   tasks only in a new suite version with a fresh baseline. Keep host and configured
   starting model/effort constant; changes made by the routing policy are measured
   outcomes. Test Codebase Memory availability consistently in each comparison.

Example fixture preparation (use a fresh private output directory):

```bash
python3 -B scripts/cortex_eval.py list
python3 -B scripts/cortex_eval.py prepare retry-dedup /tmp/cortex-trial-01 --configuration baseline --attempt 1
./scripts/cortex-live-smoke start --workdir /tmp/cortex-trial-01/project
# Observe capture/status, confirm a visible trust screen with enter, then confirm composer/events.
./scripts/cortex-live-smoke send --prompt-file /tmp/cortex-trial-01/prompt.txt
python3 -B scripts/cortex_eval.py grade /tmp/cortex-trial-01
```

For the graph-enabled route add `--codebase-memory` to CLI `start`. It enables only
the already configured isolated MCP and persists across helper resume. Ordinary
graph-disabled scenarios remain valid fallback checks. Desktop uses its isolated
configuration; record the actual tool availability instead of assuming parity.
For Desktop steering use the observed-composer procedure in
[verification](verification.md); never treat a prepared prompt as submitted.

`record DIRECTORY OBSERVATIONS.json` writes a new immutable `result.json` after
fresh grading. Observation keys are `tokens`, `seconds`, `repeated_reads` (positive
token/time values, nonnegative read count, or null); `protocol_pass`,
`claimed_complete`, `steering_observed`, `resume_observed` (booleans or null);
`payload_sha256`, `model_settings_sha256` (full lowercase digests); and `host`
(`cli` or `desktop`). These are reviewed developer measurements, not MCP contracts.
Never include commands, prompts, credentials or report bodies. `compare RECORDS.json`
takes an array of recorded results and refuses duplicate trials. Missing trials,
measurements, mismatched fixtures/hosts/settings or mixed candidate hashes remain
unverified. The original plan had 216 trials. On 2026-09-06 the user explicitly shortened
the series to 12: the first two cases across all six configurations, one attempt
each. The larger comparison function deliberately still returns unverified for
this smaller sample; its statistical criteria were not relaxed.

## Interpretation

Require at least 20% relative reduction in complex-task failures with median tokens
and elapsed time each no more than 25% higher, or at least 20% savings in both without
quality loss. Both tuning and holdout must meet the screen, with no paired functional
regressions, increased false completions or candidate protocol failures. The small
sample can only nominate a candidate for replication; even passing the threshold
returns `needs_replication`, never a claim of statistical proof. Unknown cases and
ambiguous outcomes are not evidence of improvement. Simple tasks stay in regression
coverage; report their cost separately when reviewing adoption.

This procedure supplements [protocol verification](verification.md), following the
distinction between outcomes and transcripts in
[Anthropic's evaluation guidance](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).

## Frozen comparison series (2026-09-05)

The main series uses ordinary CLI 0.153.0, a Luna/high coordinator, unchanged
initial worker routing defaults and Codebase Memory disabled for every treatment.
The graph route has separate real-host qualification; it is not a varying capability
in this experiment. All six source candidates were frozen before opening any
held-out result. Each ablation starts at the baseline and regenerates all profiles.
The evidence ablation includes consistent brief-reading boundaries and worker-owned
synthesis; hypotheses includes requirements-first independent verification; reuse
includes explicit assignment boundaries and fresh verification contexts; routing
contains only the model-selection changes. Combined is the previously qualified
full payload, including command-receipt and optional graph-routing clarifications.
Thus combined measures the complete release bundle, not a mathematically pure sum
of the four ablations.

| Configuration | Full payload SHA-256 |
| --- | --- |
| baseline | `cc786ae2fbd04cf1e9c29cfb34cf721de6ad6b8663f2d05f809baf2bee158698` |
| evidence | `f1702562af541cbf03f962ba5fc56cf1ce368765c7c1f317ca2ae352305baea1` |
| hypotheses | `5ad87ca94e8575604e902ab5ced183ecaf66eefe0033dcf6d99fa45002facca4` |
| reuse | `b65b7adfdc8d0009ee65e39d5ca693b63b9d7b6cd515fff5ebc4563719546d3c` |
| routing | `97859e280b96be8cdb3072b733b37800b554b5fc337e1f2fbe9701ec9aab78c6` |
| combined | `6a789c1d6d2eb0ef4a33532c7b9df1fc08be43997cc3b9024d60505fc4abf219` |

All candidates passed profile generation, stamping, package validation and source-only
sync checks sequentially. Shared observation helpers live outside the plugin payload.
Their findings are reviewed against actual calls and the candidate's own policy;
false-positive flags remain in private raw evidence with an explicit resolution.

Token totals count each native response once across coordinator and workers and are
cross-checked against the final cumulative thread counters. They include cached input;
cached input is also retained separately and must not be interpreted as equal-priced
uncached tokens. Duration sums coordinator product-turn durations, including required
clarification turns, and excludes operator waiting and launcher preparation. Repeated
reads count explicit code-file retrievals beyond the first retrieval of the same
unchanged revision, across native workers. A changed or newly created file starts
a new revision; its first retrieval does not count as repeated. Count files, not
commands: rereading two unchanged files adds two. Exclude documentation, protected
data checks, instruction/report loading and normal test/compile execution. This
measures work overlap, including independent review, not necessarily avoidable work.

Safe per-trial aggregates are retained in [quality-evaluation-results.json](quality-evaluation-results.json).
Missing trials remain missing; early observations do not establish a quality gain.
Raw transcripts, source fixtures, reconstruction patches and grader controls stay in
owner-private storage outside the repository. Historical qualification pilots are
excluded from this series.

## Shortened series decision (2026-09-06)

All 12 attempts were completed and retained, including failures. Each configuration
solved both small tasks under the independent output/preservation checks. Seven
attempts met the reviewed protocol requirements; five did not. These historical
protocol labels use the frozen candidates' then-current instruction-read policy,
not the stricter prohibition introduced after the series.

| Configuration | Correct results | Protocol passes | Median gross tokens | Median seconds |
| --- | --- | --- | --- | --- |
| baseline | 2/2 | 1/2 | 1,883,406 | 294.694 |
| evidence | 2/2 | 1/2 | 1,750,263 | 337.430 |
| hypotheses | 2/2 | 1/2 | 1,489,831 | 267.346 |
| reuse | 2/2 | 1/2 | 1,812,819 | 346.123 |
| routing | 2/2 | 2/2 | 1,924,910 | 367.664 |
| combined | 2/2 | 1/2 | 1,584,661 | 319.188 |

The combined bundle used about 15.9% fewer gross tokens but took about 8.3% longer
than baseline across these two cases. It also failed one protocol audit. No variant
establishes a significant quality improvement: there were no functional failures to
reduce, no repeated attempts, and no holdout, steering or recovery cases in this
shortened series. These medians are descriptive, not estimates of general performance.

Decision: stop broad comparative testing at the user's requested reduced scope;
do not release or claim an intelligence/efficiency gain. Prioritize exact reference
handoffs, prohibited plugin access and effective capacity handling. Later source
changes have a different payload and are not retrospectively covered by this series.
The additional [host compatibility findings](host-compatibility.md) independently
block claiming strict marketplace execution on the inspected V2 hosts.
