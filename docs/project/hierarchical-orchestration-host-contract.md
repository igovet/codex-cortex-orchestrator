# ADR: hierarchical orchestration host contract (Stage 00)

Status: **NO-GO** (blocking hard gate)

Date: 2026-08-20

Scope: source-mode research only. This record does not authorize a production
hierarchy runtime, a plugin installation or update, a commit, a push, or a pull
request.

## Decision

Do not implement thread-based orchestration-of-orchestrations. The current
source and available host evidence do not prove a native `create_thread`
contract that can create a child coordinator on exact `gpt-5.6-terra`, pass a
selected `reasoning_effort`, and attest the effective model and effort without
silent substitution. This NO-GO blocks every later hierarchy stage.

A predecessor-recorded Desktop root-thread observation is a deliberately
limited receipt: it records that the root selection was requested as
`gpt-5.6-terra` with `xhigh` and was confirmed in the Desktop UI. It does not
record a child-coordinator create response, an effective-value field, or any
child lifecycle event. It therefore cannot be promoted to evidence for the
native child contract in this ADR.

The required child-coordinator contract is exact and has no implicit fallback.
The only bounded coordinator efforts accepted by this Stage 00 harness are
`low`, `medium`, `high`, and `xhigh`, each requested once. `max` is policy-only
and is rejected; missing, malformed, duplicate, unsupported, or substituted
effort is a hard failure:

```text
native create_thread
  model                 = gpt-5.6-terra
  reasoning_effort      = one of low|medium|high|xhigh, requested once
  title                 = durable, non-secret title
  environment           = local or an explicitly attested worktree
  response.thread_id    = stable native identity
  response.effective_model          = gpt-5.6-terra
  response.effective_reasoning_effort = requested effort
```

Every field above must be observed from the native host or guaranteed by an
official native schema that expressly rules out substitution. A Python request
dictionary, Cortex policy metadata, a fake adapter, configured default, or
missing observation is not an effective-model or effective-effort attestation.
`gpt-5.6-luna`, `gpt-5.6-sol`, and a configured default are all failures for
this contract; they are never accepted as fallback coordinators.

The existing hidden `spawn_agent` routing and the existing Luna-only
visible-thread behavior remain unchanged. A different hierarchy that avoids
separate native orchestration threads is a distinct architecture and requires
separate user approval; it is not an automatic fallback from this NO-GO.
This ADR contains no runtime routing, lifecycle authorization, or later-stage
authorization.

## Evidence boundary and source inventory

The development-only probe at
[`scripts/cortex-hierarchy-host-spike.py`](../../scripts/cortex-hierarchy-host-spike.py)
reads source constants through an isolated AST/JSON inspection seam. It does
not import or execute the production Cortex runtime. Its deterministic static
result is currently `status=FAIL`, `decision=NO-GO`, and
`support_evidence=false`:

| Item | Current source observation | Meaning |
| --- | --- | --- |
| Policy model set | `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna` | Cortex policy vocabulary only; not a native thread catalog |
| Policy effort set | `low`, `medium`, `high`, `xhigh`, `max` | Policy vocabulary only; not proof that `create_thread` accepts any value |
| Configured default | `gpt-5.6-luna` in `plugins/cortex/profiles.json` | Explicitly forbidden as Terra evidence |
| Native create-thread model catalog | `gpt-5.6-luna` in `_v3_host_capabilities()` | Terra is not declared by the current source projection |
| Native-argument helper inventory | The create-thread branch initializes `prompt`, `title`, and `target`; a separate conditional source path adds `model` | The full inventory is conditional, not a native signature. `model` is not proven on every relevant path, and `reasoning_effort` is not declared by the create-thread branch. |
| Effective model observation | unavailable | Blocking unknown; request metadata cannot substitute for it |
| Effective effort observation | unavailable | Blocking unknown; policy metadata cannot substitute for it |

The AST seam records a deterministic, sanitized candidate inventory for every
reachable conditional return and matching top-level declaration. It marks a
field `literal` only when every candidate safely decodes to the same value.
Literal alternatives are reported as `conditional`; a dynamic expression is
reported as `dynamic`; and a missing field on a reachable return is reported as
`unavailable`. These states preserve every safe candidate value but are
non-affirming: a partial model catalog or argument list can never establish
native support. The static result remains deterministic
`FAIL`/`NO-GO`/`support_evidence=false` regardless of that source projection
or of any future literal alternatives.

The JSON also marks each native capability as `UNPROVEN`: model, effort,
identity, lifecycle, worktree ownership, recovery, and child-worker receipts.
Source declarations and the candidate inventory are source-only evidence, not
a native host contract. A separate native evidence package is still required
(none is available in this checkout).
Source projection, fake-host `PASS`, and live `SKIP` are never native `GO`
evidence.

These facts are grounded in the current source ranges
`plugins/cortex/scripts/cortex.py:650-710` and
`plugins/cortex/scripts/cortex.py:9327-9351`. The existing delegation policy
also reserves `visible_thread` for the Luna route and requires the exact native
thread catalog (`plugins/cortex/scripts/cortex_runtime/delegation.py:90-102`).
The source policy's Terra effort mapping is useful for request intent, but it
does not establish a native argument name or effective-value attestation.

## Host calls and required arguments

The following is the minimum contract a future native adapter must expose. The
names are a contract inventory, not a claim that the current host exposes them.

| Operation | Required safe inputs | Required observations |
| --- | --- | --- |
| Create child coordinator | exact model, exact effort, title, `local`/`worktree` target, bounded parent correlation | durable `thread_id`, title, environment, effective model, effective effort |
| Spawn child worker | exact child `thread_id`, bounded worker request | durable worker identity and a non-secret lifecycle receipt |
| Follow up | exact child `thread_id`, bounded follow-up payload | accepted/rejected outcome bound to that thread |
| Resume after question/compaction/restart | exact child `thread_id` and durable revision/cursor | resumed identity and no cross-thread substitution |
| Observe completion/failure/question/termination | exact child identity | terminal status and bounded reason class |
| Cleanup | adapter-owned temporary resources only | complete cleanup attestation |

Prompts, tokens, reports, raw stderr, and private host metadata must not enter
the probe output. The durable identity required by Cortex is the native
`thread_id` returned by the host and re-attested on every follow-up/resume;
displayed UI labels alone are insufficient. A lost create response is a
terminal failure until the host provides an idempotent, identity-bound lookup
that is itself part of the official contract.

## Probe and fake-host contract

The probe's `run_host_contract()` adapter seam is deliberately stricter than a
request builder. A native PASS requires exactly four distinct thread IDs and,
for every requested effort, all of these exact correlated receipts:

1. a non-empty, safe, distinct thread ID;
2. observed effective model exactly equal to Terra;
3. observed effective effort exactly equal to the requested supported effort;
4. exact title and environment attestation;
5. child-worker spawning and worker identity;
6. follow-up acceptance bound to that thread;
7. resume bound to that thread;
8. completion observation bound to that thread;
9. failure observation bound to that thread;
10. question observation bound to that thread;
11. termination observation bound to that thread; and
12. cleanup correlated to exactly the created thread IDs.

Any missing, cross-thread, duplicate, substituted, malformed, or unsanitized
observation is `FAIL`. A bounded timeout is `FAIL`. Before allocating either
end of a `Pipe`, a `Process`, or calling `start()`, the harness accepts only a
finite, non-boolean numeric timeout greater than zero and no greater than its
named development-only cap of 60 seconds. Conversion failures, strings,
booleans, NaN, infinities, non-positive values, and values above that cap all
produce the same sanitized `FAIL`/`NO-GO` `invalid_timeout` result without
selecting an adapter. The default is the valid finite value of ten seconds.

The positive deterministic fake-host case creates four distinct synthetic
thread IDs, checks each of the four bounded coordinator effort cases once, and
returns `PASS` only after all lifecycle checks pass. That fake-host `PASS`
demonstrates only that the harness detects a complete adapter response; it is
non-native evidence, never native GO evidence, and does not prove native
Desktop behavior.

### Fake-adapter deadline boundary

The complete fake-host evaluation runs in one code-controlled `fork` child
process. This preserves the adapter's state for every create and lifecycle
call in that one child while giving the parent a real receive deadline. Once
`start()` has returned, one non-throwing parent finalizer owns both Pipe ends
and the child process. It attempts both endpoint closes and a bounded join; if
the child is still alive, liveness is unavailable, or the join cannot be
verified, it continues with bounded `terminate`, another join, bounded `kill`,
and a final bounded join. A failure in a close, liveness check, terminate,
kill, or join never skips later feasible attempts and never escapes the
probe.

The finalizer reports verified cleanup only when every required OS operation
and liveness check succeeds. If an operation or liveness check fails, the
result remains sanitized `FAIL`/`NO-GO` with `cleanup_unverified`; it does not
claim that the child was reaped. On a blocked adapter call, the parent returns
the separate `bounded_timeout` and `cleanup_incomplete_after_timeout` reason
classes after the same finalizer has made its bounded attempts. The parent
never calls adapter cleanup after a blocked call, because that could enter the
same hung adapter; adapter cleanup is deliberately not attested in that case.

The harness fails closed with `process_isolation_unavailable` and
`cleanup_disabled` before selecting or calling an adapter if a safe `fork`
boundary is unavailable. Invalid or malformed child-result transport also
returns `FAIL`/`NO-GO` with `cleanup_unverified`; it is not retried and never
falls back to a thread-based timeout. These cancellation and cleanup outcomes
are fake-host harness evidence only. They provide no native Desktop lifecycle,
model, or effort evidence.

The static `--json` mode is safe to run in any checkout and always emits a
machine-readable NO-GO for the current unsupported native contract. The
`--live --json` mode is a deliberately disabled, non-evidence boundary: it does
not read `CORTEX_HIERARCHY_HOST_COMMAND` or any other environment-derived
executable command, does not select a host adapter, and never invokes a
process-launch API. It deterministically returns `SKIP`,
`support_evidence=false`, and `decision=NO-GO`; a SKIP is never promoted to
PASS. No environment-controlled executable is admitted or run by the Stage 00
probe. Any future native adapter requires separate user approval, a code-bound
integration, and real/native-schema evidence; environment configuration cannot
authorize one.

## Future native-evidence protocol (not run)

This is a future proof protocol, not permission to invoke a host today. It has
two independently evaluated modes: Codex Desktop and equivalent CLI-host mode.
Each mode has a hard limit of **one** isolated visible child-coordinator test.
Before that one creation, the native host or an official native schema must
expose the exact `model` and `reasoning_effort` request parameters and an
inspectable, host-attested `effective_model` and
`effective_reasoning_effort`. The requested and effective values must match
exact Terra and the same supported mandatory effort. If this preflight is
absent, incomplete, or mismatched, record terminal NO-GO for that mode, create
no child, and do not retry, substitute a model or effort, or select a hidden
subagent route.

For an allowed creation, use a code-bound source-mode adapter and isolated
temporary `HOME` and `CODEX_HOME`; never install or update a user plugin. Keep
only this safe, correlated record for the one child:

| Required record | Required exact value or receipt |
| --- | --- |
| Mode and correlation | `desktop` or `cli-host`, plus a bounded non-secret parent/request correlation |
| Requested and effective routing | requested `gpt-5.6-terra` and supported effort; host-attested identical effective model and effort |
| Durable child identity | non-empty native `thread_id`, exact title, and host-attested `local` or `worktree` environment |
| Lifecycle and recovery | child-worker identity, follow-up, resume after question/compaction/restart, completion, failure, question, and termination receipts bound to that `thread_id` |
| Timeout and cleanup | finite deadline result and verified cleanup receipt for adapter-owned temporary resources |

The report must not include a prompt, token count, report contents, raw stderr,
or private host metadata. An absent or cross-thread receipt, lost create
response, host failure, unsafe output, timeout, unverified cleanup, unavailable
child-worker operation, or missing native capability is terminal NO-GO for that
mode. There is no Luna/Sol/default, metadata-only, repeat-attempt, or automatic
fallback. Stage 00 may be revised to GO or CONDITIONAL GO only when both modes
independently have every exact PASS receipt above; a fake-host PASS, source
projection, or disabled-live SKIP cannot satisfy either mode.

## Current Stage 00 preflight receipts

This execution recorded only sanitized capability outcomes. It did not retain
configuration values, prompts, token counts, host-private metadata, or raw
stderr. CLI-host and Desktop evidence are separate ledgers: neither mode may
borrow a request, effective-value, or lifecycle receipt from the other.

| Mode | Safe preflight result | Required native receipt status | Terminal disposition | Child creations / retries |
| --- | --- | --- | --- | --- |
| CLI-host | `codex exec --help` documents the model selector and a generic configuration override. It does not document a native child-thread operation or a dedicated, host-attested reasoning-effort input. | No documented `create_thread`, `effective_model`, or `effective_reasoning_effort` output field was available. A generic configuration parser is not an accepted-effort or effective-value receipt. | **NO-GO before invocation.** The one permitted Terra/xhigh equivalent probe was not run because its mandatory effective-value preflight failed. No fallback or rerun is allowed. | `0 / 0` |
| Desktop | A predecessor-recorded root-thread receipt separately establishes only the requested, UI-confirmed Terra/xhigh selection. In this execution context, the available Desktop tool schema does not expose the required native `create_thread` request/response contract. | The root receipt contains no child-coordinator response, effective model/effort attestation, child `thread_id`, child-worker, lifecycle, deadline, or adapter-cleanup receipt. The available schema likewise cannot attest request `model` plus `reasoning_effort`, title/environment, and response `thread_id`, `effective_model`, and `effective_reasoning_effort` in one native operation. | **NO-GO before child creation.** The root receipt is not a child creation. Do not substitute a hidden worker, a collaboration route, a different model, or a different effort. | `0 / 0` |

The requested values for either future native mode remain exactly
`gpt-5.6-terra` and `xhigh`. The requested value is deliberately not recorded
as an effective value here: no host attestation exists. No native child,
worker, follow-up, resume, terminal lifecycle, deadline, or adapter-cleanup
receipt was produced because neither mode passed preflight. The absence of a
receipt is a terminal NO-GO, not a retry opportunity.

The CLI discovery and the Desktop capability inspection are not a live native
scenario. The harness's static `FAIL` and disabled-live `SKIP` outcomes remain
separately labelled non-native evidence. The four-thread fake-adapter PASS
remains test coverage for receipt validation only; it neither consumes nor
authorizes a Desktop or CLI child creation. Consequently both mode ledgers are
incomplete and the Stage 00 decision remains **NO-GO**.

### Current revalidation receipt (2026-08-20)

This source-mode revalidation ran `codex exec --help` and `codex --version`
against Codex CLI `0.147.0`. The CLI documents `-m` for a model request and a
generic `-c` configuration override, but it does not document a native child
thread operation or a host-attested `effective_model` or
`effective_reasoning_effort` output. The bounded CLI model-invocation allowance
therefore remained unused: `child_creations=0` and `retry_count=0`.

The current Desktop worker capability catalog likewise contains no
`create_thread` schema exposing the required `model` and `reasoning_effort`
inputs together with `thread_id`, `effective_model`, and
`effective_reasoning_effort` response fields. No Desktop create operation was
performed, so its result is also `child_creations=0` and `retry_count=0`. This
is compatible with, but does not extend, the predecessor-recorded root-thread
selection receipt: that receipt remains requested Terra/xhigh UI context only,
not a child effective-value or lifecycle attestation.

No prompt, configuration value, token count, raw stderr, or private host
metadata is retained in this receipt. The static probe stays `FAIL`/`NO-GO` and
the deliberately disabled live probe stays `SKIP`/`NO-GO`; neither constitutes
native evidence or an alternate route around the hard gate.

## Hard-question disposition

| Stage 00 question | Disposition | Blocking evidence gap |
| --- | --- | --- |
| Is native `create_thread` available to the root coordinator? | **PARTIALLY OBSERVED / NO-GO** | A predecessor-recorded root Desktop thread establishes limited requested-selection context, but current source projection and this execution context provide neither a native `create_thread` schema nor the required effective-value and child-lifecycle receipts. |
| Can a child thread invoke Cortex and then spawn a worker? | **UNKNOWN / NO-GO** | No native parent-to-child-to-worker trace is available. |
| How does a parent observe child completion/failure/question/termination? | **UNKNOWN / NO-GO** | No official completion or event contract is available. |
| Can follow-up target the exact child after question or compaction? | **UNKNOWN / NO-GO** | No identity-bound follow-up/resume evidence is available. |
| Does thread identity survive Desktop restart? | **UNKNOWN / NO-GO** | No durable identity/restart proof is available. |
| Is thread ID bound to bootstrap rather than only UI? | **UNKNOWN / NO-GO** | Native response/schema is unavailable. |
| Can four threads be created in one model turn? | **UNKNOWN / NO-GO** | No bounded concurrency evidence is available. |
| Is `worktree` supported and who owns its lifecycle? | **UNKNOWN / NO-GO** | No native environment observation or cleanup authority is available. |
| Can child creation explicitly request Terra? | **NO** for current source projection | Current catalog declares Luna only. The renderer's conditional `model` insertion is source-only and cannot prove native acceptance. |
| Can child creation pass and attest effective effort? | **NO** for current source projection | The create-thread branch does not declare a reasoning-effort argument, and no effective observation exists. |
| Can silent model/effort substitution be detected? | **UNKNOWN / NO-GO** | A native effective observation or official guarantee is missing. |

The explicit negative source findings and every unresolved native question remain
blocking. No planner, writer, fake-host test, static request projection, or
skipped live probe may waive them.

## Security and compatibility

The probe is development-only and standard-library-only. It uses source text
inspection instead of importing `plugins/cortex/scripts/cortex.py`, so it cannot
open the Cortex SQLite ledger or call public MCP operations. The validated
static and disabled-live paths emit fixed reason classes and bounded safe
identifiers; neither path carries a native host receipt. A current
development-only limitation is that the invalid-programmatic-environment
failure branch preserves a caller-provided string in its machine-readable
request record. That path is not an accepted sanitized receipt, must never be
used with sensitive input, and remains a non-native NO-GO; correcting its
redaction requires a separate change to the probe and its regression tests.
The child-process boundary is restricted to the fake-host test seam and uses no
environment-derived executable. The disabled live mode itself starts no
process, reads no environment-derived command, and performs no host or
filesystem adapter setup.

No production hierarchy runtime, SQLite schema, public MCP schema, hook, skill,
installed plugin, global configuration, or network state is changed by this
Stage 00 artifact. Existing hidden worker dispatch remains the supported
internal path. Existing explicit visible threads remain Luna-only under the
current policy. Rollback is deletion of the development-only script, test
module, and this ADR; no runtime migration is required.

## Go/No-Go exit rule

This ADR has one decision: **NO-GO**. A future revision may change it only when
one bounded native-host or official-native-schema evidence package proves all
of the following in the same contract: exact Terra request and effective
Terra observation, explicit supported effort and effective-effort observation,
durable child identity, completion/failure/question/termination observation,
identity-bound follow-up and resume, child worker spawning, environment
ownership/cleanup, lost-create handling, and bounded concurrency. Until then,
all later hierarchy stages are blocked.

## Verification record

The Stage 00 harness and tests were run in source mode with bytecode disabled:

```text
codex exec --help
codex --version
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v tests.test_hierarchy_host_contract
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/cortex-hierarchy-host-spike.py --json
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/cortex-hierarchy-host-spike.py --live --json
```

The fake-host suite covers the positive and negative contract cases, complete
conditional AST inventories, pre-start rejection of invalid, non-finite, and
over-cap timeouts, bounded blocked-adapter cancellation, poll/receive and
`BaseException` containment, endpoint/process/liveness fault injection,
isolation unavailability, malformed result transport, and the disabled-live
no-launch invariant. The static probe emits the expected
`FAIL`/`NO-GO`/`support_evidence=false` result, while live mode emits
`SKIP`/`NO-GO`/`support_evidence=false`; both expose only safe fields. Neither
result is evidence that a native Desktop operation was attempted.
