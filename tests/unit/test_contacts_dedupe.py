"""scripts/maintenance/contacts_dedupe.py: a REPORT-ONLY duplicate finder (synthetic rows)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "contacts_dedupe", REPO / "scripts/maintenance/contacts_dedupe.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["contacts_dedupe"] = mod
    spec.loader.exec_module(mod)
    return mod


cd = _load()

ROWS = [
    {"id": "a1", "user_id": "demo_u1", "name": "Wren", "relationship": "friend", "phone": None},
    {"id": "a2", "user_id": "demo_u1", "name": "Wren Alder", "relationship": "friend", "phone": "555"},
    {"id": "b1", "user_id": "demo_u1", "name": "Pell Ardent", "relationship": "brother"},
    {"id": "b2", "user_id": "demo_u1", "name": "Pell Hale", "relationship": "brother"},   # other surname
    {"id": "c1", "user_id": "demo_u2", "name": "Wren", "relationship": "niece"},          # other user
]


def test_requires_dry_run(capsys):
    assert cd.main([]) == 2
    assert "refused" in capsys.readouterr().err


def test_reports_the_stub_pair_and_nothing_else(tmp_path, capsys):
    f = tmp_path / "rows.json"
    f.write_text(json.dumps(ROWS))
    assert cd.main(["--dry-run", "--json-file", str(f)]) == 0
    out = capsys.readouterr().out
    assert "1 likely-duplicate group(s)" in out and "nothing was changed" in out
    assert "a1" in out and "a2" in out and "suggest keeping" in out
    assert out.index("a2") < out.index("a1")          # the fuller record is listed first
    assert "b1" not in out and "b2" not in out and "c1" not in out   # negative controls


def test_a_stub_next_to_two_fuller_people_is_not_reported_as_a_duplicate(tmp_path, capsys):
    """Per its own docstring: Dan + Dan Smith + Dan Jones must not group Smith with Jones."""
    rows = [
        {"id": "d0", "user_id": "demo_u1", "name": "Dan", "relationship": "friend"},
        {"id": "d1", "user_id": "demo_u1", "name": "Dan Smith", "relationship": "friend"},
        {"id": "d2", "user_id": "demo_u1", "name": "Dan Jones", "relationship": "friend"},
    ]
    f = tmp_path / "rows.json"
    f.write_text(json.dumps(rows))
    assert cd.main(["--dry-run", "--json-file", str(f)]) == 0
    assert "No likely-duplicate contacts found." in capsys.readouterr().out


def test_clean_data_reports_none(tmp_path, capsys):
    f = tmp_path / "rows.json"
    f.write_text(json.dumps(ROWS[2:]))
    assert cd.main(["--dry-run", "--json-file", str(f)]) == 0
    assert "No likely-duplicate contacts found." in capsys.readouterr().out


def test_unreadable_rows_exit_2(tmp_path, capsys):
    assert cd.main(["--dry-run", "--json-file", str(tmp_path / "missing.json")]) == 2


def test_script_has_no_write_path():
    src = (REPO / "scripts/maintenance/contacts_dedupe.py").read_text().lower()
    for verb in ("delete from", "update people", "insert into", "drop table"):
        assert verb not in src
