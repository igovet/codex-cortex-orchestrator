Desktop model configuration function: `scripts/cortex-desktop-dev:live_test_config` (line 192).
It sets the isolated profile's model, coordinator reasoning effort, developer instructions, and default subagent model.
Immediate/direct caller: `scripts/cortex-desktop-dev:configure_workspace_network` (line 226).
Direct relationship: `configure_workspace_network` reads the isolated config, then calls `live_test_config(source)` at line 236.
The graph also reports `main` as a transitive caller (depth 2), not the immediate caller.
Evidence boundary: targeted source inspection and codebase graph trace; no source files were modified.
