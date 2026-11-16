"""A record the SHADOW kernel wrote, opened by the AUTHORITY: forget and undo must hold for legacy receipts too.

The legacy record is GENERATED, not hand-written: the real shadow daemon (``shadow_daemon.py``, its own process) is driven over its
socket with add / remove / undo, and then the one thing this branch's spine adds to a database it writes is taken away (the
``reversals`` index and its meta key), so the file is what the shadow kernel left before this change. Why that is faithful: the
receipts themselves (the lab ``Lists`` driver's whole-list snapshots, unmarked ``list.remove``, the dispatcher's ``undo`` receipts)
are written by the same driver and dispatcher code the base branch runs; the index is the only schema difference (checked by hand
against a database generated with ``git archive origin/feature/os-shadow-7-kernel-verbs``: same tables bar ``reversals``, same receipt
payload keys). The authority then opens it through the real daemon, with the database move (``--db``), and every case is checked
with the raw-bytes scan of every non-credential file, and again across a second restart. Synthetic members and items only.
"""
from __future__ import annotations

import json
import sqlite3

import pytest
from test_authority import A, ASKED_FOR, Daemon, _members_bytes, digest, state  # noqa: F401  (state is a fixture)
from test_shadow_daemon import Shadow

MILK, EGGS = "synthetic marker <milk>", "synthetic marker <eggs>"


def legacy(st, history) -> dict:
    """Run the shadow daemon on the state directory, play ``history`` (a list of ("add"|"remove"|"undo", item)), stop it, and strip
    this branch's index so the database is the pre-change one. Returns {name: seq} of the requests and receipts it made."""
    sh, seen = Shadow(st[0]), {}
    try:
        for n, (move, item) in enumerate(history):
            if move == "add":
                out = sh.add(A, item)
                ASKED_FOR.setdefault(str(st[0]), set()).add((A, item))
                seen[f"add{n}"], seen[f"said{n}"] = out["seq"], out["asked"]
            elif move == "remove":
                said = sh.voice.publish("utterance", subject=A, payload={"text": f"take {item} off"})
                out = sh.brain.call("act", action="list.remove", on_behalf_of=A,
                                    args={"item": item, "list": "shopping", "asked": said.seq}, caused_by=[said.seq])
                assert out["verified"]
                seen[f"remove{n}"], seen[f"said{n}"] = out["seq"], said.seq
            else:
                seen[f"undo{n}"] = sh.panel.call("undo", person=A, actor=A)["seq"]
    finally:
        sh.stop()
    db = sqlite3.connect(str(st[0] / "kernel" / "zoe-os.db"))
    db.execute("DROP TABLE IF EXISTS reversals")
    db.execute("DELETE FROM meta WHERE key LIKE 'reversals_%'")
    db.commit()
    db.close()
    return seen


def authority(st) -> Daemon:
    d = Daemon(st[0], db=str(st[0] / "perm" / "zoe-os.db"))           # the move: the old lab path to the permanent one
    st[1].append(d)
    return d


def everywhere(st, word: str) -> list:
    return _members_bytes(st[0], word.encode())


def test_forgetting_a_migrated_removal_takes_the_add_it_removed_and_keeps_the_rest(state):
    seen = legacy(state, [("add", MILK), ("add", EGGS), ("remove", MILK)])
    d = authority(state)
    assert d.digests() == [digest(EGGS)]
    assert d.panel.call("forget", subject=A, actor=A, seqs=[seen["said2"]])["count"] >= 4      # "take milk off", by seq
    assert everywhere(state, MILK) == [] and d.digests() == [digest(EGGS)]
    d.kill()
    again = authority(state)
    assert again.digests() == [digest(EGGS)] and everywhere(state, MILK) == []                 # not restored by the rebuild


def test_forgetting_a_migrated_add_erases_the_removal_that_took_it(state):
    seen = legacy(state, [("add", MILK), ("add", EGGS), ("remove", MILK)])
    d = authority(state)
    assert d.panel.call("forget", subject=A, actor=A, seqs=[seen["said0"]])["count"] >= 4      # milk's add, by its request
    assert everywhere(state, MILK) == [] and d.digests() == [digest(EGGS)]                     # the removal's args and proof too
    assert everywhere(state, EGGS)
    d.kill()
    again = authority(state)
    assert again.digests() == [digest(EGGS)] and everywhere(state, MILK) == []


@pytest.mark.parametrize("forget", ["the_undo", "the_add", "the_removal"])
def test_a_migrated_undo_of_a_removal_and_every_forget_order(state, forget):
    seen = legacy(state, [("add", MILK), ("add", EGGS), ("remove", MILK), ("undo", None)])
    d = authority(state)
    assert d.digests() == sorted([digest(EGGS), digest(MILK)])                                  # the undo put milk back
    if forget == "the_undo":
        d.panel.call("forget", subject=A, actor=A, seqs=[seen["undo3"]])
    elif forget == "the_add":
        d.panel.call("forget", subject=A, actor=A, seqs=[seen["said0"]])
    else:
        d.panel.call("forget", subject=A, actor=A, seqs=[seen["said2"]])
    assert everywhere(state, MILK) == [] and d.digests() == [digest(EGGS)]
    d.kill()
    again = authority(state)
    assert again.digests() == [digest(EGGS)] and everywhere(state, MILK) == []


@pytest.mark.parametrize("before, after", [
    ([MILK, EGGS], [EGGS, MILK]),                                       # the reviewer's sequence: never two eggs
    ([MILK, MILK, EGGS], [EGGS, MILK, MILK]),                           # duplicates: both milks come back, one egg
])
def test_undo_of_a_migrated_removal_restores_only_the_removed_rows(state, before, after):
    legacy(state, [("add", item) for item in before] + [("remove", MILK)])
    d = authority(state)
    assert d.digests() == [digest(EGGS)]
    out = d.panel.call("undo", person=A, actor=A)
    assert out["verified"] is True                                       # the read-back matches the converted snapshot
    assert d.digests() == sorted(digest(i) for i in after)
    d.kill()
    assert authority(state).digests() == sorted(digest(i) for i in after)    # and the rebuild agrees


def test_the_conversion_leaves_no_copy_of_other_rows_in_a_migrated_removal(state):
    seen = legacy(state, [("add", MILK), ("add", EGGS), ("remove", MILK)])
    authority(state).kill()
    db = sqlite3.connect(f"file:{state[0] / 'perm' / 'zoe-os.db'}?mode=ro", uri=True)
    (raw,) = db.execute("SELECT payload FROM events WHERE seq = ?", (seen["remove2"],)).fetchone()
    rows = db.execute("SELECT seq, target FROM reversals WHERE seq = ?", (seen["remove2"],)).fetchall()
    db.close()
    before = json.loads(raw)["undo"]["before"]
    assert before["reverses"] is True and [r["item"] for r in before["rows"]] == [MILK]       # eggs is no longer copied here
    assert rows == [(seen["remove2"], seen["said0"])]                                          # indexed by kind, at open


def test_a_conversion_killed_halfway_changes_nothing_and_the_next_start_does_it_all(state):
    import authority as authority_mod
    seen = legacy(state, [("add", MILK), ("add", EGGS), ("remove", MILK)])
    path = state[0] / "kernel" / "zoe-os.db"

    class Dies:                                                          # the database, but the second rewrite never happens
        def __init__(self, db):
            self.db, self.updates = db, 0

        def execute(self, sql, *a):
            if sql.startswith("UPDATE events"):
                self.updates += 1
                if self.updates == 2:
                    raise KeyboardInterrupt
            return self.db.execute(sql, *a)

    db = sqlite3.connect(str(path), isolation_level=None)
    before = db.execute("SELECT seq, payload FROM events ORDER BY seq").fetchall()
    with pytest.raises(KeyboardInterrupt):
        authority_mod.convert_legacy(type("S", (), {"_db": Dies(db)})())
    assert db.execute("SELECT seq, payload FROM events ORDER BY seq").fetchall() == before     # all or nothing
    db.close()
    d = authority(state)                                                 # the next start converts everything
    assert d.digests() == [digest(EGGS)]
    d.kill()
    db = sqlite3.connect(f"file:{state[0] / 'perm' / 'zoe-os.db'}?mode=ro", uri=True)
    (raw,) = db.execute("SELECT payload FROM events WHERE seq = ?", (seen["remove2"],)).fetchone()
    db.close()
    assert json.loads(raw)["undo"]["before"].get("reverses") is True
