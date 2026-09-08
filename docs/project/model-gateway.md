# Model Gateway lifecycle

The Model Gateway is controlled by the global
`$CODEX_HOME/cortex/config.toml`. Marketplace MCP startup enables it by
default; set `gateway.enabled = false` for explicit storage-only mode.

The packaged Python MCP entrypoint creates the owner-only config when absent,
installs the exact hash-locked wheel set into `$CODEX_HOME/cortex/deps/<lock-digest>`, and
calls the cached supervisor's `connect` operation. Private dependency receipts
allow verified trees to be reused across MCP/worker starts. Different lock
revisions use separate trees. The isolated launcher's supplied dependency tree
is verified and retained. Installation output is kept off the MCP stdout
JSON-RPC stream. Any dependency, ownership, config, payload, or readiness
failure aborts MCP startup with a nonzero error.

The isolated `scripts/cortex-dev` launcher checks the explicit gateway config
before launching CLI or Desktop preparation and runs the packaged
Marketplace-cache `cortex_runtime_ctl.py ensure` selected from the prepared
candidate receipt. MCP startup performs the same check for an existing
explicitly enabled configuration. Recovery is fail-closed: bind,
identity, dependency, or readiness failures propagate and prevent MCP startup
from continuing with an unverified provider route. Before binding, the
supervisor drains an older same-user Cortex gateway whose complete command and
packaged manifest match the requested loopback listener, then retries the bind.
It never terminates an unrelated process on the port. An absent configuration
is created with defaults using an atomic create-only write; an existing file is
validated and never overwritten.
The packaged MCP manifest explicitly forwards both the observation directory
and the isolated `CORTEX_DEPENDENCY_DIR` supplied by `scripts/cortex-dev`; this
keeps cached MCP startup and the gateway child on the same pinned dependency
identity.
The Desktop helper derives that same canonical target after preparation,
rejects missing or non-private dependency trees, scrubs inherited values, and
overwrites the child environment before launching Electron.

The gateway configuration file and its `cortex` control directory must be
owner-controlled and private; an existing directory, symlink, permissive mode,
or other unsafe path fails closed rather than being treated as absent. Config
bytes and their inode fingerprint are captured from one opened descriptor, so
an atomic replacement cannot leave an enabled snapshot cached under a disabled
file's fingerprint.

`reload` hot-swaps model/effort policy changes. Listener host/port or upstream
changes remain pending until the supervisor drains and restarts the owned child.
Reloading a transition to `gateway.enabled = false` stops the owned process;
the proxy also rejects any request that races with that transition instead of
forwarding under a disabled policy. If the owned child cannot be signaled or
does not drain, reload returns a structured failure and the control command
exits nonzero; it never reports successful convergence. A process reporting
`draining` is not readiness-successful and cannot be reused for convergence.
`stop` sends a controlled drain signal and waits up to its deadline for runtime
state removal. Provider configuration is automatic after readiness: it
uses the effective `CODEX_HOME` (or an explicit `--codex-home`), preserves the
existing Codex TOML including inline table-header comments, and writes a
first-version `config.toml.cortex-backup` before an atomic update. Backup
publication is create-only under concurrent configure calls, so the first
complete recovery point is never overwritten. Automatic setup preserves
model and reasoning-effort choices and unrelated feature flags. It selects
`context_management = false` and `remote_compaction_v2 = true`, journaling the
previous presence/value of each key. Reconnect preserves later user feature
overrides, and disconnect restores only unchanged owned feature values.
Valid quoted table headers,
array-table boundaries, and inline comments are preserved; settings are inserted
only into their semantic TOML tables.

The provider points to the verified fixed loopback listener, with
`requires_openai_auth = true` and `supports_websockets = true`. Codex supplies
its existing authentication. Automatic setup requires its normal OpenAI route;
custom providers/upstreams and existing user-owned `cortex` definitions are
rejected. A separate private route journal stores the previous selection and
owned fields, supports interrupted publication recovery, and preserves later
user overrides. Stop/disable restores only unchanged owned values. The running
gateway observes removal/disable of the exact Marketplace entry recorded at
setup, restores the route and drains. A forcibly killed gateway cannot observe
uninstallation. The first MCP startup can occur after the client loaded its
configuration: a new task or Desktop restart may be needed. No hot migration of
existing clients is claimed. See the official [custom provider documentation](https://learn.chatgpt.com/docs/config-file/config-advanced#custom-model-providers).

When `gateway.enabled` changes from true to false, `ensure` drains and waits for
the positively owned child to remove its runtime state, then reports `stopped`;
it does not leave a healthy proxy running under a disabled configuration.

The experimental context-management flow can install a new context window
without any server summary. Codex's
[upstream implementation](https://github.com/openai/codex/commit/32b65bb)
explicitly routes token-budget compaction through a local reset while retaining
the compaction lifecycle events. Thus a `compacted` event or a successful
notes/thread-hint request alone cannot establish model-based summarization.
Automatic provider setup disables that mode and selects the V2 summary flow;
an already running client needs a configuration reload. A later user override
back to context management is respected but restores the local-reset behavior.

Compaction routing covers both supported summary wire paths. A POST to
`/backend-api/codex/responses` is classified as `auto_compaction` only when it
contains a direct `compaction_trigger`; a POST to
`/backend-api/codex/responses/compact` is classified as `manual_compaction`
when its JSON body can be decoded. Because the production upstream retired
that path, recognized JSON is routed in-call to `/backend-api/codex/responses`
while retaining the query string; opaque legacy bytes remain byte- and
path-preserving. HTTP recognized forms are rewritten at `model`,
`reasoning.effort`, and the upstream-required `store: false` and `stream: true`
values. When the optional WebSocket transport is used, only uncompressed
direct `response.create` compaction messages are inspected and only `model`
and `reasoning.effort` are rewritten; store and stream fields are preserved for
this transport. Ordinary JSON requests
retain their original model and effort in bounded diagnostics, while malformed
ordinary or opaque legacy wire formats are forwarded byte-for-byte. No request
body, credentials, or headers are logged. The runtime keeps a private,
rotating gateway outcome log bounded to 256 KiB.

The client-facing transport is HTTP/SSE and WebSocket on the same fixed local
provider listener. The proxy opens TLS to the fixed upstream. Each WebSocket
leg has its own handshake, masking and message framing, with compression
disabled; application headers, message content and control signals are relayed.
Redirects are rejected before a second authenticated handshake request. The installed
Codex binary's source markers point to `core/src/compact_remote_v2.rs` and
`codex-api/src/endpoint/responses_websocket.rs`, and its request schema exposes
`response.create`, `input`, `reasoning`, `store`, and `stream`. The precise
threshold branch is not distributed as readable Rust source, so the wire-shape
conclusion is source-backed but branch-level provenance is unconfirmed.

The public upstream source resolves the route shape: `core/src/compact_remote_v2.rs`
constructs automatic-compaction metadata with `CompactionTrigger::Auto`; its
`compact_remote_v2_attempt.rs` appends a `ResponseItem::CompactionTrigger {}`
to the prompt input and calls `ModelClientSession::stream`. The common
`codex-api/src/endpoint/responses_websocket.rs` serializes the resulting
`ResponsesWsRequest::ResponseCreate` and sends that serialized request as a
WebSocket text message. Therefore the automatic trigger is handled by the same
outbound Responses WebSocket `response.create` path, not by the retired
`/backend-api/codex/responses/compact` route. This confirms the trigger/handler
shape, but not the exact local token-threshold branch in the stripped binary.

Automatic compaction and a standalone `/compact` action may use the same V2
WebSocket `response.create` with `input: [{"type":"compaction_trigger"}]`.
The gateway can classify the protocol trigger (`websocket_compaction`) but
cannot infer whether it came from a threshold or a user action from network
bytes alone. Safe attribution uses the local event stream: a native `/compact`
user message immediately before the compaction indicates standalone steering;
absence of that message leaves the event as auto-or-unknown. Do not repeat a
one-shot prompt to obtain this correlation.

The isolated `scripts/cortex-dev` path invokes the same provider setup only in
its prepared, owner-only candidate. It no longer sets proxy or certificate
variables. Diagnostics record only bounded route/model/effort/status metadata;
cookies, authorization, bodies, and WebSocket payloads are excluded.
The isolated launcher migrates its former default port 8787 to 18787 so live
checks can coexist with a running stable gateway. Other explicitly configured
isolated ports are retained. Process replacement is scoped to the same Codex
home and never drains the stable listener during a development run.

The isolated developer launcher removes the owner-controlled dependency target
before installing the Linux CPython 3.11/3.12 wheel set from
`plugins/cortex/requirements.lock` with pip `--require-hashes` and `--no-compile`,
then verifies imported distribution versions and the installed dependency-byte
digest. Gateway health and runtime state require the lock-manifest digest,
installed-byte digest, current normalized package digest, exact stamped
entrypoint, and complete `python -B cortex_gateway.py serve --host ... --port
...` invocation. A substituted same-version dependency therefore fails health
identity. Requests
must carry a non-empty valid loopback Host authority: IPv4/localhost may include
a decimal port, and IPv6 must use `[::1]` with an optional port.
The proxy forwards the original request path/query, exact body bytes, and every
header pair (including duplicates, `Authorization`, cookies, `Origin`, content
metadata, and hop-by-hop controls) without filtering or collapsing them. The
incoming `Host` is validated as a loopback listener authority, then rewritten
only on the upstream wire to the configured upstream authority; HTTP/1.1 cannot
route to `chatgpt.com` while presenting `127.0.0.1` as its virtual host. This is
transport metadata, not a semantic request rewrite, and prevents Cloudflare
from rejecting the request before authentication. The body exceptions are the
compaction model/effort policy and the recognized HTTP compaction compatibility
fields described above; representation metadata changes only as required for
those rewritten bytes. Redirects are not followed, so forwarded credentials cannot cross the
fixed upstream boundary. Response header multiplicity and streamed body chunks
are retained; client cancellation cancels only that request's upstream task.
