"""One black-box source-mode gate for the publishable Cortex plugin."""
from __future__ import annotations

import hashlib
import json
import os
import py_compile
import re
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


def test_cortex_plugin_is_publishable_and_operational() -> None:
    """Prove the packaged marketplace contract and one durable public lifecycle."""
    repository = Path(__file__).resolve().parents[1]
    plugin = repository / "plugins" / "cortex"
    server = plugin / "scripts" / "cortex.py"

    def require(condition: bool, label: str) -> None:
        if not condition:
            raise AssertionError(f"release gate failed: {label}")

    marketplace_path = repository / ".agents" / "plugins" / "marketplace.json"
    manifest_path = plugin / ".codex-plugin" / "plugin.json"
    mcp_path = plugin / ".mcp.json"
    marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mcp_contract = json.loads(mcp_path.read_text(encoding="utf-8"))
    for contract in (marketplace_path, *sorted(plugin.rglob("*.json"))):
        json.loads(contract.read_text(encoding="utf-8"))
    for contract in sorted(plugin.rglob("*.toml")):
        tomllib.loads(contract.read_text(encoding="utf-8"))

    version = str(manifest.get("version") or "")
    require(manifest.get("name") == "cortex" and version.startswith("11.0.1"), "plugin manifest identity")
    require(marketplace.get("name") == "cortex", "marketplace identity")
    entries = marketplace.get("plugins")
    require(isinstance(entries, list) and len(entries) == 1, "single marketplace entry")
    require(isinstance(entries[0], dict), "marketplace entry object")
    require(entries[0].get("name") == manifest.get("name"), "marketplace and manifest name parity")
    require(entries[0].get("source") == {"source": "local", "path": "./plugins/cortex"}, "marketplace source parity")
    servers = mcp_contract.get("mcpServers")
    require(isinstance(servers, dict) and isinstance(servers.get("cortex"), dict), "MCP server contract")
    launch = servers["cortex"]
    require(launch.get("command") == "./scripts/cortex-launcher", "MCP launcher parity")
    require(launch.get("args") == ["./scripts/cortex.py"] and launch.get("cwd") == ".", "MCP server parity")

    def source_snapshot() -> tuple[tuple[str, str, str], ...]:
        records: list[tuple[str, str, str]] = []
        targets = (plugin, marketplace_path, repository / "scripts" / "validate-cortex-marketplace.py")
        for target in targets:
            candidates = (target, *target.rglob("*")) if target.is_dir() else (target,)
            for candidate in candidates:
                relative = candidate.relative_to(repository).as_posix()
                stat = candidate.lstat()
                if candidate.is_symlink():
                    records.append((relative, "symlink", os.readlink(candidate)))
                elif candidate.is_dir():
                    records.append((relative, "directory", ""))
                elif candidate.is_file():
                    records.append((relative, "file", hashlib.sha256(candidate.read_bytes()).hexdigest()))
        return tuple(sorted(records))

    before_source = source_snapshot()

    with tempfile.TemporaryDirectory(prefix="cortex-marketplace-gate-") as temporary:
        root = Path(temporary)
        project = root / "project"
        host_store = root / "host-state"
        private_home = root / "home"
        private_codex_home = root / "codex-home"
        project.mkdir()
        host_store.mkdir(mode=0o700)
        private_home.mkdir(mode=0o700)
        private_codex_home.mkdir(mode=0o700)
        pycache = root / "pycache"
        pycache.mkdir(mode=0o700)
        environment = os.environ.copy()
        environment.update({
            "HOME": str(private_home),
            "CODEX_HOME": str(private_codex_home),
            "CORTEX_HOST_STATE_DIR": str(host_store),
            "PYTHONPYCACHEPREFIX": str(pycache),
        })
        for source in sorted(plugin.rglob("*.py")):
            compiled = pycache / source.relative_to(repository).with_suffix(".pyc")
            compiled.parent.mkdir(parents=True, exist_ok=True)
            try:
                py_compile.compile(str(source), cfile=str(compiled), doraise=True)
            except py_compile.PyCompileError as error:
                raise AssertionError("release gate failed: plugin Python compilation") from error
        validation = subprocess.run(
            [sys.executable, "scripts/validate-cortex-marketplace.py"],
            cwd=repository,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            check=False,
        )
        require(validation.returncode == 0, "marketplace validator")

        class JsonRpc:
            def __init__(self, audience: str | None = None) -> None:
                command = [sys.executable, str(server)]
                if audience:
                    command.append(f"--mcp-audience={audience}")
                self.process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    env=environment,
                )
                self.identifier = 1
                self.response_frames: list[str] = []
                try:
                    initialized = self.request("initialize", {"protocolVersion": "2025-06-18"})
                    self.initialized = initialized
                    require(initialized.get("serverInfo", {}).get("name") == "cortex", "MCP server name")
                    require(initialized.get("serverInfo", {}).get("version") == version, "MCP server version")
                    self.notify("notifications/initialized", {})
                except BaseException:
                    # Context-manager entry has not happened yet.  Always
                    # release the child and every pipe before surfacing an
                    # initialization failure, without replacing that failure
                    # by an expected non-zero child exit.
                    self.close(require_clean_exit=False)
                    raise

            def request(self, method: str, params: dict[str, object]) -> dict[str, object]:
                require(self.process.stdin is not None and self.process.stdout is not None, "MCP pipes")
                request_id = self.identifier
                self.identifier += 1
                self.process.stdin.write(json.dumps({
                    "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
                }, ensure_ascii=False) + "\n")
                self.process.stdin.flush()
                line = self.process.stdout.readline()
                require(bool(line), "MCP response")
                try:
                    response = json.loads(line)
                except json.JSONDecodeError as error:
                    raise AssertionError("release gate failed: MCP response JSON") from error
                self.response_frames.append(line)
                rpc_error = response.get("error") if isinstance(response.get("error"), dict) else {}
                require(
                    response.get("id") == request_id and "error" not in response,
                    "MCP JSON-RPC success " + json.dumps({
                        "code": rpc_error.get("code"),
                    }, sort_keys=True),
                )
                result = response.get("result")
                require(isinstance(result, dict), "MCP result object")
                return result

            def notify(self, method: str, params: dict[str, object]) -> None:
                require(self.process.stdin is not None, "MCP stdin")
                self.process.stdin.write(json.dumps({
                    "jsonrpc": "2.0", "method": method, "params": params,
                }, ensure_ascii=False) + "\n")
                self.process.stdin.flush()

            def tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
                result = self.request("tools/call", {"name": name, "arguments": arguments})
                payload = result.get("structuredContent")
                require(isinstance(payload, dict), "MCP structured content")
                return payload

            def close(self, *, require_clean_exit: bool = True) -> None:
                if self.process.stdin and not self.process.stdin.closed:
                    self.process.stdin.close()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    self.process.wait(timeout=5)
                finally:
                    for pipe in (self.process.stdout, self.process.stderr):
                        if pipe and not pipe.closed:
                            pipe.close()
                if require_clean_exit:
                    require(self.process.returncode == 0, "MCP clean exit")

            def __enter__(self) -> "JsonRpc":
                return self

            def __exit__(self, *_: object) -> None:
                self.close()

        runtime_path = str(plugin / "scripts")
        if runtime_path not in sys.path:
            sys.path.insert(0, runtime_path)
        prior_pycache_prefix = sys.pycache_prefix
        sys.pycache_prefix = str(pycache)
        try:
            import cortex as cortex_server
            from cortex_runtime import ledger_db
            from cortex_runtime.ledger_db import DATABASE_NAME, DATABASE_SCHEMA_VERSION
        finally:
            sys.pycache_prefix = prior_pycache_prefix

        # Exercise the one exact released aggregate-v17 predecessor through
        # the same canonical registry used by production.  The fixture is
        # synthetic: it proves lossless live-row retention, Unicode question
        # conversion, retired-object removal, and fail-closed tamper handling
        # without embedding any host ledger contents or adding another gate.
        legacy_root = root / "legacy-v17"
        legacy_root.mkdir(mode=0o700)
        legacy_database = legacy_root / DATABASE_NAME
        legacy_statements = (
            ledger_db._BASE_SCHEMA_STATEMENTS
            + ledger_db._ARTIFACT_SCHEMA_STATEMENTS
            + ledger_db._CLOSURE_SCHEMA_STATEMENTS
            + ledger_db._PROJECTION_SCHEMA_STATEMENTS
            + ledger_db._PRUNE_SCHEMA_STATEMENTS
            + ledger_db._PRE_QUESTION_BATCH_REVISION_AWARE_ORCHESTRATION_SCHEMA_STATEMENTS
            + ledger_db._GOVERNANCE_SCHEMA_STATEMENTS
            + ledger_db._GOVERNANCE_INTEGRITY_SCHEMA_STATEMENTS
            + ledger_db._GOVERNANCE_LIFECYCLE_INTEGRITY_SCHEMA_STATEMENTS
            + ledger_db._GOVERNANCE_LIFECYCLE_ENVELOPE_AUTH_SCHEMA_STATEMENTS
            + ledger_db._ATTEMPT_RESULT_EVENT_PROTOCOL_SCHEMA_STATEMENTS
            + ledger_db._ATTEMPT_VERIFICATION_AUTHORITY_SCHEMA_STATEMENTS
            + ledger_db._ATTEMPT_QUESTION_EVENT_SCHEMA_STATEMENTS
            + ledger_db._PRE_REPORT_REPAIR_ESCROW_SCHEMA_STATEMENTS
        )
        predecessor_histories = ledger_db._EXACT_LEGACY_PRE_REPORT_V17_HISTORY
        require(
            len(predecessor_histories) == 1
            and predecessor_histories[0][0] == 17
            and predecessor_histories[0][1] == "canonical-current-ledger",
            "one exact aggregate-v17 predecessor registry entry",
        )
        created = "2026-08-25T00:00:00+00:00"
        report_text = "synthetic report — сохранён"
        report_raw = report_text.encode("utf-8")
        report_digest = hashlib.sha256(report_raw).hexdigest()
        question_text = "Выбрать safe_mode — или продолжить read-only?"
        answer_text = "safe_mode — proceed read-only"
        with sqlite3.connect(legacy_database) as connection:
            ledger_db._execute_migration_statements(connection, legacy_statements)
            connection.execute(
                "INSERT INTO schema_migrations(version,name,applied_at,checksum) VALUES(?,?,?,?)",
                (*predecessor_histories[0][:2], created, predecessor_histories[0][2]),
            )
            connection.execute("PRAGMA user_version = 17")
            connection.execute(
                "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    "task-legacy-gate", 1, "task-0001", "{}", "{}", None,
                    "active", 1, created, created,
                ),
            )
            connection.execute(
                "INSERT INTO task_revisions VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    "task-legacy-gate", 1, None, "initial", "synthetic task",
                    "en", "synthetic task", "not_required", created,
                ),
            )
            connection.execute(
                "INSERT INTO worker_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "session-legacy-gate", "task-legacy-gate", "attempt-legacy-gate",
                    "agent-legacy-gate", "general", "spawn_agent", 1, "completed",
                    0, created, created, created,
                ),
            )
            connection.execute(
                "INSERT INTO artifact_blobs VALUES(?,?,?,?,?,?,?)",
                (
                    "blob-legacy-gate", report_digest, "text/plain", len(report_raw),
                    1, "utf-8", created,
                ),
            )
            connection.execute(
                "INSERT INTO artifact_blob_chunks VALUES(?,?,?,?,?,?)",
                (
                    "blob-legacy-gate", 0, report_text, None, len(report_raw),
                    report_digest,
                ),
            )
            connection.execute(
                "INSERT INTO logical_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "artifact-legacy-gate", "task-legacy-gate", "report",
                    "synthetic report", "text/plain", report_digest, len(report_raw),
                    1, 1, "blob-legacy-gate", None, created,
                ),
            )
            connection.execute(
                "INSERT INTO attempt_results VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "attempt-result-legacy-gate", "task-legacy-gate", "attempt-legacy-gate",
                    "completed", "COMPLETED", "synthetic completion", "[]", "[]", "[]",
                    "[]", "{}", "{}", "[]", "server_observed", "sha256:" + report_digest,
                    "submission-legacy-gate", created, created, created, created, created,
                ),
            )
            connection.execute(
                "INSERT INTO question_batches VALUES(?,?,?,?,?,?,?,?)",
                (
                    "batch-legacy-gate", "task-legacy-gate", "attempt-legacy-gate",
                    "batch-key-legacy-gate", "answered", "und", created, created,
                ),
            )
            connection.execute(
                "INSERT INTO question_items VALUES(?,?,?,?,?,?,?)",
                (
                    "batch-legacy-gate", "question-key-legacy-gate", "text",
                    question_text, question_text, "[]", 1,
                ),
            )
            connection.execute(
                "INSERT INTO question_answers VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    "batch-legacy-gate", "question-key-legacy-gate", answer_text,
                    "und", "[]", answer_text, "not_required", None, None,
                ),
            )
            connection.execute("PRAGMA foreign_keys = ON")
            require(not connection.execute("PRAGMA foreign_key_check").fetchall(), "v17 synthetic fixture integrity")
        os.chmod(legacy_database, 0o600)

        tampered_root = root / "tampered-v17"
        tampered_root.mkdir(mode=0o700)
        tampered_database = tampered_root / DATABASE_NAME
        with sqlite3.connect(legacy_database) as source, sqlite3.connect(tampered_database) as target:
            source.backup(target)
            target.execute("UPDATE schema_migrations SET checksum = ?", ("0" * 64,))
        os.chmod(tampered_database, 0o600)
        try:
            ledger_db.ensure_database(tampered_root)
        except ValueError as error:
            require("unsupported pre-canonical ledger" in str(error), "tampered v17 fails closed")
        else:
            raise AssertionError("release gate failed: tampered v17 accepted")
        with sqlite3.connect(tampered_database) as connection:
            require(
                int(connection.execute("PRAGMA user_version").fetchone()[0]) == 17
                and connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
                and connection.execute("SELECT COUNT(*) FROM question_batches").fetchone()[0] == 1,
                "tampered v17 remains untouched",
            )

        ledger_db.ensure_database(legacy_root)
        with sqlite3.connect(legacy_database) as connection:
            connection.row_factory = sqlite3.Row
            objects = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','index','trigger')"
                )
            }
            durable_question = connection.execute(
                "SELECT question_ref,question_text,answer_text,status FROM durable_questions"
            ).fetchone()
            require(int(connection.execute("PRAGMA user_version").fetchone()[0]) == 18, "v17 to v18 upgrade")
            require(
                not {"question_batches", "question_items", "question_answers", "question_batches_status_idx"} & objects,
                "v17 retired question storage removed",
            )
            require(
                durable_question is not None
                and str(durable_question["question_ref"]).startswith("question-")
                and durable_question["question_text"] == question_text
                and durable_question["answer_text"] == answer_text
                and durable_question["status"] == "answered",
                "v17 Unicode text pair preserved",
            )
            require(
                connection.execute("SELECT status FROM tasks").fetchone()[0] == "active"
                and connection.execute("SELECT status FROM worker_sessions").fetchone()[0] == "completed"
                and connection.execute("SELECT lifecycle_status FROM attempt_results").fetchone()[0] == "COMPLETED"
                and connection.execute("SELECT kind FROM logical_artifacts").fetchone()[0] == "report"
                and connection.execute("SELECT text_content FROM artifact_blob_chunks").fetchone()[0] == report_text,
                "v17 live task session result report and chunk preserved",
            )
            require(connection.execute("SELECT COUNT(*) FROM repair_escrow").fetchone()[0] == 0, "legacy repair authority revoked")
            require(connection.execute("PRAGMA quick_check").fetchone()[0] == "ok", "migrated v18 quick check")
            require(not connection.execute("PRAGMA foreign_key_check").fetchall(), "migrated v18 foreign keys")
        contracts = getattr(cortex_server, "PUBLIC_CONTRACTS", None)
        schema_registry = getattr(cortex_server, "PUBLIC_SCHEMA_REGISTRY", None)
        runtime_tools = getattr(cortex_server, "PUBLIC_TOOLS", None)
        require(isinstance(contracts, dict) and bool(contracts), "canonical public contracts")
        require(isinstance(schema_registry, dict) and isinstance(runtime_tools, dict), "runtime public registries")
        start_contract = contracts.get("start_orchestration")
        start_schema = start_contract.get("inputSchema") if isinstance(start_contract, dict) else None
        start_properties = start_schema.get("properties") if isinstance(start_schema, dict) else None
        project_root_schema = start_properties.get("project_root") if isinstance(start_properties, dict) else None
        waves_schema = start_properties.get("waves") if isinstance(start_properties, dict) else None
        wave_schema = waves_schema.get("items") if isinstance(waves_schema, dict) else None
        wave_properties = wave_schema.get("properties") if isinstance(wave_schema, dict) else None
        workers_schema = wave_properties.get("workers") if isinstance(wave_properties, dict) else None
        worker_schema = workers_schema.get("items") if isinstance(workers_schema, dict) else None
        worker_properties = worker_schema.get("properties") if isinstance(worker_schema, dict) else None
        allowed_paths_schema = worker_properties.get("allowed_paths") if isinstance(worker_properties, dict) else None
        allowed_path_item = allowed_paths_schema.get("items") if isinstance(allowed_paths_schema, dict) else None
        require(
            isinstance(project_root_schema, dict)
            and project_root_schema.get("description") == "An absolute path to the project root."
            and isinstance(allowed_paths_schema, dict)
            and allowed_paths_schema.get("description")
            == "Every entry is strictly project-relative to project_root, never absolute."
            and isinstance(allowed_path_item, dict)
            and allowed_path_item.get("description")
            == "A project-relative path such as desktop-v11-smoke.txt; never an absolute path.",
            "canonical start path guidance",
        )
        path_guidance_markers = (
            "an absolute path to the project root",
            "strictly project-relative to project_root",
            "desktop-v11-smoke.txt",
        )
        require(
            not any(
                marker in str(start_contract.get("description") or "").lower()
                for marker in path_guidance_markers
            ),
            "path guidance absent from tool description",
        )
        model_facing_sources = (
            *sorted((plugin / "skills").rglob("SKILL.md")),
            *sorted((plugin / "agents").glob("*.toml")),
            plugin / "profiles.json",
            plugin / "scripts/cortex_runtime/briefings.py",
            plugin / "scripts/cortex_runtime/prompt_compiler.py",
        )
        require(
            all(
                not any(marker in source.read_text(encoding="utf-8").lower() for marker in path_guidance_markers)
                for source in model_facing_sources
            ),
            "path guidance absent from skills and prompts",
        )
        answer_contract = contracts["answer_orchestration_question"]
        answer_schema = answer_contract["inputSchema"]["properties"]["answer_text"]
        answer_guidance = str(answer_schema.get("description") or "").lower()
        require(
            all(marker in answer_guidance for marker in (
                "exact arbitrary-unicode", "answer text", "durable question",
            )),
            "answer guidance owned by canonical input schema",
        )
        answer_guidance_sources = (
            *sorted((plugin / "skills").rglob("SKILL.md")),
            *sorted((plugin / "agents").glob("*.toml")),
            plugin / "profiles.json",
            plugin / "prompt-contracts.json",
            plugin / "scripts/cortex_runtime/briefings.py",
        )
        require(
            "answer_text" not in str(answer_contract.get("description") or "")
            and all(
                "answer_text" not in source.read_text(encoding="utf-8")
                for source in answer_guidance_sources
            ),
            "answer argument guidance absent from tool description, skills, prompts, and briefing",
        )
        orchestrator_skill_text = (plugin / "skills/orchestrator/SKILL.md").read_text(encoding="utf-8").lower()
        control_skill_text = (plugin / "skills/cortex-control/SKILL.md").read_text(encoding="utf-8").lower()
        native_wait_markers = (
            "explicit 300-second native wait", "never use the native default",
            "another explicit 300-second native wait", "durable-question marker", "terminal-completion marker",
            "`read_worker_wave`", "does not authorize",
        )
        require(
            all(marker in orchestrator_skill_text for marker in native_wait_markers)
            and all(marker in control_skill_text for marker in native_wait_markers),
            "coordinator timeout repeats exact-child waits before result reads",
        )
        profile_contract = json.loads((plugin / "profiles.json").read_text(encoding="utf-8"))
        shared_contract = profile_contract.get("shared_worker_contract")
        wait_action = (
            shared_contract.get("coordinator_action_semantics", {}).get("wait_for_bound_workers")
            if isinstance(shared_contract, dict)
            else None
        )
        require(
            isinstance(wait_action, dict)
            and wait_action.get("per_wait_timeout_seconds") == 300
            and wait_action.get("minimum_overall_wait_seconds") == 300
            and wait_action.get("native_default_wait_allowed") is False
            and wait_action.get("repeat_after_no_marker") is True
            and wait_action.get("timeout_authorizes_result_read") is False
            and "same-child follow-up" in str(shared_contract.get("worker_question_pause_contract") or ""),
            "profile-native wait and durable-question pause contract",
        )
        prompt_contract = json.loads((plugin / "prompt-contracts.json").read_text(encoding="utf-8"))
        completion_contract = prompt_contract.get("worker_completion_contract")
        completion_text = "\n".join(
            str(value) for value in completion_contract.values()
        ).lower() if isinstance(completion_contract, dict) else ""
        require(
            all(marker in completion_text for marker in (
                "explicit 300-second native wait", "never use the native default",
                "another explicit 300-second native wait", "durable-question marker",
                "terminal-completion marker", "same-child follow-up",
            )),
            "prompt-native wait and durable-question pause contract",
        )
        require(
            schema_registry == {name: contract.get("inputSchema") for name, contract in contracts.items()}
            and set(runtime_tools) == set(contracts),
            "canonical registry parity",
        )

        def nested_values(value: object) -> list[object]:
            values = [value]
            if isinstance(value, dict):
                for item in value.values():
                    values.extend(nested_values(item))
            elif isinstance(value, list):
                for item in value:
                    values.extend(nested_values(item))
            return values

        for name, contract in contracts.items():
            require(
                isinstance(contract, dict)
                and set(contract) == {
                    "description", "inputSchema", "base_operation", "injected_arguments",
                    "audience", "execution",
                },
                f"canonical contract shape: {name}",
            )
            description = contract["description"]
            schema = contract["inputSchema"]
            require(isinstance(description, str) and isinstance(schema, dict), f"canonical contract types: {name}")
            require(
                contract.get("audience") in {"coordinator", "worker"}
                and isinstance(contract.get("execution"), dict)
                and set(contract["execution"]) == {"prerequisite", "terminal"}
                and isinstance(contract["execution"].get("prerequisite"), str)
                and isinstance(contract["execution"].get("terminal"), bool),
                f"canonical execution ownership: {name}",
            )
            require(
                not re.search(r"(?:Contract:|requires? fields?|forbids? fields?|optional fields?)", description, re.IGNORECASE),
                f"description contains no argument field table: {name}",
            )
            for item in nested_values(schema):
                if not isinstance(item, dict):
                    continue
                require(not ({"oneOf", "anyOf", "allOf", "not"} & set(item)), f"schema combinators absent: {name}")
                if item.get("type") == "object":
                    require(item.get("additionalProperties") is False, f"closed object schema: {name}")
        retired_aliases = {
            str(contract["base_operation"])
            for contract in contracts.values()
            if isinstance(contract, dict) and contract.get("injected_arguments")
        }
        require(not (retired_aliases & set(contracts)), "retired multiplexed aliases absent")

        scalar_pool: list[object] = []
        called: set[str] = set()
        worker_dispatch_calls: set[str] = set()
        protected_values: set[str] = set()

        def remember(value: object) -> None:
            for item in nested_values(value):
                if isinstance(item, (str, int, bool)) and item not in scalar_pool:
                    scalar_pool.append(item)
                protected_schemas = [
                    candidate for contract in contracts.values()
                    for candidate in nested_values(contract.get("inputSchema"))
                    if isinstance(candidate, dict)
                    and candidate.get("format") in {
                        "cortex-coordinator-ref", "cortex-repair-capsule", "cortex-payload-digest",
                    }
                ]
                if isinstance(item, str) and any(scalar_matches(item, schema) for schema in protected_schemas):
                    protected_values.add(item)

        def scalar_matches(value: object, schema: dict[str, object]) -> bool:
            raw_type = schema.get("type")
            if raw_type == "string":
                if not isinstance(value, str):
                    return False
                if len(value) < int(schema.get("minLength", 0)) or len(value) > int(schema.get("maxLength", 1 << 30)):
                    return False
                pattern = schema.get("pattern")
                if isinstance(pattern, str) and re.fullmatch(pattern, value) is None:
                    return False
            elif raw_type == "integer":
                if not isinstance(value, int) or isinstance(value, bool):
                    return False
                if isinstance(schema.get("minimum"), int) and value < schema["minimum"]:
                    return False
                if isinstance(schema.get("maximum"), int) and value > schema["maximum"]:
                    return False
            elif raw_type == "boolean" and not isinstance(value, bool):
                return False
            enum = schema.get("enum")
            if isinstance(enum, list) and value not in enum:
                return False
            return "const" not in schema or value == schema["const"]

        unavailable_formats = {
            "cortex-task-ref", "cortex-coordinator-ref", "cortex-dispatch-ref",
            "cortex-question-ref", "cortex-attempt-result-ref", "cortex-page-cursor",
            "cortex-repair-capsule", "cortex-payload-digest",
        }
        format_fixtures = {
            "cortex-initiative-ref": "initiative-release-gate",
            "cortex-record-ref": "record-release-gate",
            "cortex-artifact-ref": "artifact-release-gate",
            "cortex-request-ref": "request-release-gate",
        }

        class MissingSchemaValue(RuntimeError):
            pass

        def sample(schema: dict[str, object]) -> object:
            if "const" in schema:
                return schema["const"]
            default = schema.get("default")
            if default is not None:
                return default
            enum = schema.get("enum")
            if isinstance(enum, list) and enum:
                return enum[0]
            raw_type = schema.get("type")
            if raw_type == "object":
                properties = schema.get("properties")
                required = schema.get("required")
                require(isinstance(properties, dict) and isinstance(required, list), "sampled object schema")
                return {name: sample(properties[name]) for name in required}
            if raw_type == "array":
                count = max(1, int(schema.get("minItems", 0)))
                item_schema = schema.get("items")
                require(isinstance(item_schema, dict), "sampled array items schema")
                return [sample(item_schema) for _ in range(count)]
            if raw_type == "integer":
                return int(schema.get("minimum", 0))
            if raw_type == "boolean":
                return True
            if raw_type == "string":
                format_name = schema.get("format")
                if format_name == "cortex-project-root":
                    return str(project)
                for candidate in reversed(scalar_pool):
                    if scalar_matches(candidate, schema):
                        return candidate
                if isinstance(format_name, str) and format_name in format_fixtures:
                    return format_fixtures[format_name]
                if format_name in unavailable_formats:
                    raise MissingSchemaValue(str(format_name))
                length = max(1, int(schema.get("minLength", 1)))
                candidate = "x" * length
                if scalar_matches(candidate, schema):
                    return candidate
                raise MissingSchemaValue(str(schema.get("pattern") or format_name or "string"))
            return None

        def validate_instance(value: object, schema: dict[str, object], pointer: str = "") -> None:
            raw_type = schema.get("type")
            if raw_type == "object":
                require(isinstance(value, dict), f"schema object instance {pointer}")
                properties = schema.get("properties")
                required = schema.get("required")
                require(isinstance(properties, dict) and isinstance(required, list), f"schema object definition {pointer}")
                require(set(required).issubset(value) and set(value).issubset(properties), f"closed object instance {pointer}")
                for key, item in value.items():
                    validate_instance(item, properties[key], pointer + "/" + key)
            elif raw_type == "array":
                require(isinstance(value, list), f"schema array instance {pointer}")
                require(len(value) >= int(schema.get("minItems", 0)), f"schema array minimum {pointer}")
                require(len(value) <= int(schema.get("maxItems", 1 << 30)), f"schema array maximum {pointer}")
                if schema.get("uniqueItems"):
                    canonical = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
                    require(len(canonical) == len(set(canonical)), f"schema array uniqueness {pointer}")
                item_schema = schema.get("items")
                require(isinstance(item_schema, dict), f"schema array items {pointer}")
                for index, item in enumerate(value):
                    validate_instance(item, item_schema, pointer + f"/{index}")
            elif raw_type == "string":
                require(scalar_matches(value, schema), f"schema string instance {pointer}")
            elif raw_type == "integer":
                require(scalar_matches(value, schema), f"schema integer instance {pointer}")
            elif raw_type == "boolean":
                require(isinstance(value, bool), f"schema boolean instance {pointer}")
            elif "enum" in schema or "const" in schema:
                require(scalar_matches(value, schema), f"schema scalar instance {pointer}")

        def contract_for(base_operation: str, **injected: object) -> tuple[str, dict[str, object]]:
            matches = [
                (name, contract)
                for name, contract in contracts.items()
                if contract.get("base_operation") == base_operation
                and contract.get("injected_arguments") == injected
            ]
            require(len(matches) == 1, f"one canonical contract for {base_operation}/{injected}")
            name, contract = matches[0]
            schema = contract.get("inputSchema")
            require(isinstance(schema, dict), f"canonical input schema for {name}")
            return name, schema

        def set_free_text(arguments: dict[str, object], schema: dict[str, object], value: str) -> None:
            properties = schema.get("properties")
            required = schema.get("required")
            require(isinstance(properties, dict) and isinstance(required, list), "semantic text schema")
            candidates = [
                name for name in required
                if isinstance(properties.get(name), dict)
                and properties[name].get("type") == "string"
                and not properties[name].get("format")
                and not properties[name].get("enum")
                and not properties[name].get("pattern")
                and len(value) >= int(properties[name].get("minLength", 0))
                and len(value) <= int(properties[name].get("maxLength", 1 << 30))
            ]
            require(len(candidates) == 1, "one schema-selected semantic text field")
            arguments[candidates[0]] = value

        def set_required_array(arguments: dict[str, object], schema: dict[str, object], value: list[object]) -> None:
            properties = schema.get("properties")
            required = schema.get("required")
            require(isinstance(properties, dict) and isinstance(required, list), "semantic array schema")
            candidates = [
                name for name in required
                if isinstance(properties.get(name), dict) and properties[name].get("type") == "array"
            ]
            require(len(candidates) == 1, "one schema-selected semantic array field")
            arguments[candidates[0]] = value

        def invoke(
            rpc: JsonRpc,
            name: str,
            schema: dict[str, object],
            *,
            arguments: dict[str, object] | None = None,
        ) -> dict[str, object]:
            request_arguments = arguments if arguments is not None else sample(schema)
            require(isinstance(request_arguments, dict), f"generated arguments object: {name}")
            validate_instance(request_arguments, schema)
            contract = contracts[name]
            if contract.get("audience") == "worker":
                properties = schema.get("properties")
                require(isinstance(properties, dict), f"worker schema properties: {name}")
                dispatch_schema = properties.get("dispatch_ref")
                supplied_dispatch_ref = request_arguments.get("dispatch_ref")
                require(
                    isinstance(dispatch_schema, dict)
                    and scalar_matches(supplied_dispatch_ref, dispatch_schema)
                    and supplied_dispatch_ref == dispatch_ref,
                    f"worker call uses the one schema-bound server dispatch: {name}",
                )
                worker_dispatch_calls.add(name)
            payload = rpc.tool(name, request_arguments)
            called.add(name)
            remember(payload)
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            require(
                payload.get("error_code") != "tool_arguments_invalid"
                and error.get("code") != "tool_arguments_invalid",
                f"schema-valid request reached backend first try: {name}",
            )
            return payload

        def page_call(
            rpc: JsonRpc,
            name: str,
            schema: dict[str, object],
            arguments: dict[str, object],
        ) -> tuple[list[str], dict[str, object]]:
            parts: list[str] = []
            while True:
                payload = invoke(rpc, name, schema, arguments=arguments)
                part = payload.get("content") or payload.get("report")
                if isinstance(part, str):
                    parts.append(part)
                cursor = payload.get("next_cursor")
                if cursor is None:
                    return parts, payload
                require(isinstance(cursor, str) and cursor.startswith("c11p."), f"server c11p cursor: {name}")
                properties = schema.get("properties")
                require(isinstance(properties, dict), f"cursor schema properties: {name}")
                cursor_fields = [
                    field for field, field_schema in properties.items()
                    if isinstance(field_schema, dict) and field_schema.get("format") == "cortex-page-cursor"
                ]
                require(len(cursor_fields) == 1, f"one canonical cursor field: {name}")
                arguments = {**arguments, cursor_fields[0]: cursor}

        def require_no_protected_value(value: object, label: str) -> None:
            serialized = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
            require(all(item not in serialized for item in protected_values), label)

        before_project_paths = tuple(sorted(path.relative_to(project).as_posix() for path in project.rglob("*")))
        with JsonRpc("worker") as worker_contract:
            worker_listed = worker_contract.request("tools/list", {})
            worker_tools = worker_listed.get("tools")
            require(isinstance(worker_tools, list) and bool(worker_tools), "worker audience tools")
            worker_names = {tool.get("name") for tool in worker_tools if isinstance(tool, dict)}
            worker_instructions = str(worker_contract.initialized.get("instructions") or "")
            worker_schema_fields: set[str] = set()
            for tool in worker_tools:
                require(isinstance(tool, dict) and isinstance(tool.get("inputSchema"), dict), "worker audience schema")
                schema = tool["inputSchema"]
                properties = schema.get("properties")
                required = schema.get("required")
                require(isinstance(properties, dict) and isinstance(required, list), "worker closed input schema")
                dispatch_schema = properties.get("dispatch_ref")
                require(
                    "dispatch_ref" in required
                    and isinstance(dispatch_schema, dict)
                    and dispatch_schema.get("format") == "cortex-dispatch-ref",
                    "worker dispatch authority is required by inputSchema",
                )
                worker_schema_fields.update(str(field) for field in properties)
                advertised = json.dumps(tool, ensure_ascii=False, sort_keys=True)
                require(
                    all(field not in advertised for field in ("task_ref", "coordinator_ref", "assignment_ref")),
                    "worker schema identity isolation",
                )
                require("dispatch_ref" not in str(tool.get("description") or ""), "worker description has no dispatch_ref field prose")
            require(
                all(field not in worker_instructions for field in worker_schema_fields),
                "worker initialize keeps argument fields exclusively in inputSchema",
            )

        with JsonRpc("coordinator") as coordinator_contract:
            coordinator_listed = coordinator_contract.request("tools/list", {})
            coordinator_tools = coordinator_listed.get("tools")
            require(isinstance(coordinator_tools, list) and bool(coordinator_tools), "coordinator audience tools")
            coordinator_names = {tool.get("name") for tool in coordinator_tools if isinstance(tool, dict)}
        require(
            worker_names == {name for name, contract in contracts.items() if contract.get("audience") == "worker"}
            and coordinator_names == {name for name, contract in contracts.items() if contract.get("audience") == "coordinator"}
            and not (worker_names & coordinator_names),
            "audience tool ownership separation",
        )

        with JsonRpc() as coordinator:
            listed = coordinator.request("tools/list", {})
            tools = listed.get("tools")
            require(isinstance(tools, list), "default audience public MCP tools")
            advertised = {tool.get("name"): tool for tool in tools if isinstance(tool, dict) and isinstance(tool.get("name"), str)}
            require(set(advertised) == set(contracts) == worker_names | coordinator_names, "public tool registry discovery")
            for name, contract in contracts.items():
                require(
                    advertised[name].get("description") == contract.get("description")
                    and advertised[name].get("inputSchema") == contract.get("inputSchema"),
                    f"tools/list canonical object parity: {name}",
                )

            start_name, start_schema = contract_for("start_orchestration")
            malformed_call = coordinator.request("tools/call", {"name": start_name, "arguments": []})
            malformed_payload = malformed_call.get("structuredContent")
            require(
                isinstance(malformed_payload, dict)
                and malformed_payload.get("ok") is False
                and malformed_payload.get("action") == "retry_same_operation"
                and malformed_payload.get("retryable") is True
                and malformed_payload.get("state_mutated") is False
                and malformed_payload.get("error_code") == "tool_arguments_invalid",
                "malformed arguments structured correction without raw -32602",
            )
            malformed_changes = malformed_payload.get("allowed_changes")
            require(isinstance(malformed_changes, list) and bool(malformed_changes), "malformed arguments correction")

            missing_arguments = sample(start_schema)
            require(isinstance(missing_arguments, dict), "missing-field request base")
            required = start_schema.get("required")
            require(isinstance(required, list) and bool(required), "missing-field canonical required set")
            missing_field = str(required[0])
            missing_arguments.pop(missing_field)
            missing = coordinator.tool(start_name, missing_arguments)
            require(
                missing.get("ok") is False
                and missing.get("action") == "retry_same_operation"
                and missing.get("retryable") is True
                and missing.get("state_mutated") is False
                and missing.get("error_code") == "tool_arguments_invalid"
                and isinstance(missing.get("allowed_changes"), list),
                "missing required field flat structured correction",
            )
            require(
                any(change.get("path") == "/" + missing_field for change in missing["allowed_changes"] if isinstance(change, dict)),
                "missing required field executable correction",
            )

            start_arguments = sample(start_schema)
            require(isinstance(start_arguments, dict), "start arguments")
            authored_mission = (
                "Before project verification, obtain the required user decision Ω and preserve "
                "that obligation through the immutable worker briefing."
            )
            authored_waves = start_arguments.get("waves")
            require(isinstance(authored_waves, list) and len(authored_waves) == 1, "one authored gate wave")
            authored_wave = authored_waves[0]
            require(isinstance(authored_wave, dict), "authored wave object")
            authored_wave["phase"] = "implementation"
            authored_workers = authored_wave.get("workers")
            require(isinstance(authored_workers, list) and len(authored_workers) == 1, "one authored worker")
            require(isinstance(authored_workers[0], dict), "authored worker object")
            authored_workers[0]["objective"] = authored_mission
            started = invoke(coordinator, start_name, start_schema, arguments=start_arguments)
            require(started.get("ok") is True and started.get("action") == "invoke_dispatches", "authored start lifecycle")
            issued_strings = [item for item in nested_values(started) if isinstance(item, str)]
            dispatch_refs = [item for item in issued_strings if re.fullmatch(r"dispatch-[0-9a-f]{24}", item)]
            require(len(set(dispatch_refs)) == 1, "one native dispatch authority")
            dispatch_ref = dispatch_refs[0]
            bootstrap_candidates = [item for item in issued_strings if dispatch_ref in item and len(item) > len(dispatch_ref)]
            require(bool(bootstrap_candidates), "native-equivalent spawn bootstrap")
            bootstrap = max(bootstrap_candidates, key=len)
            require(bootstrap.count(dispatch_ref) == 1, "single dispatch_ref vertical bootstrap")
            require(
                all(name not in bootstrap for name in ("task_ref", "assignment_ref", "coordinator_ref")),
                "worker bootstrap private-ref scrub",
            )

            inspect_name, inspect_schema = contract_for("manage_orchestration", action="inspect")
            inspection_parts, _ = page_call(coordinator, inspect_name, inspect_schema, sample(inspect_schema))
            require_no_protected_value("".join(inspection_parts), "protected values absent from inspection pages")

            with JsonRpc("worker") as worker:
                briefing_name, briefing_schema = contract_for("read_dispatch_briefing")
                briefing_parts, _ = page_call(worker, briefing_name, briefing_schema, sample(briefing_schema))
                briefing_text = "".join(briefing_parts)
                require(bool(briefing_text), "complete dispatch briefing")
                require(authored_mission in briefing_text, "coordinator-authored mission preserved in briefing")
                require(
                    all(marker in briefing_text for marker in (
                        "durable-question marker", "same-child follow-up", "real user answer",
                    )),
                    "worker question pauses native turn before project work",
                )

                question = ("Какую гарантию сохранить؟ 保留互換性を確認してください。\0" * 220)
                answer = ("Сохранить совместимость ✅ 互換性を維持する\0" * 240)
                require(len(question.encode("utf-8")) > 8_192 and len(answer.encode("utf-8")) > 8_192, "large Unicode NUL exchange")
                ask_name, ask_schema = contract_for("worker_question", action="ask")
                ask_arguments = sample(ask_schema)
                require(isinstance(ask_arguments, dict), "question arguments")
                set_free_text(ask_arguments, ask_schema, question)
                asked = invoke(worker, ask_name, ask_schema, arguments=ask_arguments)
                require(asked.get("ok") is True, "durable Unicode NUL question")

                show_name, show_schema = contract_for("manage_orchestration", action="question_show")
                shown_parts, _ = page_call(coordinator, show_name, show_schema, sample(show_schema))
                require(question in "".join(shown_parts), "byte-exact paged question display")
                require_no_protected_value("".join(shown_parts), "protected values absent from question pages")

                answer_name, answer_schema = contract_for("manage_orchestration", action="question_answer")
                answer_arguments = sample(answer_schema)
                require(isinstance(answer_arguments, dict), "answer arguments")
                set_free_text(answer_arguments, answer_schema, answer)
                answered = invoke(coordinator, answer_name, answer_schema, arguments=answer_arguments)
                require(answered.get("ok") is True, "durable Unicode NUL answer")

                poll_name, poll_schema = contract_for("worker_question", action="poll")
                poll_parts, polled = page_call(worker, poll_name, poll_schema, sample(poll_schema))
                require(polled.get("ok") is True and answer in "".join(poll_parts), "byte-exact paged answer poll")

                submit_name, submit_schema = contract_for("complete_attempt", action="submit")
                submit_arguments = sample(submit_schema)
                require(isinstance(submit_arguments, dict), "submit arguments")
                set_free_text(submit_arguments, submit_schema, " \t\n")
                rejected = invoke(worker, submit_name, submit_schema, arguments=submit_arguments)
                require(rejected.get("ok") is False and rejected.get("action") == "repair_patch_only", "real semantic repair issuance")
                require(
                    any(isinstance(item, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", item) for item in nested_values(rejected)),
                    "repair escrow digest",
                )
                report = "Готово ✅ 漢字 العربية — " * 1_100
                require(len(report.encode("utf-8")) > 8_192, "large Unicode report")
                repair_name, repair_schema = contract_for("complete_attempt", action="repair")
                repair_arguments = sample(repair_schema)
                require(isinstance(repair_arguments, dict), "repair arguments")
                set_required_array(
                    repair_arguments,
                    repair_schema,
                    [{"op": "replace", "path": "/report", "value": report}],
                )
                repaired = invoke(worker, repair_name, repair_schema, arguments=repair_arguments)
                require(repaired.get("ok") is True and repaired.get("terminal") is True, "real repaired completion")

            require(bool(protected_values), "issued protected authority")
            coordinator.close()
            coordinator = JsonRpc()
            read_name, read_schema = contract_for("read_worker_result", action="read_wave")
            result_pages, result = page_call(coordinator, read_name, read_schema, sample(read_schema))
            try:
                projection = json.loads("".join(result_pages))
            except json.JSONDecodeError as error:
                raise AssertionError("release gate failed: result projection JSON") from error
            projected_reports = [item.get("report") for item in nested_values(projection) if isinstance(item, dict)]
            require(report in projected_reports, "byte-exact Unicode report pagination")
            require_no_protected_value(projection, "protected values absent from result projection")
            result_ref_lists = [
                item for item in nested_values(result)
                if isinstance(item, list)
                and bool(item)
                and all(isinstance(ref, str) for ref in item)
                and any(str(ref).startswith("attempt-result-") for ref in item)
            ]
            require(bool(result_ref_lists), "server-derived result references")
            continue_name, continue_schema = contract_for("continue_orchestration")
            continue_arguments = sample(continue_schema)
            require(isinstance(continue_arguments, dict), "continue arguments")
            set_required_array(continue_arguments, continue_schema, list(result_ref_lists[0]))
            handoff = invoke(coordinator, continue_name, continue_schema, arguments=continue_arguments)
            require(handoff.get("ok") is True and handoff.get("action") == "deliver_handoff", "restart durable handoff")
            handoff_report = handoff.get("report")
            require(isinstance(handoff_report, str), "handoff receipt")
            try:
                handoff_projection = json.loads(handoff_report)
            except json.JSONDecodeError as error:
                raise AssertionError("release gate failed: handoff receipt JSON") from error
            require(isinstance(handoff_projection, dict) and handoff_projection.get("close_verified") is True, "verified close")
            require_no_protected_value(handoff_projection, "protected values absent from handoff projection")

            pending = [name for name in contracts if name not in called]
            stalled: dict[str, str] = {}
            for _ in range(len(contracts) + 1):
                if not pending:
                    break
                progress = False
                available_prerequisites = {"none", "coordinator", "worker"}
                available_formats = {
                    str(candidate.get("format"))
                    for contract in contracts.values()
                    for candidate in nested_values(contract.get("inputSchema"))
                    if isinstance(candidate, dict)
                    and isinstance(candidate.get("format"), str)
                    and any(scalar_matches(value, candidate) for value in scalar_pool)
                }
                if "cortex-question-ref" in available_formats:
                    available_prerequisites.add("question")
                if {"cortex-repair-capsule", "cortex-payload-digest"}.issubset(available_formats):
                    available_prerequisites.add("repair")
                if "cortex-attempt-result-ref" in available_formats:
                    available_prerequisites.add("predecessor")
                ordered_pending = sorted(
                    pending,
                    key=lambda candidate: bool(contracts[candidate]["execution"]["terminal"]),
                )
                for name in ordered_pending:
                    prerequisite = str(contracts[name]["execution"]["prerequisite"])
                    if prerequisite not in available_prerequisites:
                        stalled[name] = "prerequisite:" + prerequisite
                        continue
                    schema = contracts[name].get("inputSchema")
                    require(isinstance(schema, dict), f"pending canonical schema: {name}")
                    try:
                        arguments = sample(schema)
                    except MissingSchemaValue as error:
                        stalled[name] = str(error)
                        continue
                    require(isinstance(arguments, dict), f"pending generated arguments: {name}")
                    invoke(coordinator, name, schema, arguments=arguments)
                    pending.remove(name)
                    stalled.pop(name, None)
                    progress = True
                if not progress:
                    break
            require(not pending, "every advertised tool receives a schema-valid black-box call: " + json.dumps(stalled, sort_keys=True))
            require(called == set(contracts), "complete advertised tool execution coverage")
            require(worker_dispatch_calls == worker_names, "every worker tool proves schema-bound dispatch authority by an actual call")
            require_no_protected_value(coordinator.response_frames, "protected values absent from restarted public frames")
            coordinator.close()

        databases = sorted(host_store.rglob(DATABASE_NAME))
        require(bool(databases), "durable host database")
        for database in databases:
            with sqlite3.connect(database) as connection:
                user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                objects = {
                    str(row[0])
                    for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
                }
                migrations = connection.execute(
                    "SELECT version, name FROM schema_migrations ORDER BY version"
                ).fetchall()
            require(user_version == DATABASE_SCHEMA_VERSION == 18, "v18 durable schema")
            require(not any("batch" in name.lower() for name in objects), "retired batch objects absent")
            require(
                bool(migrations)
                and migrations[-1] == (DATABASE_SCHEMA_VERSION, "canonical-current-ledger"),
                "v18 canonical fresh-ledger migration evidence",
            )

        after_project_paths = tuple(sorted(path.relative_to(project).as_posix() for path in project.rglob("*")))
        require(before_project_paths == after_project_paths == (), "zero project writes")
    require(source_snapshot() == before_source, "zero source checkout writes")
