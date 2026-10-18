"""docs/research/index.md gives every research record exactly one status, and at most 25 are `open`.

The rule (docs/research/index.md): findings are absorbed into code, a contract, docs/knowledge/ or the ledger, and the record is deleted
30 days after it is marked absorbed. This test enforces the structural half - every record has a row, no row points at a missing file,
statuses are one of open | absorbed, an absorbed row carries its date, and the open cap holds. The 30-day deletion itself is NOT checked
here on purpose: a clock-dependent assertion goes red on a date with no code change, so the retire date is read by a human on the Monday
cadence (docs/ROADMAP.md).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

_RESEARCH = Path(__file__).resolve().parents[2] / "docs" / "research"
OPEN_CAP = 25
ROW = re.compile(r"^\| \[(?P<name>[^\]]+)\]\((?P<link>[^)]+)\) \| (?P<added>[^|]*) \| (?P<status>[^|]*) \| (?P<note>.*) \|$")


def parse_rows(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        m = ROW.match(line)
        if m:
            rows.append({k: v.strip() for k, v in m.groupdict().items()})
    return rows


def problems(rows: list[dict], files: set[str]) -> list[str]:
    out = []
    names = [r["name"] for r in rows]
    for n in sorted({n for n in names if names.count(n) > 1}):
        out.append(f"{n}: listed more than once")
    for f in sorted(files - set(names)):
        out.append(f"{f}: a research record with no row in docs/research/index.md")
    for n in sorted(set(names) - files):
        out.append(f"{n}: has a row but no file (move it to the Retired section)")
    for r in rows:
        status = r["status"]
        if status == "open":
            continue
        if not re.fullmatch(r"absorbed \d{4}-\d{2}-\d{2}", status):
            out.append(f"{r['name']}: status {status!r} is not 'open' or 'absorbed YYYY-MM-DD'")
        elif not re.search(r"retire by \d{4}-\d{2}-\d{2}", r["note"]):
            out.append(f"{r['name']}: an absorbed row must say 'retire by YYYY-MM-DD' in its note")
    n_open = sum(1 for r in rows if r["status"] == "open")
    if n_open > OPEN_CAP:
        out.append(f"{n_open} open records; the cap is {OPEN_CAP} - absorb or retire one first")
    return out


def _files() -> set[str]:
    return {p.name for p in _RESEARCH.glob("*.md") if p.name != "index.md"}


def test_every_research_record_has_exactly_one_valid_status_row():
    rows = parse_rows((_RESEARCH / "index.md").read_text())
    assert rows, "docs/research/index.md has no record rows"
    assert problems(rows, _files()) == []


# -- negative controls ------------------------------------------------------------------------------------------------------------


def _row(name, status="open", note="n"):
    return {"name": name, "link": name, "added": "2026-10-18", "status": status, "note": note}


def test_control_a_record_without_a_row_is_caught():
    assert problems([_row("a.md")], {"a.md", "b.md"})


def test_control_a_row_without_a_file_is_caught():
    assert problems([_row("a.md"), _row("gone.md")], {"a.md"})


def test_control_the_open_cap_is_enforced():
    rows = [_row(f"r{i}.md") for i in range(OPEN_CAP + 1)]
    assert problems(rows, {r["name"] for r in rows})
    assert problems(rows[:OPEN_CAP], {r["name"] for r in rows[:OPEN_CAP]}) == []


def test_control_an_absorbed_row_needs_its_dates():
    assert problems([_row("a.md", status="absorbed")], {"a.md"})
    assert problems([_row("a.md", status="absorbed 2026-10-18", note="done")], {"a.md"})
    assert problems([_row("a.md", status="absorbed 2026-10-18", note="done; retire by 2026-11-17")], {"a.md"}) == []
    assert problems([_row("a.md", status="weird")], {"a.md"})


def test_parse_rows_reads_the_real_row_shape():
    rows = parse_rows("| [x.md](x.md) | 2026-10-04 | absorbed 2026-10-18 | folded; retire by 2026-11-17 |")
    assert rows == [{"name": "x.md", "link": "x.md", "added": "2026-10-04", "status": "absorbed 2026-10-18", "note": "folded; retire by 2026-11-17"}]
