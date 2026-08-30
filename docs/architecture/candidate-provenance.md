# Isolated candidate provenance

The live-dev runtime must prove which source payload is being executed before
ordinary Codex starts. The product and server version is `1.12.1`; the cache
identity is a separate content address:

```text
1.12.1+codex.sha256.<16 lowercase hexadecimal characters>
```

## Canonical identity

`scripts/cortex_release_candidate.py` derives the installable manifest from
the exact packaged plugin tree and its validated import/document closure. Each
manifest record contains the repository-relative path, normalized byte count,
and SHA-256 of the bytes. Records are sorted and serialized as compact,
deterministic JSON before the aggregate SHA-256 is computed.

The plugin manifest is generated with the cache version, so hashing it raw
would be self-referential. During identity calculation only, its `version`
field is replaced by the product version (`1.12.1`) and the normalized JSON is
hashed. The source and installed plugin therefore have the same canonical
identity even though the installed directory name contains the derived cache
suffix. No other file is normalized.

The candidate path is immutable. If the derived path already exists, sync
must verify its complete regular-file set and canonical digest; a missing,
extra, symlinked, or modified file is a collision/tamper failure and the path
is never overwritten.

## Launcher gate

`scripts/cortex-dev` refreshes only `$HOME/.cortex-dev/.codex`. Before it
executes Codex it locates the exact content-addressed cache path, compares the
source plugin digest with the installed plugin digest, and prints the product
version, build ID, both digests, parity result, and exact candidate path. Any
failure terminates before Codex launch. The stable user profile is outside the
workflow.

The launcher exports the verified build ID and digests. The MCP initialize
response exposes them as additive read-only `serverInfo` fields, allowing a
black-box client to correlate a live process with the candidate that passed
the gate.

The candidate also verifies itself at process startup and immediately before
answering `initialize`. The runtime scans the actual plugin directory
containing the running entrypoint, rejects symlinks and non-regular files,
uses the same canonical normalization/digest implementation as the builder,
and checks that the manifest's `1.12.1+codex.sha256.<prefix>` suffix matches
the computed content. Launcher-provided build/source values are expectations
only; they cannot override the computed identity. The reported candidate path
is derived from the running package, never copied from an environment
variable. If the package is tampered with after process start, the initialize
gate exits before exposing a usable MCP session.

## Qualification

`tests/test_candidate_provenance.py` launches the exact candidate entrypoint
as a separate stdio MCP process. It exercises `initialize` and `tools/list`,
checks the complete advertised tool catalogue, and verifies the runtime build
identity. The same
test family checks deterministic hashing, modified bytes, and unexpected
installed files, root symlinks, and environment isolation. It does not import
the checkout runtime into the candidate process and does not use the stable
profile or perform a live cache refresh. Runtime self-verification covers
spoofed launcher values, invalid build suffixes, and post-build package
changes before `initialize` can succeed.

The current Phase B gate has been exercised on the working tree without a
live run or user-cache access: candidate provenance, sync workflow, marketplace
gate, semantic-registry compatibility, and Phase C architecture acceptance
tests passed (`22 passed`); marketplace source validation passed; shell syntax,
`git diff --check`, and the no-`.pyc` check passed. The sync regression also
confirmed that the checkout remains at base `1.12.1` while the installed test
payload uses a separate stamped candidate path.

## Runtime payload closure — 2026-08-29

The installable Python payload is governed by the single
`plugins/cortex/runtime-payload.json` manifest. It declares the launcher and
every production module under `scripts/cortex_runtime/`, including
`filesystem_policy.py`. Candidate construction derives the expected module set
from the actual runtime directory and compares it with that manifest before
copying any file. A missing declaration, an undeclared module, a duplicate,
unsafe path, missing file, symlink, or non-regular file fails closed. The
marketplace validator reads the same manifest and applies the same exact-set
check to both source and candidate trees.

This closes the packaging root cause: a source module can no longer be used by
tests or production policy while being absent from the candidate closure. The
runtime payload manifest is itself part of the immutable candidate payload, so
the complete closure participates in the deterministic content-addressed
digest. Same source content produces the same build ID; an extra or missing
runtime module cannot be silently absorbed by an import-discovery heuristic.

## Shared closure and topology gate — 2026-08-29

The closure implementation is centralized in
`scripts/cortex_payload_manifest.py` and is consumed by both the candidate
builder and marketplace validator. It recursively enumerates every production
Python file below `scripts/cortex_runtime/`, including nested packages, and
requires the corresponding package initializers. The declared manifest and the
discovered set must match exactly; duplicates, unsafe paths, missing files,
symlinks, non-regular entries, and undeclared nested modules fail closed.

The same boundary validates repository, direct candidate, and plugin roots
with `lstat` before any absolute-path derivation. Candidate validation also
compares the complete directory topology, so an extra empty directory or an
otherwise undeclared directory cannot be smuggled into an immutable candidate.
The manifest and topology are included in the deterministic candidate payload
identity. Focused regressions cover nested module/symlink/non-regular cases,
missing files/directories, root symlinks, extra empty directories, marketplace
parity, and same-source determinism.

## Remaining path-boundary review finding — 2026-08-29

The candidate-builder and direct candidate-tree checks now reject a symlinked
candidate root, but the installed version-directory boundary is not yet
equivalent. `plugin_tree_digest()` validates the final `plugins/cortex` path;
an alias at its version-directory parent is therefore transparent to that
function. The synchronization script also resolves the installed path before
its tree comparison. A targeted temporary-candidate probe still accepted a
symlinked version root, so packaging clearance remains blocked until the
version directory and `.cortex-candidates` staging root are checked with
`lstat` before any resolution or reuse. The release builder enforces exact
plugin directory topology, but a temporary source tree with an extra empty
`plugins/cortex` directory still passed `validate-cortex-marketplace.py`.
Marketplace validation must consume the same exact plugin-tree topology gate;
until it does, the two packaging paths are not equivalent and packaging
clearance remains blocked.

## Trusted managed-path re-review — 2026-08-29

The shared `scripts/cortex_payload_manifest.py` lstat chain correctly validates
the staging root, candidate version path, direct candidate/plugin roots, and
missing-directory creation. Marketplace validation now consumes the exact
plugin directory-topology helper and rejects undeclared empty directories.
However, the synchronization reuse path still resolves the installed cache
path before its comparison (`scripts/sync-cortex.sh`), so a symlinked cache
version parent remains a P1 bypass until that call site validates the lexical
ancestor chain first. The content-addressed payload itself remains immutable
and includes the canonical runtime manifest and topology-relevant file
closure, but packaging clearance is withheld pending the sync-path fix.

## Final managed-path clearance — 2026-08-29

The sync reuse path now validates the lexical repository and installed-version
ancestor chains with `lstat` before reading or comparing them; it no longer
resolves a managed path before validation. Real isolated sync regressions reject
symlinked `.cortex-candidates` and installed-version parents, while a second
isolated sync reuses an unchanged content-addressed candidate without a second
plugin installation. The focused provenance, sync, and marketplace gates pass
with zero failures and zero skips. Packaging is source-cleared within the
managed path/package-invariant scope; the exact Decision candidate and
LLM-driven live-dev gates remain separate and unrun.

## Final candidate identity evidence — 2026-08-29

The repository-supported isolated sync path staged and validated candidate
`1.12.1+codex.sha256.eb691a9a49377dcc` from base version `1.12.1`:

```text
source/candidate digest: eb691a9a49377dcc24640d415c5fa38d8e94b7cb33c104277443c5c3004c453f
release validation: passed; files=94
marketplace validation: passed
MCP initialize parityVerified: true
```

The exact-candidate Phase D run removed checkout `PYTHONPATH` and
`CORTEX_SOURCE_MODE`, used isolated `HOME`/`CODEX_HOME`, and ran with
`PYTHONDONTWRITEBYTECODE=1`/`python3 -B`. The complete suite passed 11/11,
including the exit-aware 80-pair stdio stress. No child crash, forced
termination, hidden EOF, stderr, duplicate receipt/mutation, SIGBUS, or
Python-side WAL/SHM sidecar was observed. Candidate provenance is therefore
verified for the Decision vertical slice; focused LLM-driven live-dev remains
the next gate and has not been run.

## Isolated marketplace reconciliation — 2026-08-29

Content addressing intentionally changes the marketplace root whenever the
candidate payload changes. Codex correctly refuses a second registration of
the same marketplace name from a different root. The old refresh path always
issued an add, so a pre-existing isolated registration made live-dev fail
before ordinary Codex started.

`scripts/cortex-dev` now authorizes a single reconciliation boundary after it
has established the exact lexical isolated target. `scripts/sync-cortex.sh`
independently checks that `HOME` is exactly the owner home's `.cortex-dev`
directory, that `CODEX_HOME` is exactly its `.codex` child, and that neither
managed ancestor is symlinked. Only then does it inspect the native marketplace
list and converge the one `cortex` entry:

- an exact current candidate root is reused without a native mutation;
- a missing entry is added through the native CLI;
- a different entry is removed by the exact `cortex` name and replaced through
  the native CLI.

No configuration file is patched directly. Unrelated isolated marketplaces are
not enumerated for mutation, and a stable profile, a mismatched target, or a
symlinked managed path is refused before any marketplace command can run. The
normal source-sync workflow has no reconciliation authority. Candidate cache
installation and the existing source/candidate parity comparison still run
after the native registration is converged.

Focused isolated regressions cover a stale different source, exact-source
reuse, a missing entry, preservation of an unrelated marketplace, symlinked
target refusal, and main-profile refusal. This is delivery-infrastructure
evidence only; the focused live decision check must still prove the ordinary
interactive process.

## Authoritative installed-candidate receipt — 2026-08-29

The cache path is selected by the native isolated installation, not by the
semantic product version. `scripts/sync-cortex.sh` therefore writes one
machine-readable, canonical JSON receipt only after the isolated installation
and exact installed-package parity verification both succeed. It is stored as
`$CODEX_HOME/.cortex-candidate-receipt.json`, is atomically replaced with a
durable owner-only (`0600`) file, and contains no credentials or diagnostics.

Its fixed schema binds one exact isolated target to the exact stamped candidate
version and lexical installed cache path, source digest, candidate digest,
build ID, base version, and a digest of the canonical unsigned receipt. The
receipt is not a cache index and is never a latest-version scan. Equal source
content produces byte-identical receipt content and reuses the same immutable
candidate directory.

`scripts/cortex-dev` first invokes the supported isolated sync, then consumes
all launch identity values from that receipt. It does not construct a cache
directory from `1.12.1`, scan cache directories, or accept an unstamped alias.
Before ordinary Codex is executed it rejects a missing, malformed, tampered,
foreign-isolation, non-owner-only, symlinked, non-lexical, out-of-managed-path,
modified, or parity-mismatched receipt/candidate. It prints both the stamped
candidate version and receipt path together with the existing provenance lines.
Receipt-commit failure makes sync fail even if the native installation itself
succeeded, so a stale receipt can never authorize a launch.

Focused isolated regressions cover stamped-path consumption, deterministic
same-source reuse, absence of base-version cache reconstruction, missing and
tampered receipts, cross-isolated receipt copying, receipt and cache-ancestor
symlinks, and post-install receipt-write failure. These are source/fake-native
CLI delivery checks only. The real ordinary-Codex/tmux live gate remains
unrun and is still the only evidence that can promote live-dev status.

## Delivery manifest versus installed plugin payload — 2026-08-29

There are two strict but non-interchangeable manifest scopes. The repository
delivery manifest includes the marketplace registration, public documentation,
and support scripts needed to stage and validate a release candidate. Codex's
installed cache intentionally contains only the `plugins/cortex` payload. An
installed-cache verifier must therefore never require repository-only delivery
files such as `.agents/plugins/marketplace.json` to exist below the plugin
cache root.

The boundary is explicit in `CandidateManifest.installable_plugin_manifest()`
and `plugin_records()`: source delivery validation still checks the complete
repository manifest, while receipt and installed-candidate qualification digest
only the exact declared installable plugin file set. The latter continues to
reject every missing, extra, altered, non-regular, or symlinked payload file
and every unsafe directory topology. It is a scope separation, not a relaxed
parity check.

Installed qualification also uses the single typed resolver in
`scripts/cortex_candidate_location.py`. A complete checkout/release root maps
to its plugin child once; an installed root maps directly to the exact plugin
root named by the verified receipt. The two topologies are never auto-detected
or interchanged. Wrong roots, duplicate nested `plugins/cortex` roots,
symlinked/missing server paths, receipt disagreement, and source fallback fail
before an MCP subprocess can start.
