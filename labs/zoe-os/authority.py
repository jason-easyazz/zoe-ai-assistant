"""The Zoe OS kernel as the AUTHORITY for list add (stage (c), ``docs/knowledge/zoe-os-integration.md``).

    python3 labs/zoe-os/authority.py [--dir STATE_DIR]

The shadow daemon (``shadow_daemon.py``) plus three things an authority needs and a shadow did not:

* a DURABLE list driver. Its rows are REBUILT FROM THE LOG: a row exists exactly when a live, verified ``list.add`` receipt says so
  (minus the verified ``list.remove`` / ``undo`` receipts after it). The receipt is already durable, written in one transaction with
  the closing of its write-ahead intent, and already reached by every forget, so the row and its proof cannot disagree: an
  acknowledged add (the client read a verified receipt) survives a ``kill -9``; one killed before its receipt committed has no
  receipt, so it is absent after the restart (and ``recover`` gives its intent an ``uncertain`` receipt). Never half there. Chosen
  over a second table so there is ONE copy of the household's words to erase, and so nothing here touches the spine's or the
  dispatcher's transaction code;
* erase reach over the wire: ``erase_member`` (everything of the member in the record and the list store) and ``erase_item``
  ("forget that" on one list item). Surface credentials only (a person's decision), idempotent, each answers with a receipt that
  is ``verified`` only when the read-back finds nothing left;
* ``health``: alive, schema version, whether the last write went through: what zoe-data reads to decide whether to fall back.

A revoke is remembered: at start every roster member is granted the brain's acts EXCEPT one whose newest ``brain`` grant change in
the log is a revoke (the shadow re-granted everyone on every start, which an authority must not do).

Lab only, stdlib only; never started by any script or deploy (unit template ``zoe-os-authority-kernel.service`` beside this file).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import threading
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent)]

from shadow_daemon import ACTS, ADAPTERS, SHADOW_BRAIN, serve, state_dir, write_creds  # noqa: E402
from zoe_os.brain.drivers import ASKED, Alarms, Keeper, Lists, Reminders  # noqa: E402
from zoe_os.capability import Person  # noqa: E402
from zoe_os.events import Kind  # noqa: E402
from zoe_os.kernel import Kernel  # noqa: E402
from zoe_os.server import KernelServer  # noqa: E402
from zoe_os.spine import Refused  # noqa: E402

SCHEMA = 1          # the authority's own record shape (the rebuild rule below); bumped when it changes meaning
_RECEIPTS = "SELECT seq, subject, payload FROM events WHERE kind = 'action_receipt' AND tombstoned = 0"


def digest(item: str) -> str:
    """What the reconcile compares: never the words. zoe-data computes the same over its own stored text."""
    return hashlib.sha256(item.strip().lower().encode()).hexdigest()[:16]


class DurableLists(Lists):
    """``Lists`` whose rows are a function of the log. Snapshots are LEAN (a count, or the removed rows): a receipt never carries the
    person's whole list, so an add costs the same on the hundredth item as on the first."""

    def __init__(self, spine) -> None:
        super().__init__()
        self.spine = spine

    @staticmethod
    def key(row_or_args) -> tuple:
        return row_or_args.get("list", "shopping"), row_or_args["item"]

    def snapshot(self, person, args):                       # list.add: how many copies were there
        return {"n": sum(1 for r in self.rows.get(person, []) if self.key(r) == self.key(args))}

    def put_back(self, person, args, before):               # undo of list.add: the row this add appended goes
        rows = self.rows.get(person, [])
        mine = [r for r in rows if self.key(r) == self.key(args) and r["asked"] == args["asked"]]
        if mine:
            rows.remove(mine[-1])

    def removed_snapshot(self, person, args):               # list.remove: the rows it is about to take
        return {"rows": [dict(r) for r in self.rows.get(person, []) if self.key(r) == self.key(args)]}

    def put_back_removed(self, person, args, before):
        self.rows.setdefault(person, []).extend(dict(r) for r in before["rows"])

    def remove(self, person, args):
        rows = self.rows.get(person, [])
        keep = [r for r in rows if self.key(r) != self.key(args)]
        self.rows[person] = keep
        return len(rows) - len(keep)

    def removed(self, person, args, result):
        gone = all(self.key(r) != self.key(args) for r in self.rows.get(person, []))
        return {"item": args["item"], "removed": int(result or 0)} if gone else None

    def register(self, kernel) -> "DurableLists":
        super().register(kernel)
        kernel.actions.register(
            "list.remove", self.remove, self.removed, needs_accept=False, snapshot=self.removed_snapshot,
            undo=self.put_back_removed, doc="Remove an item from one of the person's lists",
            params={"item": {"type": "string", "required": True, "description": "what to remove"},
                    "list": {"type": "string", "required": False, "description": "which list; the shopping list if not said"},
                    "asked": ASKED})
        return self

    # ---- the rebuild: the rows ARE the live verified receipts, in order ----------------------------------------------------------
    def _apply(self, payload: dict, original) -> None:
        action, person = payload.get("action"), payload["_subject"]
        if action in ("list.add", "list.remove") and payload.get("outcome") == "verified" and "undo" in payload:
            args = payload["undo"]["args"]
            if action == "list.add":
                self.add(person, args)
            else:
                self.remove(person, args)
        elif action == "undo" and payload.get("outcome") == "verified" and original is not None:
            saved = original.get("undo") or {}
            if original.get("action") == "list.add" and "args" in saved:
                self.put_back(person, saved["args"], saved.get("before"))
            elif original.get("action") == "list.remove" and "before" in saved:
                self.put_back_removed(person, saved["args"], saved["before"])

    def rebuild(self, person: str | None = None) -> int:
        """Rows from the log: everyone at start, one person after a forget. Returns the number of rows."""
        db = self.spine._db
        sql, args = (_RECEIPTS + " AND subject = ? ORDER BY seq", (person,)) if person else (_RECEIPTS + " ORDER BY seq", ())
        if person:
            self.rows.pop(person, None)
        else:
            self.rows.clear()
        live: dict[int, dict] = {}
        for seq, subject, raw in db.execute(sql, args).fetchall():
            payload = json.loads(raw) if raw else {}
            live[seq] = payload
            if payload.get("action") not in ("list.add", "list.remove", "undo") or subject is None:
                continue
            original = live.get(payload.get("undoes")) if payload.get("action") == "undo" else None
            self._apply({**payload, "_subject": subject}, original)
        for who in [p for p, rows in self.rows.items() if not rows]:
            del self.rows[who]
        return sum(len(r) for r in self.rows.values())


class DurableKeeper(Keeper):
    """On a forget the list rows are REBUILT from what the log still holds (a forget by named events carries no scope, and the lab
    keeper reads that as "everything": the rebuild is right for every scope). Reminders and alarms keep the lab keeper's rule."""

    def __init__(self, lists: DurableLists, *others) -> None:
        super().__init__(*others)
        self.lists = lists

    def on_event(self, ev, ctx) -> None:
        super().on_event(ev, ctx)
        if ev.kind is Kind.FORGET and ev.payload.get("subject"):
            self.lists.rebuild(ev.payload["subject"])


class AuthorityServer(KernelServer):
    """The kernel server plus the authority's ops (``wire.OPS``: ``health``, ``erase_member``, ``erase_item``, ``list_digest``)."""

    def __init__(self, kernel, path: str, lists: DurableLists, others=(), **kw) -> None:
        super().__init__(kernel, path, **kw)
        self.lists, self.others = lists, tuple(others)
        self.last_write_ok: bool | None = None     # None: no write since start

    def _op_act(self, conn, a: dict) -> dict:
        try:
            out = super()._op_act(conn, a)
        except Refused:
            self.last_write_ok = True              # the refusal was recorded: the write path works
            raise
        except Exception:
            self.last_write_ok = False
            raise
        self.last_write_ok = out.get("outcome") == "verified"
        return out

    def _op_health(self, conn, a: dict) -> dict:
        """Alive, the schema, and whether a write goes through RIGHT NOW (a no-op write in its own transaction): an unhealthy answer
        is the fallback signal, and it can never stick, because every health call writes again."""
        db = self.kernel.spine._db
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE meta SET value = value WHERE key = 'authority_schema'")
            db.execute("COMMIT")
            self.last_write_ok = True
        except Exception:  # noqa: BLE001 - a locked or read-only database is an unhealthy kernel, not a crashed one
            if db.in_transaction:
                db.execute("ROLLBACK")
            self.last_write_ok = False
        return {"alive": True, "schema": SCHEMA, "last_write_ok": self.last_write_ok,
                "recovered": len(self.kernel.recovered), "rows": sum(len(r) for r in self.lists.rows.values())}

    def _receipts_for(self, person: str, item_digest: str) -> list[int]:
        """The person's live list receipts for the item (by digest), and the utterances they cite (the words go with them)."""
        db, seqs = self.kernel.spine._db, []
        for seq, _subject, raw in db.execute(_RECEIPTS + " AND subject = ?", (person,)).fetchall():
            p = json.loads(raw) if raw else {}
            args = (p.get("undo") or {}).get("args") or {}
            if p.get("action") in ("list.add", "list.remove") and digest(str(args.get("item", ""))) == item_digest:
                seqs.append(seq)
                seqs.extend(json.loads(db.execute("SELECT COALESCE(caused_by, '[]') FROM events WHERE seq = ?", (seq,)).fetchone()[0]))
        return seqs

    def _live_events(self, person: str) -> int:
        return self.kernel.spine._db.execute(
            "SELECT COUNT(*) FROM people p JOIN events e ON e.seq = p.seq WHERE p.person = ? AND e.tombstoned = 0",
            (person,)).fetchone()[0]

    def _op_erase_member(self, conn, a: dict) -> dict:
        """Everything of the member: their events (and what derives from them), their list rows, their reminder and alarm rows, and
        their brain grant (revoked, so a restart does not re-grant them). ``verified`` only when the read-back finds nothing."""
        self._person_op(conn, "erase a member")
        subject = a["subject"]
        if subject not in self.kernel.grants.people:
            return {"erased": 0, "receipt_seq": None, "verified": True, "known": False}     # never acted for: nothing held
        count = self.kernel.spine.forget(subject, actor=subject).count
        notice = self.kernel.spine.last_seq                                  # the FORGET event: the erase's durable receipt
        self.kernel.grants.revoke(subject, SHADOW_BRAIN["skill"])
        self.lists.rebuild(subject)
        for store in self.others:                                            # the lab reminder / alarm stores, in memory
            (store.at if isinstance(store, Alarms) else store.rows).pop(subject, None)
        left = len(self.lists.rows.get(subject, [])) + self._live_events(subject)
        return {"erased": count, "receipt_seq": notice, "verified": left == 0, "known": True}

    def _op_erase_item(self, conn, a: dict) -> dict:
        """"Forget that" on one list item, by DIGEST (no words cross the wire for an erase), for every member who holds it: production
        cannot say which member added an item on a shared list, so the erase is household-wide (the safe over-forget)."""
        self._person_op(conn, "erase a list item")
        want, erased, notices = a["digest"], 0, []
        for person in sorted(self.kernel.grants.people):
            seqs = self._receipts_for(person, want)
            if seqs:
                erased += self.kernel.spine.forget(person, actor=person, seqs=seqs).count
                notices.append(self.kernel.spine.last_seq)
            self.lists.rebuild(person)
        left = sum(1 for rows in self.lists.rows.values() for r in rows if digest(r["item"]) == want) + sum(
            len(self._receipts_for(p, want)) for p in self.kernel.grants.people)
        return {"erased": erased, "receipt_seq": notices[-1] if notices else None, "verified": left == 0}

    def _op_list_digest(self, conn, a: dict) -> dict:
        self._person_op(conn, "read the list digest")
        return {"digests": sorted(digest(r["item"]) for rows in self.lists.rows.values() for r in rows)}


def revoked(k: Kernel, skill: str) -> set[str]:
    """Members whose newest grant change for ``skill`` in the log is a revoke."""
    last: dict[str, str] = {}
    for (raw,) in k.spine._db.execute("SELECT payload FROM events WHERE kind = 'grant_change' AND tombstoned = 0 ORDER BY seq"):
        p = json.loads(raw) if raw else {}
        if p.get("skill") == skill and p.get("person"):
            last[p["person"]] = p.get("change", "")
    return {who for who, change in last.items() if change == "revoked"}


def build(state: Path):
    people = json.loads((state / "people.json").read_text("utf-8"))["people"]
    if not people or not all(isinstance(p, str) and p for p in people):
        raise SystemExit("people.json has no members: the kernel acts for nobody it does not know")
    (state / "kernel").mkdir(mode=0o700, exist_ok=True)
    k = Kernel(str(state / "kernel" / "zoe-os.db"), [Person(p) for p in people])
    k.spine._db.execute("INSERT OR REPLACE INTO meta VALUES ('authority_schema', ?)", (str(SCHEMA).encode(),))
    lists = DurableLists(k.spine).register(k)
    lists.rebuild()
    reminders, alarms = Reminders(lambda: k.clock()).register(k), Alarms().register(k)
    for manifest in (*ADAPTERS, SHADOW_BRAIN):
        k.grants.install(manifest)
    k.install(DurableKeeper(lists, reminders, alarms))
    no = revoked(k, SHADOW_BRAIN["skill"])
    for person in people:
        if person not in no:
            k.grants.grant(person, SHADOW_BRAIN["skill"], acts=list(ACTS))
    srv = AuthorityServer(k, str(state / "k.sock"), lists, (reminders, alarms))
    creds = {"voice": srv.issue("voice"), "brain": srv.issue("brain"), "panel": srv.issue("panel", surface=True)}
    return k, srv, creds


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", help="state directory (default $ZOE_OS_SHADOW_DIR or ~/.zoe/os-shadow)")
    state = state_dir(ap.parse_args(argv).dir)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.umask(0o077)
    k, srv, creds = build(state)
    write_creds(state, srv.path, creds)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    print(f"zoe-os authority kernel up: {len(k.grants.people)} members, {sum(len(r) for r in srv.lists.rows.values())} list rows, "
          f"{len(k.recovered)} intents recovered as uncertain", flush=True)
    serve(k, srv, stop)
    srv.shutdown()
    k.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
