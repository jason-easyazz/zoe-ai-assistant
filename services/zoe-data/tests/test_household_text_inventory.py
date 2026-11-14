"""The household-text inventory (ROADMAP A5-A7): every place household words can be written is declared.

``household_text.py`` is the registry; this test holds the code to it. It FAILS when:

  * a function that handles text-like values (``text``, ``utterance``, ``transcript``, ``message``, ``reply``, ``prompt``, ...)
    writes a file (``open(.., "w"/"a"/"x")``, ``.write_text``, ``.write_bytes``, ``json.dump``, ``wave.open(.., "wb")``) and that
    ``relpath::function`` is in neither ``WRITE_SITES`` (mapped to a store) nor ``NOT_HOUSEHOLD`` (reviewed: not household text);
  * a migration / runtime ``CREATE TABLE`` has a text-like column and the table is in no inventory (``forget_reach.TABLES``,
    or ``NO_TEXT_TABLES`` below with a reason);
  * a store of words in ``forget_reach`` is missing here, an entry is incomplete, a ``gap`` is not ledgered in open-problems.md,
    an allowlist pattern matches nothing any more, or the docs page drifts from the registry.

What it cannot see is stated in docs/knowledge/household-text-inventory.md. Pure parsing (``ci_safe``): no database, no network.
Break-the-fix controls are in-file (``test_the_detector_catches_*``).
"""
from __future__ import annotations

import ast
import fnmatch
import re
from pathlib import Path

import pytest

import forget_reach as fr
import household_text as ht

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
REPO = SVC.parents[1]
SCAN_ROOTS = ("services/zoe-data", "scripts")
_SKIP_DIRS = {"tests", "test", "node_modules", "__pycache__", "venv", ".venv", "data", "models", "archive"}

# identifier tokens (split on "_") that mark a value as text a person said or wrote, or Zoe's reply to it
TEXTY = re.compile(r"(?:^|_)(text|utt|utterance|utterances|transcript|transcripts|message|messages|reply|replies|prompt|words|said"
                   r"|turn|turns|fact|facts|hyp|hyps|hypothesis|stt|heard|asr)(?:$|_)", re.I)


def _is_write(n: ast.AST) -> bool:
    if not isinstance(n, ast.Call):
        return False
    f = n.func
    if (isinstance(f, ast.Name) and f.id == "open") or (isinstance(f, ast.Attribute) and f.attr == "open"):
        mode = n.args[1] if len(n.args) > 1 else next((k.value for k in n.keywords if k.arg == "mode"), None)
        return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and any(c in mode.value for c in "wax")
    if isinstance(f, ast.Attribute) and f.attr in ("write_text", "write_bytes"):
        return True
    return isinstance(f, ast.Attribute) and f.attr == "dump" and isinstance(f.value, ast.Name) and f.value.id == "json"


_IO_NAMES = frozenset({"write_text", "read_text"})          # the file API itself is not a text value


def _texty(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and n.attr in _IO_NAMES:
            continue
        name = (n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else n.arg if isinstance(n, ast.arg)
                else n.arg if isinstance(n, ast.keyword) and not isinstance(n.value, ast.Constant) else None)   # not subprocess text=True
        if name and TEXTY.search(name):
            return True
        # a short space-free string: a dict key, a record field (rec["utt_text"]) or a file name ("x.texts.jsonl"); prose
        # (docstrings, messages) has spaces and is not judged
        if (isinstance(n, ast.Constant) and isinstance(n.value, str) and 0 < len(n.value) <= 60 and " " not in n.value
                and any(TEXTY.search(tok) for tok in re.split(r"[^A-Za-z0-9_]+", n.value) if tok)):
            return True
    return False


def write_sites(source: str, rel: str) -> set[str]:
    """``rel::function`` for every file write whose INNERMOST enclosing function (or the module) handles a text-like value."""
    tree = ast.parse(source)
    out: set[str] = set()

    def visit(node: ast.AST, scope: ast.AST, name: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child, child.name)
                continue
            if _is_write(child) and _texty(scope):
                out.add(f"{rel}::{name}")
            visit(child, scope, name)

    visit(tree, tree, "<module>")
    return out


def _py_files() -> list[Path]:
    out = []
    for base in SCAN_ROOTS:
        for p in (REPO / base).rglob("*.py"):
            rel = p.relative_to(REPO)
            if not _SKIP_DIRS.intersection(rel.parts[:-1]) and not p.name.startswith("test_"):
                out.append(p)
    return out


@pytest.fixture(scope="module")
def all_sites() -> set[str]:
    sites: set[str] = set()
    for p in _py_files():
        sites |= write_sites(p.read_text(encoding="utf-8", errors="replace"), p.relative_to(REPO).as_posix())
    return sites


# ── 1. the write-site detector ────────────────────────────────────────────────────────────────

def test_the_scan_sees_the_known_writers(all_sites):
    """Sanity control: a scan that silently found nothing would make the coverage test vacuous."""
    assert len(all_sites) >= 50
    for known in ("services/zoe-data/semantic_router.py::_append_shadow_line", "services/zoe-data/routers/telegram_media.py::_maybe_capture",
                  "services/zoe-data/zoe_agent.py::_log_feedback_triple"):
        assert known in all_sites


def test_every_text_write_site_is_declared(all_sites):
    unknown = sorted(s for s in all_sites if ht.classify(s) is None)
    assert not unknown, (
        f"code writes a file from a function handling text and the site is not in the household-text inventory: {unknown}. "
        "Map it to a store in services/zoe-data/household_text.py WRITE_SITES (add the store if it is new, with its forget reach), "
        "or, if it never holds household words, add it to NOT_HOUSEHOLD with the reason")


def test_no_allowlist_pattern_is_stale(all_sites):
    stale = [p for p in (*ht.WRITE_SITES, *ht.NOT_HOUSEHOLD) if not any(fnmatch.fnmatchcase(s, p) for s in all_sites)]
    assert not stale, f"allowlist entries that match no write site any more (remove them): {stale}"


def test_every_mapped_site_names_a_registered_store():
    assert {k for k in ht.WRITE_SITES.values()} <= set(ht.BY_KEY)


def test_the_detector_catches_a_new_text_sink():
    """Break-the-fix control: a new function that appends what someone said to a file is caught and is undeclared."""
    src = ('import json\n'
           'def keep_diary(utterance, path):\n'
           '    with open(path, "a") as fh:\n'
           '        fh.write(json.dumps({"said": utterance}))\n'
           'def save_cfg(cfg, path):\n'
           '    path.write_text(json.dumps(cfg))\n'
           'def read_only(text, path):\n'
           '    return open(path, "r").read()\n')
    got = write_sites(src, "services/zoe-data/diary.py")
    assert got == {"services/zoe-data/diary.py::keep_diary"}          # the config writer and the reader are not text sinks
    assert ht.classify("services/zoe-data/diary.py::keep_diary") is None


def test_the_detector_catches_removing_a_reviewed_entry(all_sites, monkeypatch):
    """Break-the-fix control on the real tree: drop one WRITE_SITES entry and the coverage check must go red."""
    site = "services/zoe-data/routers/telegram_media.py::_maybe_capture"
    monkeypatch.delitem(ht.WRITE_SITES, site)
    assert ht.classify(site) is None and site in all_sites


def test_nested_functions_are_judged_on_their_own_scope():
    src = ('def outer(transcript):\n'
           '    def inner(cfg, p):\n'
           '        p.write_text(cfg)\n'
           '    return inner\n')
    assert write_sites(src, "x.py") == set()


# ── 2. tables with a text column ──────────────────────────────────────────────────────────────

_TEXT_COL = re.compile(r'(?:^|[(,])\s*"?([a-z_][a-z0-9_]*)"?\s+(?:TEXT|VARCHAR|CHARACTER VARYING|JSONB?)\b', re.I | re.M)
_TEXT_COL_NAMES = re.compile(r"(?:^|_)(text|content|body|title|description|summary|note|notes|message|transcript|utterance|reply"
                             r"|prompt|query|label|payload|words|said|line)(?:$|_)", re.I)

#: Tables with a text-like column that hold no household words (and no member column, so forget_reach does not list them)
NO_TEXT_TABLES: dict[str, str] = {
    "panels": "a panel's name, room and settings",
    "panel_client_certs": "a client certificate fingerprint and an owner note about the device",
    "panel_presence_events": "presence events: a type, a payload of sensor values and a confidence",
    "people_field_definitions": "the LABELS of custom person fields (the values, which hold words, are people_field_values)",
    "struggle_proposals": "the text-free failure pattern by design (struggle_proposals.py module doc)",
    "trust_allowlist": "tool-name patterns the owner allowed",
}


def text_tables(sources) -> set[str]:
    out = set()
    for src in sources:
        text = Path(src).read_text(encoding="utf-8", errors="replace")
        for name, body in fr._bodies(text):
            if any(_TEXT_COL_NAMES.search(c) for c in _TEXT_COL.findall(body)):
                out.add(name)
    return out


def _schema_sources() -> list[Path]:
    skip = {"tests", "node_modules", "__pycache__", "venv", ".venv", "data", "models"}
    return [p for p in SVC.rglob("*.py") if not skip.intersection(p.relative_to(SVC).parts[:-1])
            and ("alembic" in p.parts or "CREATE TABLE" in p.read_text(encoding="utf-8", errors="replace"))]


def test_every_table_with_a_text_column_is_in_an_inventory():
    known = {s.key for s in fr.TABLES} | set(NO_TEXT_TABLES) | set(re.findall(r"pg:([a-z_0-9]+)", " ".join(s.location for s in ht.EXTRA)))
    missing = sorted(text_tables(_schema_sources()) - known)
    assert not missing, (f"tables with a text column in no inventory: {missing}. A table that can hold household words goes in "
                         "forget_reach.TABLES (and here); one that cannot goes in NO_TEXT_TABLES with the reason")


def test_the_table_detector_catches_a_new_text_table(tmp_path):
    mig = tmp_path / "9999_fake.py"
    mig.write_text('X = """CREATE TABLE fake_diary (id TEXT PRIMARY KEY, body TEXT, ts BIGINT)"""\n'
                   'Y = """CREATE TABLE fake_counter (id TEXT PRIMARY KEY, n INTEGER)"""\n')
    assert text_tables([mig]) == {"fake_diary"}


# ── 3. the registry itself ────────────────────────────────────────────────────────────────────

def test_every_store_of_words_in_forget_reach_is_here():
    missing = sorted(ht.must_cover() - {s.key for s in ht.FROM_FORGET_REACH})
    assert not missing, f"forget_reach stores of words with no household-text entry: {missing}"
    assert {s.key for s in ht.FROM_FORGET_REACH} <= set(fr.BY_KEY)
    assert not {s.key for s in ht.EXTRA} & set(fr.BY_KEY), "an EXTRA store is in forget_reach: take it from there"


def test_every_entry_is_complete_and_names_no_real_value():
    bad = []
    for s in ht.STORES:
        for f in ("location", "text", "retention", "owner"):
            if len(getattr(s, f).strip()) < 3:
                bad.append(f"{s.key}: {f} missing")
        if re.search(r"@|/home/|\b\d{1,3}(\.\d{1,3}){3}\b", s.location):
            bad.append(f"{s.key}: the location must be a pattern, not a real path or address")
        st, _fo, _de = ht.reach(s)
        if st not in (fr.REACHED, fr.GAP, fr.OUT_OF_SCOPE):
            bad.append(f"{s.key}: status {st!r}")
        if s in ht.EXTRA and st != fr.REACHED and len(s.why) < 15:
            bad.append(f"{s.key}: a gap or out-of-scope store needs a reason")
    assert not bad, bad
    assert len(ht.BY_KEY) == len(ht.STORES)


def _ledger() -> set[str]:
    text = (REPO / "docs" / "knowledge" / "open-problems.md").read_text(encoding="utf-8")
    line = next((l for l in text.splitlines() if "household-text gaps:" in l), "")
    return set(re.findall(r"`([a-z_0-9]+)`", line.split("household-text gaps:", 1)[1].split(" · ", 1)[0])) if line else set()


def test_extra_gaps_are_ledgered_and_the_ledger_is_not_stale():
    gaps = {s.key for s in ht.EXTRA if s.status == fr.GAP}
    assert gaps == _ledger(), f"open-problems.md 'household-text gaps:' must list exactly {sorted(gaps)}"


def test_the_docs_page_carries_the_generated_inventory():
    doc = (REPO / "docs" / "knowledge" / "household-text-inventory.md").read_text(encoding="utf-8")
    assert ht.render_markdown() in doc, ("docs/knowledge/household-text-inventory.md is stale: "
                                         "python3 services/zoe-data/household_text.py --write docs/knowledge/household-text-inventory.md")
