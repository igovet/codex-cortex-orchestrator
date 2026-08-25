---
name: content-safety
description: Protect secrets, credentials, personal data, and sensitive operational details during coding, documentation, delegation, and review. Use when handling configuration, logs, exports, incidents, authentication, or any artifact that may contain sensitive data.
---

# Content Safety

Before sharing, storing, or delegating an artifact, check for credentials, API keys, bearer tokens, private keys, session cookies, connection strings, personal data, customer exports, and internal operational identifiers.

- Redact the sensitive value while retaining the minimum useful structure, such as `<REDACTED_TOKEN>`.
- Do not put secrets in prompts, semantic conclusions, documentation, tests, fixtures, source files, command output, or commits.
- Prefer references to secure environment variables and secret managers over literal values.
- If a secret is already exposed, stop propagating it; record only its sanitized location and recommend rotation or revocation through the authorized owner.
- Keep security findings concise and avoid giving exploit instructions beyond what is needed to remediate an authorized codebase.
