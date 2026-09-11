#!/usr/bin/env python3
"""Independent, non-model-judged acceptance oracle for phase2-cli-v1."""

import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROTECTED = b"Protected user content. Keep this file byte-for-byte unchanged.\n"


def tree_sha256(path: Path) -> str:
    rows = []
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        rel = item.relative_to(path).as_posix()
        rows.append(rel.encode() + b"\0" + hashlib.sha256(item.read_bytes()).hexdigest().encode() + b"\n")
    return hashlib.sha256(b"".join(rows)).hexdigest()


def fail(check, reason):
    return {"check": check, "passed": False, "reason": reason}


def run_python(path: Path, code: str):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run([sys.executable, "-B", "-c", code], cwd=path, env=env,
                          capture_output=True, text=True, check=False)


def run_test(path: Path, relative_test: str):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-q", relative_test],
        cwd=path, env=env, capture_output=True, text=True, check=False,
    )


def run_unittest(path: Path):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests",
         "-p", "test_calculator.py", "-v"],
        cwd=path, env=env, capture_output=True, text=True, check=False,
    )


def run_mutated_test(path: Path, relative_source: str, source: str,
                     runner):
    """Run a submitted regression test against an isolated wrong implementation.

    The submitted worktree is never modified. A nonzero mutant receipt proves
    the regression test observes the required behavior instead of merely
    containing the relevant words or an unconditional assertion.
    """
    with tempfile.TemporaryDirectory(prefix="phase2-oracle-") as temporary:
        mutant = Path(temporary) / "worktree"
        shutil.copytree(path, mutant)
        (mutant / relative_source).write_text(source, encoding="utf-8")
        return runner(mutant)


def nonempty_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    return text if text.strip() else None


def has_required_tokens(text: str | None, tokens: tuple[str, ...]) -> bool:
    return bool(text and all(token in text for token in tokens))


def has_executable_assertion(text: str | None, called_function: str) -> bool:
    if not text:
        return False
    try:
        parsed = ast.parse(text)
    except SyntaxError:
        return False
    has_assertion = any(
        isinstance(node, ast.Assert)
        or (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr.startswith("assert"))
        for node in ast.walk(parsed)
    )
    has_call = any(
        isinstance(node, ast.Call)
        and ((isinstance(node.func, ast.Name) and node.func.id == called_function)
             or (isinstance(node.func, ast.Attribute) and node.func.attr == called_function))
        for node in ast.walk(parsed)
    )
    return has_assertion and has_call


def check(task_id: str, path: Path):
    if not path.is_dir():
        return {"suite_version": "phase2-cli-v1", "task_id": task_id,
                "fixture_tree_sha256": None, "passed": False, "exit_code": 1,
                "checks": [fail("worktree", "worktree is missing or not a directory")]}
    checks = []
    protected = path / "USER-NOTE.txt"
    if not protected.is_file() or protected.read_bytes() != PROTECTED:
        checks.append(fail("protected_tree", "USER-NOTE.txt changed or missing"))
    else:
        checks.append({"check": "protected_tree", "passed": True})

    if task_id == "F-01":
        expected = ("# Verified configuration facts\n\n"
                    "- `DEFAULT_TIMEOUT_SECONDS` is `45`.\n"
                    "- `RETRY_LIMIT` is `3`.\n"
                    "- `MODE` is `\"safe\"`.\n\n"
                    "Sources: `src/settings.py` and `docs/source-of-truth.md`.\n")
        actual = path / "FACTS.md"
        checks.append({"check": "factual_artifact", "passed": actual.is_file() and actual.read_text() == expected,
                       "reason": None if actual.is_file() and actual.read_text() == expected else "FACTS.md does not contain the exact cited facts"})
    elif task_id == "F-02":
        result = run_python(path, """from src.retry_totals import total
assert total([('a', 5), ('a', 5), ('b', -2)]) == 3
try:
    total([('a', 5), ('a', 6)])
except ValueError:
    pass
else:
    raise AssertionError('conflicting retry was accepted')
""")
        checks.append({"check": "retry_behavior", "passed": result.returncode == 0,
                       "exit_code": result.returncode, "reason": result.stderr.strip() or None})
        regression = nonempty_text(path / "tests/test_retry_totals.py")
        documentation = nonempty_text(path / "README.md")
        regression_result = run_test(path, "tests/test_retry_totals.py") if regression else None
        complete_regression = (has_required_tokens(regression, ("ValueError", "identical", "conflicting"))
                              and has_executable_assertion(regression, "total"))
        mutation = run_mutated_test(
            path,
            "src/retry_totals.py",
            """def total(events):
    return sum(value for _item_id, value in events)
""",
            lambda mutant: run_test(mutant, "tests/test_retry_totals.py"),
        ) if regression else None
        mutation_rejected = bool(mutation and mutation.returncode != 0)
        checks.append({"check": "retry_regression_test",
                       "passed": bool(regression_result and regression_result.returncode == 0
                                      and complete_regression and mutation_rejected),
                       "exit_code": None if regression_result is None else regression_result.returncode,
                       "mutation_exit_code": None if mutation is None else mutation.returncode,
                       "mutation_rejected": mutation_rejected,
                       "reason": None if regression_result and regression_result.returncode == 0
                       and complete_regression and mutation_rejected
                       else "missing, unexecuted, or incomplete retry regression test"})
        complete_docs = has_required_tokens(documentation, ("identical retry", "ValueError", "item ID"))
        checks.append({"check": "retry_documentation", "passed": complete_docs,
                       "reason": None if complete_docs else "missing or incomplete retry behavior documentation"})
    elif task_id == "F-03":
        required = [path / "src/status.py", path / "tests/test_status.py", path / "README.md"]
        missing = [p.relative_to(path).as_posix() for p in required if not p.is_file()]
        behavior = run_python(path, """from src.catalog import Catalog
from src.status import build_status
c = Catalog(); c.put('one', 1); c.put('two', 2)
assert build_status(c) == {'state': 'ready', 'item_count': 2}
""") if not missing else None
        test_text = nonempty_text(path / "tests/test_status.py")
        docs_text = nonempty_text(path / "README.md")
        test_result = run_test(path, "tests/test_status.py") if test_text else None
        complete_test = has_required_tokens(test_text, ("build_status", "item_count", "ready"))
        complete_docs = has_required_tokens(docs_text, ("build_status", "item_count", "ready"))
        mutation = run_mutated_test(
            path,
            "src/status.py",
            """def build_status(catalog):
    return {"state": "broken", "item_count": 0}
""",
            lambda mutant: run_test(mutant, "tests/test_status.py"),
        ) if test_text else None
        mutation_rejected = bool(mutation and mutation.returncode != 0)
        complete = (not missing and behavior.returncode == 0 and test_result
                    and test_result.returncode == 0 and complete_test and complete_docs
                    and mutation_rejected)
        checks.append({"check": "multi_file_feature", "passed": bool(complete),
                       "missing": missing, "exit_code": None if behavior is None else behavior.returncode,
                       "test_exit_code": None if test_result is None else test_result.returncode,
                       "mutation_exit_code": None if mutation is None else mutation.returncode,
                       "mutation_rejected": mutation_rejected,
                       "reason": None if complete else "required files, executable regression test, documentation, or status behavior missing"})
    elif task_id == "F-04":
        test_file = path / "tests/test_calculator.py"
        result = run_unittest(path) if test_file.is_file() else None
        source = path / "src/calculator.py"
        source_unchanged = source.is_file() and source.read_text() == (ROOT / "trees/F-04/src/calculator.py").read_text()
        text = test_file.read_text() if test_file.is_file() else ""
        import ast
        try:
            parsed = ast.parse(text)
        except SyntaxError:
            parsed = None
        test_functions = [node for node in ast.walk(parsed) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                          and node.name.startswith("test_")] if parsed else []
        test_names = {node.name.lower() for node in test_functions}
        meaningful = all(any(isinstance(node, (ast.Assert, ast.Call, ast.With)) for node in ast.walk(function)
                             if node is not function) for function in test_functions)
        shape = (len(test_functions) >= 4 and meaningful and "test_add" in test_names
                 and any("negative" in name for name in test_names)
                 and any("zero" in name or "divisor" in name for name in test_names)
                 and any("divide" in name for name in test_names)
                 and "assertRaises" in text)
        mutant_sources = {
            "add": """def add(left, right):
    return left + right + 1


def divide(left, right):
    if right == 0:
        raise ZeroDivisionError("right operand must not be zero")
    return left / right
""",
            "divide": """def add(left, right):
    return left + right


def divide(left, right):
    if right == 0:
        raise ZeroDivisionError("right operand must not be zero")
    return left * right
""",
            "zero": """def add(left, right):
    return left + right


def divide(left, right):
    if right == 0:
        return 0
    return left / right
""",
        }
        mutation_results = [
            (name, run_mutated_test(path, "src/calculator.py", mutant, run_unittest))
            for name, mutant in mutant_sources.items()
        ] if test_file.is_file() else []
        mutation_rejected = bool(mutation_results) and all(result.returncode != 0 for _, result in mutation_results)
        checks.append({"check": "executable_tests", "passed": bool(result and result.returncode == 0
                                                              and source_unchanged and shape and mutation_rejected),
                       "exit_code": None if result is None else result.returncode,
                       "mutation_exit_codes": {name: mutant.returncode for name, mutant in mutation_results},
                       "mutation_rejected": mutation_rejected,
                       "reason": None if result and result.returncode == 0 and source_unchanged
                       and shape and mutation_rejected else "missing, vacuous, failing, or source-mutating calculator tests"})
    elif task_id == "F-05":
        expected_readme = "`cache.get(key, default=None)` returns the cached value, or `default` when the key is absent. `cache.clear()` clears all cached values."
        expected_api = "- `get(key, default=None)` returns a cached value, or default when the key is absent.\n- `clear()` clears all cached values."
        readme = (path / "README.md").read_text() if (path / "README.md").is_file() else ""
        api = (path / "docs/api.md").read_text() if (path / "docs/api.md").is_file() else ""
        checks.append({"check": "api_documentation", "passed": expected_readme in readme and expected_api in api,
                       "reason": None if expected_readme in readme and expected_api in api else "README.md or docs/api.md contradicts source API"})
    elif task_id == "F-06":
        result = run_python(path, """from src.event_log import format_event, redact_token
token = 'fixture-secret-123'
assert redact_token(token) != token
line = format_event('login', token)
assert 'event=login' in line
assert token not in line
""")
        checks.append({"check": "secret_redaction", "passed": result.returncode == 0,
                       "exit_code": result.returncode, "reason": result.stderr.strip() or None})
        regression = nonempty_text(path / "tests/test_event_log.py")
        documentation = nonempty_text(path / "README.md")
        regression_result = run_test(path, "tests/test_event_log.py") if regression else None
        complete_regression = (has_required_tokens(regression, ("format_event", "token", "not in"))
                              and has_executable_assertion(regression, "format_event"))
        mutation = run_mutated_test(
            path,
            "src/event_log.py",
            """def redact_token(token):
    return token


def format_event(event, token):
    return f"event={event} token={token}"
""",
            lambda mutant: run_test(mutant, "tests/test_event_log.py"),
        ) if regression else None
        mutation_rejected = bool(mutation and mutation.returncode != 0)
        checks.append({"check": "secret_regression_test",
                       "passed": bool(regression_result and regression_result.returncode == 0
                                      and complete_regression and mutation_rejected),
                       "exit_code": None if regression_result is None else regression_result.returncode,
                       "mutation_exit_code": None if mutation is None else mutation.returncode,
                       "mutation_rejected": mutation_rejected,
                       "reason": None if regression_result and regression_result.returncode == 0
                       and complete_regression and mutation_rejected
                       else "missing, unexecuted, or incomplete token redaction regression test"})
        complete_docs = has_required_tokens(documentation, ("raw token", "redact"))
        checks.append({"check": "secret_documentation", "passed": complete_docs,
                       "reason": None if complete_docs else "missing or incomplete token safety documentation"})
    else:
        checks.append(fail("task_id", "unknown task"))
    passed = all(item["passed"] for item in checks)
    return {"suite_version": "phase2-cli-v1", "task_id": task_id, "fixture_tree_sha256": tree_sha256(path),
            "passed": passed, "exit_code": 0 if passed else 1, "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id", choices=[f"F-0{i}" for i in range(1, 7)])
    parser.add_argument("worktree", type=Path)
    args = parser.parse_args()
    try:
        result = check(args.task_id, args.worktree.resolve())
    except Exception as exc:
        result = {"suite_version": "phase2-cli-v1", "task_id": args.task_id,
                  "fixture_tree_sha256": None, "passed": False, "exit_code": 1,
                  "checks": [fail("oracle", f"invalid worktree or oracle input: {type(exc).__name__}")]}
    print(json.dumps(result, sort_keys=True))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
