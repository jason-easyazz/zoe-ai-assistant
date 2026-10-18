"""The forget-reach inventory (architecture deep dive A6): every store that holds per-member data is accounted for.

"Forgotten means forever" held for the palace and Postgres and not for the box. ``forget_reach.py`` is the inventory; this test holds the
code to it. It fails when:

  * a new Postgres table with a ``user_id`` column (or one that references such a table) appears with no inventory entry;
  * a new ``*.jsonl`` / ``*.sqlite`` / ``*.db`` sink or Chroma collection appears that no entry's markers cover;
  * a store claims to be ``reached`` by a function that does not exist or is not called from the forget path;
  * a ``gap`` is not in the open-problems ledger, or the ledger lists a gap that has since been closed;
  * the documented backup retention no longer matches the backup script, or the docs page drifts from the registry.

Break-the-fix controls are in-file: a fake table is injected and must be reported (``test_a_new_user_table_is_caught``).
Pure parsing: no database, no network, no model (``ci_safe``).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import forget_reach as fr

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
REPO = SVC.parents[1]

# where the forget path is wired: the member erase, and the places the entity forget (and its backups/exports) run from
MEMBER_SITE = SVC / "memory_service.py"
ENTITY_SITES = (SVC / "intent_router.py", SVC / "memory_forget_cascade.py", SVC / "memory_service.py",
                REPO / "scripts" / "maintenance" / "export_memory_store.py", REPO / "scripts" / "maintenance" / "redact_backups.py")


def _service_py() -> list[Path]:
    skip = {"tests", "node_modules", "__pycache__", "venv", ".venv", "data", "models"}
    return [p for p in SVC.rglob("*.py") if not skip.intersection(p.relative_to(SVC).parts[:-1])]


def _schema_sources() -> list[Path]:
    """Every migration, plus any other module that creates a table at runtime (db_pool, music_history)."""
    return [p for p in _service_py()
            if "alembic" in p.parts or "CREATE TABLE" in p.read_text(encoding="utf-8", errors="replace")]


@pytest.fixture(scope="module")
def discovered() -> dict[str, str]:
    return fr.discover_user_tables(_schema_sources())


# ── 1. Postgres tables ─────────────────────────────────────────────────────────────────────

def test_the_discovery_sees_the_schema(discovered):
    """Sanity control: a parser that silently found nothing would make every other assertion vacuous."""
    assert len(discovered) >= 60
    for known in ("exact_turns", "omi_recordings", "chat_sessions", "people", "events"):
        assert known in discovered
    assert discovered["chat_messages"] == "references chat_sessions"          # a child table without a user_id column


def test_every_per_member_table_has_an_inventory_entry(discovered):
    missing = fr.uncovered_tables(discovered)
    assert not missing, (
        f"new per-member table(s) {missing}: add a Store to services/zoe-data/forget_reach.py (reached via delete_user / the entity "
        "cascade, out_of_scope with a reason, or a ledgered gap) - then regenerate docs/knowledge/forget-reach.md")


def test_no_inventory_entry_names_a_table_that_no_migration_creates(discovered):
    assert not fr.stale_tables(discovered)


def test_a_new_user_table_is_caught(tmp_path, discovered):
    """Break-the-fix control: a migration that adds ``fake_journal`` with a user_id must make the coverage check fail, and so must a
    child table that only references a per-member table."""
    mig = tmp_path / "9999_fake.py"
    mig.write_text('SQL = """\nCREATE TABLE IF NOT EXISTS fake_journal (\n    id TEXT PRIMARY KEY,\n    user_id TEXT NOT NULL,\n    body TEXT\n)\n"""\n'
                   'SQL2 = """CREATE TABLE fake_child (id TEXT, session_id TEXT, FOREIGN KEY (session_id) REFERENCES chat_sessions(id))"""\n'
                   'SQL3 = "ALTER TABLE events ADD COLUMN IF NOT EXISTS user_id TEXT"\n')
    found = fr.discover_user_tables([*_schema_sources(), mig])
    assert "fake_journal" in found and found["fake_child"] == "references chat_sessions"
    assert {"fake_child", "fake_journal"} <= set(fr.uncovered_tables(found))
    assert "fake_journal" not in discovered and not set(fr.uncovered_tables(discovered)) & {"fake_child", "fake_journal"}


def test_a_table_without_a_member_column_is_not_swept_in(tmp_path):
    mig = tmp_path / "m.py"
    mig.write_text('X = "CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)"\n')
    assert fr.discover_user_tables([mig]) == {}


# ── 2. every other store ───────────────────────────────────────────────────────────────────

_FILE_LITERAL = re.compile(r"""["']([^"'\s]+\.(?:jsonl|sqlite3?|db))["']""")


def test_every_file_sink_in_the_service_is_covered_by_a_marker():
    markers = [m for s in fr.OTHER for m in s.markers]
    uncovered = {}
    for py in _service_py():
        for lit in _FILE_LITERAL.findall(py.read_text(encoding="utf-8", errors="replace")):
            if not any(m in lit for m in markers):
                uncovered.setdefault(lit, py.name)
    assert not uncovered, (
        f"new file sink(s) {uncovered}: decide what forget does about them and add a Store (markers=...) to forget_reach.OTHER")


def test_every_chroma_collection_is_an_inventory_marker():
    chroma = next(s for s in fr.OTHER if s.kind == "chroma")
    names = set()
    for py in _service_py():
        names.update(re.findall(r'^_[A-Z_]*COLLECTION\s*=\s*(?:os\.environ\.get\([^,]+,\s*)?"([^"]+)"',
                                py.read_text(encoding="utf-8", errors="replace"), re.M))
    assert {"mempalace_drawers", "mempalace_audit"} <= names                 # the scan sees the palace's two collections
    assert names <= set(chroma.markers), f"collection(s) {names - set(chroma.markers)} are not in the chroma inventory entry"


def test_every_marker_is_still_in_the_code():
    """A marker nobody uses any more is a silent hole in the scan above (the sink it covered is gone, or renamed)."""
    corpus = ""
    for base in (SVC, REPO / "scripts", REPO / "tools"):
        for p in base.rglob("*"):
            if p.suffix in (".py", ".sh") and "tests" not in p.parts and "node_modules" not in p.parts:
                corpus += p.read_text(encoding="utf-8", errors="replace")
    stale = [(s.key, m) for s in fr.OTHER if s.kind != "backup" for m in s.markers if m not in corpus]
    assert not stale, f"stale inventory markers: {stale}"


# ── 3. what 'reached' means ───────────────────────────────────────────────────────────────

def _module_file(mod: str) -> Path:
    for base in (SVC, REPO / "scripts" / "maintenance"):
        p = base / f"{mod}.py"
        if p.exists():
            return p
    raise AssertionError(f"no module {mod}.py for an inventory reference")


def _references(site: Path, mod: str, func: str, within: str | None = None) -> bool:
    """Does ``site`` use ``mod.func`` - a call OR a reference handed to another callable (``asyncio.to_thread(mod.func)``)? By AST, so a
    comment, a string or an ``import`` line that merely names it does not count. ``within`` limits the search to one function."""
    import ast

    tree = ast.parse(site.read_text(encoding="utf-8"))
    scope: list = [tree]
    if within:
        scope = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == within]
        assert scope, f"{site.name} has no function {within}"
    return any(isinstance(n, ast.Attribute) and n.attr == func and isinstance(n.value, ast.Name) and n.value.id == mod
               for root in scope for n in ast.walk(root))


def _called_from(ref: str, sites: tuple[Path, ...], within: str | None = None) -> bool:
    mod, func = ref.split(".", 1)[0], ref.split(".")[-1]
    return any(site.stem == mod or _references(site, mod, func, within) for site in sites)


def test_reached_stores_name_a_real_eraser_that_the_forget_path_calls():
    problems = []
    for s in fr.STORES:
        if s.status != fr.REACHED:
            continue
        if not (s.member or s.entity):
            problems.append(f"{s.key}: reached but names no eraser")
        for ref, sites, within, label in ((s.member, (MEMBER_SITE,), "delete_user", "member"), (s.entity, ENTITY_SITES, None, "entity")):
            if not ref:
                continue
            if not fr.function_defined(_module_file(ref.split(".")[0]), ref):
                problems.append(f"{s.key}: {label} eraser {ref} is not defined")
            elif not _called_from(ref, sites, within):
                problems.append(f"{s.key}: {label} eraser {ref} is not called from the forget path")
    assert not problems, problems


def test_gap_stores_that_name_an_entity_eraser_really_have_one():
    """A gap may be 'reached by the entity forget only'; the eraser it names must still exist and be called."""
    problems = [f"{s.key}: {s.entity}" for s in fr.STORES if s.status == fr.GAP and s.entity
                and not (fr.function_defined(_module_file(s.entity.split(".")[0]), s.entity) and _called_from(s.entity, ENTITY_SITES))]
    assert not problems, problems


def test_every_entry_is_complete():
    bad = []
    for s in fr.STORES:
        if s.status not in (fr.REACHED, fr.OUT_OF_SCOPE, fr.GAP):
            bad.append(f"{s.key}: unknown status {s.status}")
        if s.status in (fr.OUT_OF_SCOPE, fr.GAP) and len(s.why.strip()) < 15:
            bad.append(f"{s.key}: {s.status} needs a reason")
        if s.kind != "pg_table" and not s.markers:
            bad.append(f"{s.key}: a non-table store needs markers")
        if s.holds == fr.WORDS and s.status == fr.OUT_OF_SCOPE and s.kind == "pg_table":
            bad.append(f"{s.key}: a table that holds WORDS cannot be out of scope - it is reached or a ledgered gap")
    assert not bad, bad
    assert len({s.key for s in fr.STORES}) == len(fr.STORES)


# ── 4. gaps are ledgered, and the ledger is not stale ─────────────────────────────────────

def _ledger_gaps() -> set[str]:
    text = (REPO / "docs" / "knowledge" / "open-problems.md").read_text(encoding="utf-8")
    line = next((l for l in text.splitlines() if "forget-reach gaps:" in l), "")
    # the keys are the backticked names between "forget-reach gaps:" and the next " · " field separator
    return set(re.findall(r"`([a-z_0-9]+)`", line.split("forget-reach gaps:", 1)[1].split(" · ", 1)[0])) if line else set()


def test_every_gap_is_in_the_open_problems_ledger():
    ledger = _ledger_gaps()
    assert ledger, 'open-problems.md has no "forget-reach gaps:" line'
    assert set(fr.GAPS) <= ledger, f"gap(s) not in the ledger: {sorted(set(fr.GAPS) - ledger)}"


def test_the_ledger_names_no_gap_that_has_been_closed():
    assert _ledger_gaps() <= set(fr.GAPS), (
        f"the ledger still lists {sorted(_ledger_gaps() - set(fr.GAPS))}: a closed gap leaves the ledger the day it is fixed")


# ── 5. the backup policy ─────────────────────────────────────────────────────────────────

def test_the_documented_backup_retention_matches_the_backup_script():
    script = (REPO / "scripts" / "maintenance" / "postgres-nightly-backup.sh").read_text(encoding="utf-8")
    daily = int(re.search(r'KEEP_DAILY="\$\{ZOE_POSTGRES_BACKUP_KEEP:-(\d+)\}"', script).group(1))
    weekly = int(re.search(r'KEEP_WEEKLY="\$\{ZOE_POSTGRES_WEEKLY_KEEP:-(\d+)\}"', script).group(1))
    assert daily + 7 * weekly <= fr.BACKUP_MAX_AGE_DAYS, (
        f"the dumps now live {daily + 7 * weekly} days but the policy says {fr.BACKUP_MAX_AGE_DAYS}: change one of them on purpose")


def test_biometric_table_data_stays_out_of_the_dumps():
    script = (REPO / "scripts" / "maintenance" / "postgres-nightly-backup.sh").read_text(encoding="utf-8")
    excluded = set(re.search(r'BIOMETRIC_TABLES="([^"]+)"', script).group(1).split())
    for s in fr.TABLES:
        if s.holds == fr.BIOMETRIC and s.key not in excluded:
            assert "dumps" in s.why and "35 days" in s.why, f"{s.key}: biometric data in the dumps must say so and say how long"
    assert excluded <= {s.key for s in fr.TABLES if s.holds == fr.BIOMETRIC}


# ── 6. the human page is the registry ─────────────────────────────────────────────────────

def test_the_docs_page_carries_the_generated_inventory_and_the_policy():
    doc = (REPO / "docs" / "knowledge" / "forget-reach.md").read_text(encoding="utf-8")
    assert fr.render_markdown() in doc, "docs/knowledge/forget-reach.md is stale: python3 services/zoe-data/forget_reach.py --write docs/knowledge/forget-reach.md"
    assert f"{fr.BACKUP_MAX_AGE_DAYS} days" in doc and f"{fr.ADHOC_BACKUP_MAX_AGE_DAYS} days" in doc
