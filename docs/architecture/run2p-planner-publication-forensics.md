# Run2p planner publication forensic matrix

This development-only note records sanitized observations from the isolated
run. It contains no task identifiers, handles, prompts, report contents, or
raw diagnostic payloads.

| Boundary | Observed | Assessment |
|---|---|---|
| Planner assignment | Server-created planner assignment was present | Not an assignment-creation failure |
| Renderer | Effective contract exposed a planner-only full planning-item catalogue | Renderer did not omit the catalogue |
| Item references | Published requests used values not copied byte-for-byte from the server-rendered catalogue | Model reconstructed/invented references |
| Advertised schema | `publish_plan` requires complete v3 evidence and exact current planning-item coverage | Schema and server admission agree |
| Backend comparison | Admission compares canonical item references against the current assignment revision | Correct server-owned representation |
| Retry behavior | Repeating the malformed publication cannot repair it | Correct: unchanged or invented references must not be retried |

## Root cause

The planner publication failed at model evidence construction: the planner did
not preserve and reuse the exact server-issued planning-item references from its
dispatch brief. This is a renderer-to-model evidence-retention failure, not a
database, schema-bound, or backend-representation mismatch.

## Production-layer recommendation

Strengthen the dispatch renderer and worker prompt so the complete planning
catalogue is carried as immutable server-rendered evidence and the planner is
required to map that exact catalogue once. The backend should continue to
reject unknown, reconstructed, stale, duplicate, or incomplete references.
