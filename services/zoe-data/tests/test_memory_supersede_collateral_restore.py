"""``memory_supersede_collateral_audit.py --apply-restore``: the audited restore of rows the conflict pass retired
for a DIFFERENT person's / attribute's fact (bake-off X1 / X2, 2026-10-06; the owner's rule: memory must be flawless).

Synthetic names and an in-memory store only (never the household palace). Each guarantee has a negative control
(the guard removed / the shape broken turns the test red; see the tail of the file).
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts" / "maintenance"))
import memory_supersede_collateral_audit as tool  # noqa: E402
import memory_temporal as temporal  # noqa: E402
import live_store_guard  # noqa: E402
import restore_memories_from_export as rme  # noqa: E402
from memory_service import MemoryService  # noqa: E402

pytestmark = pytest.mark.ci_safe

OWNER = "demo-member"
OTHER = "demo-other"
T0, T_MOVE = 1_700_000_000.0, 1_710_000_000.0      # when the old row began / when the successor began
SECRET = "Zorblax"                                   # must never appear in any output or audit row
DOC = tool.DOC_KEY


def _meta(user=OWNER, status="approved", **kw):
    md = {"user_id": user, "wing": user, "status": status, "added_ts": T0, "valid_from": T0, "valid_from_basis": "captured",
          "memory_type": "fact", "source": "telegram"}
    md.update(kw)
    return md


def _retired(by, **kw):
    return _meta(status="superseded", superseded_by_id=by, invalid_at=T_MOVE, expired_at=T_MOVE + 5, **kw)


def seed():
    """doc, meta per id. Two collateral shapes, two genuine changes, one enrichment, one foreign-user row."""
    return {
        # X1: Leo's home retired by Dana's home (a different person)
        "old-leo": (f"User's friend {SECRET} lives in Perth.", _retired("new-dana")),
        "new-dana": ("User's friend Dana lives in Hobart.", _meta(added_ts=T_MOVE, valid_from=T_MOVE, supersedes_id="old-leo")),
        # X2: a friend's job retired by the same friend's move (a different attribute)
        "old-job": ("User's friend Ines works at a bookbinder.", _retired("new-move", valid_until=T_MOVE + 99)),
        "new-move": ("User's friend Ines moved to Perth.", _meta(added_ts=T_MOVE, valid_from=T_MOVE, supersedes_id="old-job")),
        # a REAL change of the same thing: stays retired
        "old-home": ("User's friend Tove lives in Cork.", _retired("new-home")),
        "new-home": ("User's friend Tove lives in Ghent.", _meta(added_ts=T_MOVE, valid_from=T_MOVE, supersedes_id="old-home")),
        # the owner's own move: stays retired
        "old-own": ("User lives in Hobart.", _retired("new-own")),
        "new-own": ("User moved to Cork.", _meta(added_ts=T_MOVE, valid_from=T_MOVE, supersedes_id="old-own")),
        # someone else's collateral: never touched by an --owner run for the owner
        "old-x": ("User's friend Oskar lives in Lisbon.", _meta(user=OTHER, status="superseded", superseded_by_id="new-x",
                                                                 invalid_at=T_MOVE, expired_at=T_MOVE)),
        "new-x": ("User's friend Pia lives in Rome.", _meta(user=OTHER, added_ts=T_MOVE, valid_from=T_MOVE)),
        # an unrelated row
        "other-row": ("User likes rowing.", _meta()),
    }


class FakeCol:
    """chroma stand-in: documents, metadata and one embedding per row; ``get`` / ``update`` / ``upsert``."""

    def __init__(self):
        self.store: dict[str, tuple[str, dict, list]] = {}
        self.upserts: list[str] = []
        self.updates: list[list[str]] = []

    def get(self, ids=None, where=None, include=None):
        got = [i for i in (ids or list(self.store)) if i in self.store]
        out = {"ids": got, "documents": [self.store[i][0] for i in got], "metadatas": [dict(self.store[i][1]) for i in got]}
        if include and "embeddings" in include:
            out["embeddings"] = [list(self.store[i][2]) for i in got]
        return out

    def update(self, ids, metadatas, documents=None, embeddings=None):
        self.updates.append(list(ids))
        for i, m in zip(ids, metadatas):
            self.store[i] = (self.store[i][0], dict(m), self.store[i][2])

    def upsert(self, ids, documents, metadatas, embeddings=None):
        for i, d, m in zip(ids, documents, metadatas):
            self.upserts.append(i)
            self.store[i] = (d, dict(m), list(embeddings[0]) if embeddings else [0.5, 0.5])


def make_svc(tmp_path, rows=None):
    svc = MemoryService(data_dir=str(tmp_path / "scratch"))
    svc._rows, svc._audit = FakeCol(), FakeCol()
    for rid, (doc, md) in (rows or seed()).items():
        svc._rows.store[rid] = (doc, dict(md), [0.1, 0.2, 0.3])
    svc._collection = lambda: svc._rows
    svc._audit_collection = lambda: svc._audit
    return svc


def drawers_of(svc):
    return [{"eid": i, DOC: d, **m} for i, (d, m, _e) in svc._rows.store.items()]


def audit_of(svc):
    return [{"eid": i, DOC: d, **m} for i, (d, m, _e) in svc._audit.store.items()]


def plan_for(svc, owner=OWNER):
    return tool.audit(drawers_of(svc), audit_of(svc), tool._matcher(tool.REPO), owner=owner)


def run(coro):
    return asyncio.run(coro)


def restore_all(svc, owner=OWNER):
    return run(tool.apply_restore(plan_for(svc, owner)["rows"], owner, svc=svc))


def meta(svc, rid):
    return svc._rows.store[rid][1]


# ── what is restorable ───────────────────────────────────────────────────────────────

def test_the_plan_is_exactly_the_two_collateral_shapes_for_the_owner(tmp_path):
    svc = make_svc(tmp_path)
    rep = plan_for(svc)
    assert sorted(r["id"] for r in rep["rows"]) == ["old-job", "old-leo"]
    assert {r["id"]: r["successor_id"] for r in rep["rows"]} == {"old-leo": "new-dana", "old-job": "new-move"}
    assert plan_for(svc, OTHER)["restorable_rows"] == 1            # per-user: the other user's row is theirs


def test_a_genuine_supersession_is_not_restored(tmp_path):
    svc = make_svc(tmp_path)
    assert restore_all(svc)["restored"] == 2
    for rid in ("old-home", "old-own"):                             # same subject, same attribute: a real change
        m = meta(svc, rid)
        assert m["status"] == "superseded" and m["superseded_by_id"] and m["invalid_at"] == T_MOVE
    assert meta(svc, "new-home")["supersedes_id"] == "old-home" and meta(svc, "new-own")["supersedes_id"] == "old-own"
    assert "old-home" not in {a["mempalace_id"] for a in audit_of(svc)}


def test_a_row_whose_text_is_approved_again_is_not_restored(tmp_path):
    rows = seed()
    rows["said-again"] = (f"User's friend {SECRET} lives in Perth.", _meta(added_ts=T_MOVE + 9, valid_from=T_MOVE + 9))
    svc = make_svc(tmp_path, rows)
    rep = plan_for(svc)
    assert [r["id"] for r in rep["rows"]] == ["old-job"] and rep["totals"]["collateral_text_already_approved"] == 1
    restore_all(svc)
    assert meta(svc, "old-leo")["status"] == "superseded"


# ── what the restore does to the row: two timelines ─────────────────────────────────

@pytest.mark.parametrize("rid,succ", [("old-leo", "new-dana"), ("old-job", "new-move")])
def test_restore_reopens_the_row_from_its_original_start(tmp_path, rid, succ):
    svc = make_svc(tmp_path)
    text_before = svc._rows.store[rid][0]
    assert restore_all(svc)["restored"] == 2
    doc, m, emb = svc._rows.store[rid]
    assert m["status"] == "approved" and m["reviewed_by"] == "operator_restore"
    for gone in ("invalid_at", "expired_at", "superseded_by_id", "invalid_at_precision"):
        assert gone not in m
    assert m["valid_from"] == T0 and m["added_ts"] == T0, "the original start and the learned-at are kept"
    assert m["restored_at"] > T_MOVE and "restored" in m["review_note"] and succ in m["review_note"]
    assert doc == text_before and emb == [0.1, 0.2, 0.3], "text and embedding untouched (a metadata-only update)"
    assert svc._rows.upserts == [], "no re-index when the index still has the row"
    assert temporal.valid_at(m, T0) and temporal.valid_at(m, T_MOVE - 1), "true again from where it began"
    assert temporal.row_end(m) == (T_MOVE + 99 if rid == "old-job" else None), \
        "open-ended, except an end the person stated themselves"


def test_a_stated_valid_until_is_the_persons_own_and_stays(tmp_path):
    svc = make_svc(tmp_path)
    restore_all(svc)
    assert meta(svc, "old-job")["valid_until"] == T_MOVE + 99 and "valid_until" not in meta(svc, "old-leo")


def test_a_row_without_valid_from_gets_its_start_from_added_ts_marked_backfill():
    sets, drops = temporal.restore_fields({"added_ts": T0, "invalid_at": 5.0}, now=T_MOVE)
    assert sets["valid_from"] == T0 and sets["valid_from_basis"] == "backfill" and sets["restored_at"] == T_MOVE
    assert set(drops) == {"invalid_at", "invalid_at_precision", "expired_at", "superseded_by_id"}
    sets, _ = temporal.restore_fields({"valid_from": 7.0, "added_ts": T0}, now=T_MOVE)
    assert "valid_from" not in sets, "an existing valid_from is never rewritten"


def test_an_index_that_lost_the_row_is_rebuilt_from_its_text(tmp_path):
    svc = make_svc(tmp_path)
    svc._rows.store["old-leo"] = (svc._rows.store["old-leo"][0], svc._rows.store["old-leo"][1], [])
    assert restore_all(svc) == {"restored": 2, "skipped": 0, "reindexed": 1, "successor_links_cleared": 2}
    assert svc._rows.upserts == ["old-leo"] and svc._rows.store["old-leo"][2] != []
    assert meta(svc, "old-leo")["status"] == "approved" and meta(svc, "new-dana")["status"] == "approved"


# ── the successor ───────────────────────────────────────────────────────────────────

def test_the_successor_stays_a_true_fact_but_its_link_to_the_restored_row_is_cleared(tmp_path):
    rows = seed()
    rows["new-move"][1]["supersedes_id"] = "some-older-row"          # first link wins: a link elsewhere is not ours
    svc = make_svc(tmp_path, rows)
    before = {i: svc._rows.store[i][0] for i in ("new-dana", "new-move")}
    assert restore_all(svc)["successor_links_cleared"] == 1
    dana, move = meta(svc, "new-dana"), meta(svc, "new-move")
    assert "supersedes_id" not in dana and dana["supersedes_cleared_id"] == "old-leo" and "supersedes_cleared_note" in dana
    assert move["supersedes_id"] == "some-older-row" and "supersedes_cleared_id" not in move
    for m in (dana, move):
        assert m["status"] == "approved" and m["valid_from"] == T_MOVE
    assert {i: svc._rows.store[i][0] for i in before} == before


# ── idempotent ──────────────────────────────────────────────────────────────────────

def test_a_second_run_restores_zero_and_writes_nothing_more(tmp_path):
    svc = make_svc(tmp_path)
    assert restore_all(svc)["restored"] == 2
    snap = ({i: (d, dict(m)) for i, (d, m, _e) in svc._rows.store.items()}, len(svc._audit.store))
    again = restore_all(svc)
    assert again == {"restored": 0, "skipped": 0, "reindexed": 0, "successor_links_cleared": 0}
    assert plan_for(svc)["restorable_rows"] == 0
    assert ({i: (d, dict(m)) for i, (d, m, _e) in svc._rows.store.items()}, len(svc._audit.store)) == snap


def test_a_stale_plan_never_overwrites_a_row_changed_since(tmp_path):
    svc = make_svc(tmp_path)
    plan = plan_for(svc)["rows"]
    meta_leo = svc._rows.store["old-leo"][1]
    meta_leo["superseded_by_id"] = "new-home"                                       # retired again, by something else
    out = run(tool.apply_restore(plan, OWNER, svc=svc))
    assert out["restored"] == 1 and out["skipped"] == 1 and meta(svc, "old-leo")["status"] == "superseded"
    meta(svc, "old-job")["status"] = "archived"                                      # a person archived it meanwhile
    assert run(tool.apply_restore(plan, OWNER, svc=svc))["restored"] == 0


def test_only_the_owners_rows_are_restored_even_with_a_foreign_plan_row(tmp_path):
    svc = make_svc(tmp_path)
    foreign = plan_for(svc, OTHER)["rows"]
    assert run(tool.apply_restore(foreign, OWNER, svc=svc)) == {
        "restored": 0, "skipped": 1, "reindexed": 0, "successor_links_cleared": 0}
    assert meta(svc, "old-x")["status"] == "superseded"
    # and the service itself refuses a row of another user
    assert run(svc.restore_superseded(OWNER, "old-x", expected_successor_id="new-x", actor="t")) is None


# ── the audit trail ─────────────────────────────────────────────────────────────────

def test_the_audit_lane_records_before_and_after_with_no_text(tmp_path):
    svc = make_svc(tmp_path)
    restore_all(svc)
    rows = audit_of(svc)
    rest = {a["mempalace_id"]: a for a in rows if a["action"] == "restore_collateral"}
    assert set(rest) == {"old-leo", "old-job"}
    for rid, succ in (("old-leo", "new-dana"), ("old-job", "new-move")):
        a = rest[rid]
        before, after = json.loads(a["before"]), json.loads(a["after"])
        assert a["actor"] == "operator_restore" and a["user_id"] == OWNER and a["timestamp"]
        assert before["status"] == "superseded" and before["superseded_by_id"] == succ and before["invalid_at"] == T_MOVE
        assert after["status"] == "approved" and after["valid_from"] == T0 and "invalid_at" not in after
        assert "other_" in a["reason"] or "attribute" in a["reason"]
    unlink = [a for a in rows if a["action"] == "restore_collateral_unlink"]
    assert {a["mempalace_id"] for a in unlink} == {"new-dana", "new-move"}
    assert json.loads(unlink[0]["before"])["supersedes_id"] in ("old-leo", "old-job")
    assert SECRET not in json.dumps(rows) and "lives in" not in json.dumps(rows), "the ledger never carries memory text"
    assert len(rows) == 4


# ── guards: flags, the stopped service, the live store ──────────────────────────────

def _palace(path, svc_rows):
    """A chroma-shaped sqlite of ``svc_rows`` (the snapshot the dry run / apply reads)."""
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE collections (id TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE segments (id TEXT PRIMARY KEY, scope TEXT, collection TEXT);
        CREATE TABLE embeddings (id INTEGER PRIMARY KEY, segment_id TEXT, embedding_id TEXT);
        CREATE TABLE embedding_metadata (id INTEGER, key TEXT, string_value TEXT, int_value INTEGER,
                                         float_value REAL, bool_value INTEGER);
        INSERT INTO collections VALUES ('c1','mempalace_drawers');
        INSERT INTO segments VALUES ('s1','METADATA','c1');
        INSERT INTO collections VALUES ('c2','mempalace_audit');
        INSERT INTO segments VALUES ('s2','METADATA','c2');
    """)
    for n, (rid, (doc, md)) in enumerate(svc_rows.items(), 1):
        c.execute("INSERT INTO embeddings VALUES (?,?,?)", (n, "s1", rid))
        for k, v in {**md, DOC: doc}.items():
            c.execute("INSERT INTO embedding_metadata VALUES (?,?,?,?,?,?)", (n, k, str(v), None, None, None))
    c.commit()
    c.close()


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """main() against a sqlite snapshot, with the service check and the service factory stubbed."""
    db = tmp_path / "palace" / "chroma.sqlite3"
    db.parent.mkdir()
    _palace(str(db), seed())
    svc = make_svc(tmp_path)
    calls = {"service": 0}

    def fake_service(_dir):
        calls["service"] += 1
        return svc

    def fake_read(_ledger, _db):
        return drawers_of(svc), audit_of(svc)             # the live view of the (fake) store after the writes

    monkeypatch.setattr(tool, "service_up", lambda: None)
    monkeypatch.setattr(tool, "_make_service", fake_service)
    monkeypatch.setattr(tool, "_read_palace", fake_read)
    return type("Cli", (), {"db": str(db), "svc": svc, "calls": calls})


def main(cli, *argv):
    return tool.main(["--db", cli.db, *argv])


FLAGS = ["--apply-restore", "--owner", OWNER, "--i-have-reviewed", "--i-stopped-zoe-data"]


def test_dry_run_is_the_default_and_writes_nothing(cli, capsys):
    assert main(cli, "--owner", OWNER) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "Restorable" in out and SECRET not in out
    assert cli.calls["service"] == 0 and len(cli.svc._audit.store) == 0
    assert meta(cli.svc, "old-leo")["status"] == "superseded"


@pytest.mark.parametrize("flags", [
    ["--apply-restore", "--owner", OWNER],
    ["--apply-restore", "--owner", OWNER, "--i-have-reviewed"],
    ["--apply-restore", "--owner", OWNER, "--i-stopped-zoe-data"],
    ["--apply-restore", "--i-have-reviewed", "--i-stopped-zoe-data"],          # no --owner: one user per run
])
def test_apply_refuses_without_the_flags_and_the_owner(cli, flags, capsys):
    assert main(cli, *flags) == 2
    assert cli.calls["service"] == 0 and meta(cli.svc, "old-leo")["status"] == "superseded"
    assert "refusing" in capsys.readouterr().err


def test_apply_refuses_a_drawers_export(cli, tmp_path, capsys):
    exp = tmp_path / "exp.json"
    exp.write_text(json.dumps({"ids": [], "documents": [], "metadatas": []}))
    assert main(cli, *FLAGS, "--export", str(exp)) == 2 and cli.calls["service"] == 0


def test_apply_refuses_while_zoe_data_may_be_running(cli, monkeypatch, capsys):
    monkeypatch.setattr(tool, "service_up", lambda: "the zoe-data unit state is 'active'")
    assert main(cli, *FLAGS) == 3
    assert "refusing --apply-restore" in capsys.readouterr().err
    assert cli.calls["service"] == 0 and len(cli.svc._audit.store) == 0
    assert meta(cli.svc, "old-leo")["status"] == "superseded"


@pytest.mark.parametrize("state,port_open,refused", [
    ("active", False, True), ("activating", False, True), ("deactivating", False, True), ("reloading", False, True),
    ("", False, True),                                    # systemctl could not answer (no user bus under sudo/cron)
    ("inactive", True, True),                             # unit says down but the port is bound
    ("failed", True, True),
    ("inactive", False, False), ("failed", False, False),
])
def test_the_service_check_fails_closed_on_every_state_but_a_provably_stopped_one(monkeypatch, state, port_open, refused):
    class R:
        stdout = state + "\n"

    monkeypatch.setattr(rme.subprocess, "run", lambda *a, **k: R())

    def conn(*a, **k):
        if port_open:
            import contextlib
            return contextlib.nullcontext()
        raise OSError("refused")

    monkeypatch.setattr(rme.socket, "create_connection", conn)
    assert bool(tool.service_up()) is refused


def test_the_service_check_refuses_when_systemctl_is_missing(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(rme.subprocess, "run", boom)
    monkeypatch.setattr(rme.socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert "cannot be read" in tool.service_up()


def test_end_to_end_prints_counts_only_and_a_second_run_restores_zero(cli, capsys):
    assert main(cli, *FLAGS) == 0
    out = capsys.readouterr().out
    assert "APPLIED: restored=2 skipped=0 reindexed=0 successor_links_cleared=2 ledger_rows=2 approved_now=2 " \
           "restorable_remaining=0" in out
    assert SECRET not in out and "lives in" not in out
    assert main(cli, *FLAGS) == 0
    assert "APPLIED: restored=0" in capsys.readouterr().out
    assert len(cli.svc._audit.store) == 4                                  # nothing more was written
    assert main(cli, "--owner", OWNER, "--json") == 0
    assert json.loads(capsys.readouterr().out)["restorable_rows"] == 0     # the operator's verify step


def test_the_run_fails_loudly_when_the_store_does_not_show_the_restore(cli, monkeypatch, capsys):
    reads = []

    def read(_l, _d):                      # the plan sees the store; the post-apply read lost a restored row
        reads.append(1)
        rows = drawers_of(cli.svc)
        return (rows if len(reads) == 1 else [d for d in rows if d["eid"] != "old-leo"]), audit_of(cli.svc)

    monkeypatch.setattr(tool, "_read_palace", read)
    assert main(cli, *FLAGS) == 5
    assert "WARNING" in capsys.readouterr().err


def test_the_live_store_guard_stays_armed(tmp_path, monkeypatch):
    svc = make_svc(tmp_path)
    monkeypatch.setenv("ZOE_LIVE_PALACE_DIR", str(tmp_path / "scratch"))     # as if this were the household palace
    trips = live_store_guard.trip_count()
    with pytest.raises(live_store_guard.LiveStoreViolation):
        run(svc.restore_superseded(OWNER, "old-leo", expected_successor_id="new-dana", actor="operator_restore"))
    assert live_store_guard.trip_count() == trips + 1
    assert meta(svc, "old-leo")["status"] == "superseded" and len(svc._audit.store) == 0


def test_the_service_never_restores_a_row_that_is_not_superseded_by_the_named_successor(tmp_path):
    svc = make_svc(tmp_path)
    assert run(svc.restore_superseded(OWNER, "old-leo", expected_successor_id="new-move", actor="t")) is None
    assert run(svc.restore_superseded(OWNER, "other-row", expected_successor_id="new-move", actor="t")) is None
    assert run(svc.restore_superseded(OWNER, "old-leo", expected_successor_id="old-leo", actor="t")) is None
    assert run(svc.restore_superseded(OWNER, "gone", expected_successor_id="new-move", actor="t")) is None
    assert meta(svc, "old-leo")["status"] == "superseded" and len(svc._audit.store) == 0
