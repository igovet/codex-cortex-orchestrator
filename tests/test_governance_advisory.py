from __future__ import annotations

import tempfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

from cortex_runtime import governance, ledger_db


def _initiative(root: Path, ref: str) -> dict[str, object]:
    return governance.create_initiative(
        root,
        initiative_ref=ref,
        title=ref,
        goal="advisory transition",
        owner="coordinator",
    )


def test_unresolved_dependency_is_advisory_and_does_not_block_transition() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = _initiative(root, "initiative-source")
        target = _initiative(root, "initiative-target")
        governance.transition_initiative(root, initiative_ref=source["initiative_ref"], status="active")
        governance.transition_initiative(root, initiative_ref=target["initiative_ref"], status="active")
        ledger_db.ensure_database(root)
        governance.add_dependency(
            root,
            source_type="initiative",
            source_ref=source["initiative_ref"],
            target_type="initiative",
            target_ref=target["initiative_ref"],
            dependency_type="requires",
        )
        result = governance.transition_initiative(root, initiative_ref=source["initiative_ref"], status="completed")
        assert result["status"] == "active"
        assert result["applied"] is False
        assert result["advisories"][0]["code"] == "dependency_unresolved"
        assert result["recommended_next"] == "dispatch_corrective_worker"


def test_missing_close_evidence_is_advisory_but_integrity_failure_is_hard() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        initiative = _initiative(root, "initiative-close")
        governance.transition_initiative(root, initiative_ref=initiative["initiative_ref"], status="active")
        completed = governance.transition_initiative(root, initiative_ref=initiative["initiative_ref"], status="completed")
        assert completed["status"] == "completed"
        advisory = governance.transition_initiative(root, initiative_ref=initiative["initiative_ref"], status="closed")
        assert advisory["status"] == "completed"
        assert advisory["applied"] is False
        assert advisory["advisories"][0]["code"] == "close_evidence_required"
