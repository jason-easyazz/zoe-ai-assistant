"""Regression tests for Multica ticket widget copy."""

import pytest
from pathlib import Path

pytestmark = pytest.mark.ci_safe


def test_multica_widget_uses_ticket_language():
    widget = (
        Path(__file__).resolve().parents[2]
        / "zoe-ui/dist/js/widgets/core/multica-board.js"
    ).read_text()

    assert "Multica Tickets" in widget
    assert "No open tickets" in widget
    assert "Loading tickets" in widget
    assert "Evolution Board" not in widget
    assert "_safeHttpUrl" in widget
    assert "if (!value) return '';" in widget
    assert "this._esc(data.reason || '')" in widget
    assert "evolution board" not in widget.lower()
    assert "Loading board" not in widget
    assert "Board unavailable" not in widget
    assert "data.groups" in widget
    assert "child_count" in widget
    assert "issue.blocker" in widget
    assert "issue.pr_url" in widget
