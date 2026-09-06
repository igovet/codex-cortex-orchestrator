# Conventions

Use Python 3.11+ and `python3`, with no runtime third-party dependencies. Keep
installable behavior under `plugins/cortex/`. All tool arguments, retry contracts
and size limits belong in advertised schemas and descriptions, not prompt prose.

Use one project-local SQLite store at `<project>/.codex/cortex/cortex.sqlite3`.
The native host index locates and validates the canonical project but never supplies
a home-wide Cortex store. Same-project access is serialized; different projects
have independent stores. Use one Markdown pipeline per task with newest editions first. Ordinary reports
are immutable. Workers receive obligations directly and select only necessary
optional reports. The model owns coordination, evidence interpretation and final
judgment. No mandatory specialist sequence or protocol compatibility is allowed.
The coordinator answers short questions, reads necessary user sources and evidence,
owns pipeline editions and delegates the main technical work. A bounded worker can
research, implement, verify and document its result. Additional agents require a
concrete benefit; direct tiny edits versus delegation need measured total cost.
Pages bound individual reads, not the amount of requirements that may be recovered.
Bounded independent discovery may precede the first pipeline edition. Record useful
durable state before dependency, shared-resource or acceptance decisions rather than
requiring pipeline publication as a universal first stage.

Lifecycle hooks perform short local storage and integrity work. They do not select
specialists, approve actions, accept results or impose mandatory stages. Only
confirmed registered-file integrity violations may deny a patch. Hook metadata and
model actions remain separately observable.

Keep semantic version 1.15.6 for this change. Recompute the complete payload hash
before package checks or tests after every installable edit. Run release-sensitive
checks sequentially. Do not modify stable Codex configuration or plugin caches.
Use the dedicated isolated candidate launcher for real-host verification.

Plugin instructions use standard marketplace skill loading: an attached complete
body or the exact advertised SKILL.md path, plus needed declared Markdown references.
Already attached live schemas need no catalogue bootstrap or prescribed first-call
batch. Do not read agent TOML, server internals or enumerate the installation. No
personal registration, copied skill bodies or custom loader is needed. Inert
JavaScript wrappers may carry a literal patch unchanged to the native patch tool;
executable interpolation/substitution and report bodies in the Cortex writer are
forbidden. See
[host compatibility](host-compatibility.md).

Current isolated live qualification uses Luna/high for every coordinator and Luna
at medium/high for native workers. This test policy does not replace the product's
user-selected coordinator and evidence-based worker routing.
