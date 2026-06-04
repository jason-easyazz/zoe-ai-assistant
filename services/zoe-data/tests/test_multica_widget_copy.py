"""Regression tests for Multica ticket widget copy."""

from pathlib import Path


def test_multica_widget_uses_ticket_language():
    widget = (
        Path(__file__).resolve().parents[2]
        / "zoe-ui/dist/js/widgets/core/multica-board.js"
    ).read_text()

    assert "Multica Tickets" in widget
    assert "No active tickets" in widget
    assert "Loading tickets" in widget
    assert "Evolution Board" not in widget
    assert "evolution board" not in widget.lower()
    assert "Loading board" not in widget
    assert "Board unavailable" not in widget
