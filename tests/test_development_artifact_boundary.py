from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE = ROOT / "docs" / "architecture"


def test_development_only_architecture_artifacts_are_not_runtime_dependencies() -> None:
    """Development-only architecture artifacts are not shipped dependencies."""
    forbidden = {"docs/" + "architecture", "orchestration-" + "feature-parity.md"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or ARCHITECTURE in path.parents:
            continue
        if any(part in {".git", "__pycache__"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not any(token in text for token in forbidden), path
