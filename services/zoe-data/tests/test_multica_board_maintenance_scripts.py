import pytest
from pathlib import Path

pytestmark = pytest.mark.ci_safe


def test_legacy_board_maintenance_is_dry_run_and_preserves_descriptions():
    root = Path(__file__).resolve().parents[3]
    scripts = (
        root / "scripts/maintenance/multica_apply_triage_dispositions.py",
        root / "scripts/maintenance/multica_finalize_board_repair.py",
    )

    for script in scripts:
        source = script.read_text(encoding="utf-8")
        assert '--execute' in source
        assert "append_issue_note" in source
        assert "attach_label" in source
        assert "description=(" not in source
