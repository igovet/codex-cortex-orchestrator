---
name: content-safety
description: Protect secrets, credentials, personal data, and sensitive operational details during coding, documentation, delegation, and review. Use when handling configuration, logs, exports, incidents, authentication, or any artifact that may contain sensitive data.
---

# Content Safety

Before sharing, storing, or delegating an artifact, check for credentials, API keys, bearer tokens, private keys, session cookies, connection strings, personal data, customer exports, and internal operational identifiers.

This is model/user discipline. Do not claim that the V12 ledger automatically
detects, redacts, authorizes, expires, or deletes accepted content. Store only
the minimum safe English coordination content, except for exact original user
text required by the task or decision contract.

- Redact the sensitive value while retaining the minimum useful structure, such as `<REDACTED_TOKEN>`.
- Do not put secrets in prompts, semantic conclusions, documentation, tests, fixtures, source files, command output, or commits.
- Prefer references to secure environment variables and secret managers over literal values.
- If a secret is already exposed, stop propagating it; record only its sanitized location and recommend rotation or revocation through the authorized owner.
- Keep security findings concise and avoid giving exploit instructions beyond what is needed to remediate an authorized codebase.
- Treat task, decision, report, closure, idempotency, and projection
  content as potentially retained host-private data. Sanitize titles, section
  names, abort reasons, filenames, summaries, and links as well as report
  bodies. Do not derive a filename from secret-bearing or untrusted text.
- A host-private plan or finalized-report Markdown projection is safe to publish
  to the user only when the active tool has freshly verified its contained
  absolute path, regular file type, source freshness, and digest. Task,
  decision, delegation, closure, governance, handoff, index, and
  timeline records remain SQLite-only. Never copy a verified path into worker
  messages, external channels, raw logs, or error details, and never publish a
  stale/failed projection.
- Preserve an exact user decision only in its designated original-response
  field, together with the neutral prompt and language; do not generate or
  accept translated or duplicate language-specific fields. Redact sensitive values in
  the original response as required by this policy. A recorded authorization
  assertion is evidence, not a credential or approval token.
