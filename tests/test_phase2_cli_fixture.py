import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from fixtures.phase2_cli_v1.manifest_tools import ROOT, sha256_file, validate_manifest


# Keep the test independent of the implementation's oracle helpers.
MANIFEST = json.loads((ROOT / "manifest.json").read_text())
ORACLE = ROOT / "oracle.py"
RESET = ROOT / "reset.py"


def run_reset(task_id, destination):
    return subprocess.run(
        [sys.executable, str(RESET), "--task", task_id, "--destination", str(destination)],
        capture_output=True, text=True, check=False,
    )


def run_oracle(task_id, destination):
    result = subprocess.run(
        [sys.executable, str(ORACLE), task_id, str(destination)],
        capture_output=True, text=True, check=False,
    )
    return result, json.loads(result.stdout)


def test_manifest_freezes_exactly_six_families_and_no_mapping():
    assert MANIFEST["suite_version"] == "phase2-cli-v1"
    assert [row["task_id"] for row in MANIFEST["families"]] == [f"F-0{i}" for i in range(1, 7)]
    assert [row["family"] for row in MANIFEST["families"]] == [
        "factual-investigation", "bug-fix-regression", "multi-file-feature",
        "test-authoring", "documentation-api-correction", "security-sensitive-change",
    ]
    assert "mapping_policy" in MANIFEST
    for row in MANIFEST["families"]:
        assert not any(key in row for key in ("arm", "condition", "baseline", "candidate", "randomization"))


def test_manifest_hashes_are_consistent():
    result = subprocess.run([sys.executable, str(ROOT / "manifest_tools.py")],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload == {"errors": [], "valid": True}


def test_manifest_rejects_prompt_remapping_even_when_hash_is_recalculated():
    tampered = copy.deepcopy(MANIFEST)
    tampered["families"][0]["prompt_path"] = "prompts/F-02.txt"
    tampered["families"][0]["prompt_sha256"] = sha256_file(ROOT / "prompts/F-02.txt")
    assert "F-01:canonical_identity" in validate_manifest(tampered)


def test_manifest_rejects_hash_tampering():
    tampered = copy.deepcopy(MANIFEST)
    tampered["families"][0]["prompt_sha256"] = "0" * 64
    assert "F-01:canonical_identity" in validate_manifest(tampered)


@pytest.mark.parametrize("task_id", [f"F-0{i}" for i in range(1, 7)])
def test_reset_is_clean_deterministic_and_non_overwriting(task_id, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    receipt = run_reset(task_id, first)
    assert receipt.returncode == 0, receipt.stderr
    assert json.loads(receipt.stdout)["exit_code"] == 0
    receipt_again = run_reset(task_id, first)
    assert receipt_again.returncode == 1
    assert json.loads(receipt_again.stdout)["exit_code"] == 1
    assert run_reset(task_id, second).returncode == 0
    assert sorted(p.relative_to(first).as_posix() for p in first.rglob("*")) == sorted(
        p.relative_to(second).as_posix() for p in second.rglob("*")
    )
    first_files = {p.relative_to(first): p.read_bytes() for p in first.rglob("*") if p.is_file()}
    second_files = {p.relative_to(second): p.read_bytes() for p in second.rglob("*") if p.is_file()}
    assert first_files == second_files
    result, payload = run_oracle(task_id, first)
    assert result.returncode == 1
    assert payload["exit_code"] == 1 and payload["passed"] is False


def make_success(task_id, path):
    if task_id == "F-01":
        (path / "FACTS.md").write_text(
            "# Verified configuration facts\n\n"
            "- `DEFAULT_TIMEOUT_SECONDS` is `45`.\n"
            "- `RETRY_LIMIT` is `3`.\n"
            "- `MODE` is `\"safe\"`.\n\n"
            "Sources: `src/settings.py` and `docs/source-of-truth.md`.\n"
        )
    elif task_id == "F-02":
        (path / "src/retry_totals.py").write_text(
            '"""Accumulate report values, de-duplicating identical retry events."""\n\n'
            "def total(events):\n"
            "    seen = {}\n"
            "    for item_id, value in events:\n"
            "        if item_id in seen and seen[item_id] != value:\n"
            "            raise ValueError(\"conflicting retry\")\n"
            "        seen[item_id] = value\n"
            "    return sum(seen.values())\n"
        )
        (path / "tests/test_retry_totals.py").write_text(
            "from src.retry_totals import total\n\n"
            "def test_identical_retry_is_counted_once():\n"
            "    assert total([('a', 5), ('a', 5), ('b', -2)]) == 3\n\n"
            "def test_conflicting_retry_raises_value_error():\n"
            "    try:\n"
            "        total([('a', 5), ('a', 6)])\n"
            "    except ValueError:\n"
            "        pass\n"
            "    else:\n"
            "        raise AssertionError('conflicting retry was accepted')\n"
        )
        (path / "README.md").write_text(
            "# Retry totals\n\nAn identical retry for an item ID is counted once; a conflicting value raises ValueError.\n"
        )
    elif task_id == "F-03":
        (path / "src/status.py").write_text(
            "def build_status(catalog):\n"
            "    return {\"state\": \"ready\", \"item_count\": catalog.count()}\n"
        )
        (path / "tests/test_status.py").write_text(
            "from src.catalog import Catalog\nfrom src.status import build_status\n\n"
            "def test_status():\n"
            "    c = Catalog(); c.put(\"x\", 1)\n"
            "    assert build_status(c) == {\"state\": \"ready\", \"item_count\": 1}\n"
        )
        (path / "README.md").write_text("# Catalog status\n\n`build_status(catalog)` returns state=ready and item_count.\n")
    elif task_id == "F-04":
        (path / "tests").mkdir(parents=True, exist_ok=True)
        (path / "tests/test_calculator.py").write_text(
            "import unittest\nfrom src.calculator import add, divide\n\n"
            "class CalculatorTests(unittest.TestCase):\n"
            "    def test_add(self): self.assertEqual(add(2, 3), 5)\n"
            "    def test_divide(self): self.assertEqual(divide(8, 2), 4)\n"
            "    def test_negative(self): self.assertEqual(divide(-8, 2), -4)\n"
            "    def test_zero(self):\n"
            "        with self.assertRaises(ZeroDivisionError): divide(1, 0)\n"
        )
    elif task_id == "F-05":
        (path / "README.md").write_text(
            "# Cache API\n\n`cache.get(key, default=None)` returns the cached value, or `default` when the key is absent. `cache.clear()` clears all cached values.\n"
        )
        (path / "docs/api.md").write_text(
            "# API reference\n\n- `get(key, default=None)` returns a cached value, or default when the key is absent.\n- `clear()` clears all cached values.\n"
        )
    elif task_id == "F-06":
        (path / "src/event_log.py").write_text(
            '"""Format security-sensitive event metadata."""\n\n'
            "def redact_token(token):\n    return \"[REDACTED]\"\n\n"
            "def format_event(event, token):\n    return f\"event={event} token={redact_token(token)}\"\n"
        )
        (path / "tests/test_event_log.py").write_text(
            "from src.event_log import format_event\n\n"
            "def test_raw_token_is_not_disclosed():\n"
            "    token = 'fixture-secret-123'\n"
            "    assert token not in format_event('login', token)\n"
        )
        (path / "README.md").write_text(
            "# Event logging\n\nThe logger redacts the raw token while retaining the event name.\n"
        )


@pytest.mark.parametrize("task_id", [f"F-0{i}" for i in range(1, 7)])
def test_each_oracle_accepts_good_state_and_rejects_injected_wrong_state(task_id, tmp_path):
    path = tmp_path / task_id
    assert run_reset(task_id, path).returncode == 0
    make_success(task_id, path)
    good_result, good = run_oracle(task_id, path)
    assert good_result.returncode == 0, good
    assert good["passed"] is True and good["exit_code"] == 0
    if task_id == "F-01":
        (path / "FACTS.md").write_text((path / "FACTS.md").read_text().replace("`45`", "`44`"))
    elif task_id == "F-02":
        (path / "src/retry_totals.py").write_text((path / "src/retry_totals.py").read_text().replace("raise ValueError", "return"))
    elif task_id == "F-03":
        (path / "src/status.py").write_text((path / "src/status.py").read_text().replace('"ready"', '"broken"'))
    elif task_id == "F-04":
        (path / "tests/test_calculator.py").write_text((path / "tests/test_calculator.py").read_text().replace("assertRaises", "assertEqual"))
    elif task_id == "F-05":
        (path / "docs/api.md").write_text((path / "docs/api.md").read_text().replace("clear()`", "clear(key)`"))
    elif task_id == "F-06":
        (path / "src/event_log.py").write_text(
            (path / "src/event_log.py").read_text().replace(
                'return f"event={event} token={redact_token(token)}"',
                'return f"event={event} token={token}"',
            )
        )
    bad_result, bad = run_oracle(task_id, path)
    assert bad_result.returncode == 1, bad
    assert bad["passed"] is False and bad["exit_code"] == 1


def test_ledger_schema_is_condition_neutral_and_null_safe():
    schema = json.loads((ROOT / "ledger_schema.json").read_text())
    assert schema["task_identity"]["condition_independent"] is True
    assert schema["cost"]["missing_value"] == schema["latency"]["missing_value"] == "null"
    assert schema["cost"]["null_is_zero"] is False
    assert schema["latency"]["null_is_zero"] is False
    assert schema["diagnostics"]["excluded_from_quality"] is True
    assert schema["blinded_scoring"]["no_condition_mapping"] is True
    assert not any(value in json.dumps(schema).lower() for value in ("baseline", "candidate", "randomization"))


def test_f02_requires_regression_test_and_documentation(tmp_path):
    path = tmp_path / "F-02"
    assert run_reset("F-02", path).returncode == 0
    (path / "src/retry_totals.py").write_text(
        'def total(events):\n'
        '    seen = {}\n'
        '    for item_id, value in events:\n'
        '        if item_id in seen and seen[item_id] != value:\n'
        '            raise ValueError("conflicting retry")\n'
        '        seen[item_id] = value\n'
        '    return sum(seen.values())\n'
    )
    result, payload = run_oracle("F-02", path)
    assert result.returncode == 1 and payload["passed"] is False


def test_f03_rejects_empty_required_files(tmp_path):
    path = tmp_path / "F-03"
    assert run_reset("F-03", path).returncode == 0
    (path / "src/status.py").write_text('def build_status(catalog):\n    return {"state": "ready", "item_count": catalog.count()}\n')
    (path / "tests/test_status.py").write_text("")
    (path / "README.md").write_text("")
    result, payload = run_oracle("F-03", path)
    assert result.returncode == 1 and payload["passed"] is False


def test_f04_rejects_comment_padded_noop_test(tmp_path):
    path = tmp_path / "F-04"
    assert run_reset("F-04", path).returncode == 0
    (path / "tests").mkdir(parents=True, exist_ok=True)
    (path / "tests/test_calculator.py").write_text(
        "import unittest\n\n"
        "# add divide assertRaises -\n"
        "class NoOp(unittest.TestCase):\n"
        "    def test_nothing(self):\n"
        "        pass\n"
    )
    result, payload = run_oracle("F-04", path)
    assert result.returncode == 1 and payload["passed"] is False


def test_f04_missing_worktree_returns_structured_json(tmp_path):
    result = subprocess.run([sys.executable, str(ORACLE), "F-04", str(tmp_path / "missing")],
                            capture_output=True, text=True, check=False)
    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["passed"] is False and payload["exit_code"] == 1
    assert "traceback" not in result.stdout.lower() + result.stderr.lower()


def test_f06_requires_regression_test_and_documentation(tmp_path):
    path = tmp_path / "F-06"
    assert run_reset("F-06", path).returncode == 0
    (path / "src/event_log.py").write_text(
        'def redact_token(token):\n    return "[REDACTED]"\n\n'
        'def format_event(event, token):\n    return f"event={event} token={redact_token(token)}"\n'
    )
    result, payload = run_oracle("F-06", path)
    assert result.returncode == 1 and payload["passed"] is False


@pytest.mark.parametrize("task_id", ["F-02", "F-03", "F-06"])
def test_semantic_regressions_reject_comment_tokens_and_irrelevant_assertion(task_id, tmp_path):
    path = tmp_path / task_id
    assert run_reset(task_id, path).returncode == 0
    if task_id == "F-02":
        (path / "src/retry_totals.py").write_text(
            "def total(events):\n"
            "    return sum(value for _item_id, value in events)\n"
        )
        (path / "tests/test_retry_totals.py").write_text(
            "from src.retry_totals import total\n\n"
            "# identical conflicting ValueError retry regression\n"
            "def test_comment_token_noop():\n"
            "    total([])\n"
            "    assert True\n"
        )
        (path / "README.md").write_text("identical retry raises ValueError for conflicting item ID\n")
    elif task_id == "F-03":
        (path / "src/status.py").write_text(
            "def build_status(catalog):\n"
            "    return {\"state\": \"ready\", \"item_count\": catalog.count()}\n"
        )
        (path / "tests/test_status.py").write_text(
            "from src.catalog import Catalog\nfrom src.status import build_status\n\n"
            "# build_status item_count ready\n"
            "def test_comment_token_noop():\n"
            "    build_status(Catalog())\n"
            "    assert True\n"
        )
        (path / "README.md").write_text("build_status returns ready item_count\n")
    else:
        (path / "src/event_log.py").write_text(
            "def redact_token(token):\n"
            "    return \"[REDACTED]\"\n\n"
            "def format_event(event, token):\n"
            "    return f\"event={event} token={redact_token(token)}\"\n"
        )
        (path / "tests/test_event_log.py").write_text(
            "from src.event_log import format_event\n\n"
            "# format_event token not in\n"
            "def test_comment_token_noop():\n"
            "    format_event('login', 'fixture-secret-123')\n"
            "    assert True\n"
        )
        (path / "README.md").write_text("raw token redact\n")
    result, payload = run_oracle(task_id, path)
    assert result.returncode == 1
    assert payload["passed"] is False
    semantic = next(check for check in payload["checks"]
                    if check["check"] in {"retry_regression_test", "multi_file_feature", "secret_regression_test"})
    assert semantic["mutation_rejected"] is False


def test_f04_rejects_four_vacuous_assert_true_tests(tmp_path):
    path = tmp_path / "F-04-vacuous"
    assert run_reset("F-04", path).returncode == 0
    (path / "tests").mkdir(parents=True, exist_ok=True)
    (path / "tests/test_calculator.py").write_text(
        "import unittest\n\n"
        "# add divide negative zero assertRaises\n"
        "class Vacuous(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        assert True\n"
        "    def test_divide(self):\n"
        "        assert True\n"
        "    def test_negative(self):\n"
        "        assert True\n"
        "    def test_zero(self):\n"
        "        assert True\n"
    )
    result, payload = run_oracle("F-04", path)
    assert result.returncode == 1
    assert payload["passed"] is False
    executable = next(check for check in payload["checks"] if check["check"] == "executable_tests")
    assert executable["mutation_rejected"] is False
