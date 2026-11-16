"""Durable kernel rows (reminders, alarms): REBUILT FROM THE LOG, the authority list store's rule (``authority.DurableLists``).

Every action touches exactly ONE row, and its read-back (the receipt's ``proof``) carries that row's whole state after the write:
``{"id": ..., "row": {...}}``, or ``"row": None`` when it removed it. The snapshot (``receipt.undo.before``) is that row before. So
the rows are a pure function of the live, verified receipts, replayed in order:

* a row EXISTS only while the receipt that created it is live: a forget that takes the create takes the row, whatever later
  receipts say (a later edit's proof cannot resurrect it);
* a later verified receipt for the row sets it to its proof; a verified ``undo`` puts back the reversed receipt's ``before``;
* an ``uncertain`` or ``unverified`` receipt changes nothing.

Hence an acknowledged write (its verified receipt committed with the closing of its intent) survives a ``kill -9``; one killed
before that commit has no verified receipt and is absent after the restart, never half there; an undo after a restart is replayed
from its receipt; and a forget of any scope is followed by a rebuild, so its rows do not come back. ONE copy of the words: the
receipts, which every forget already reaches.
"""
from __future__ import annotations

import json

_RECEIPTS = "SELECT seq, payload FROM events WHERE kind = 'action_receipt' AND tombstoned = 0 ORDER BY seq"


class DurableRows:
    prefix = "x"                 # row ids: <prefix><the asking event's seq>[.n]
    creates = ""                 # the action that makes a row
    actions: tuple = ()          # every action over these rows (``creates`` included)
    removes: tuple = ()          # the actions that take a row away (a cancel): REVERSALS of the create, for the forget rule

    def __init__(self, spine, clock) -> None:
        self.spine, self.clock = spine, clock
        self.rows: dict[str, dict] = {}

    # ---- one row per action: id, snapshot, undo, read-back --------------------------------------------------------------------
    def fresh(self, args: dict) -> str:
        """The id a create would mint: deterministic from the asking event, so the snapshot (taken first) and the write agree."""
        base = rid = f"{self.prefix}{args['asked']}"
        n = 1
        while rid in self.rows:
            n += 1
            rid = f"{base}.{n}"
        return rid

    def target(self, action: str, args: dict) -> str:
        return self.fresh(args) if action == self.creates else str(args.get("id", ""))

    def source_of(self, rid: str) -> int | None:
        """The request that CREATED row ``rid`` (ids are ``<prefix><that request's seq>[.n]``)."""
        head = str(rid)[len(self.prefix):].split(".")[0] if str(rid).startswith(self.prefix) else ""
        return int(head) if head.isdigit() else None

    def snapshot_for(self, action: str):
        """The row before the write, plus ``source`` (the creating request: the spine links every later action over the row to it,
        so forgetting the row takes its edits, snoozes and cancel, and forgetting a cancel takes the row) and ``reverses`` for an
        action that takes the row away (``Spine._row_source``)."""
        def snapshot(person, args):
            rid = self.target(action, args)
            row = self.rows.get(rid)
            out = {"provenance": 1, "id": rid, "row": dict(row) if row else None}
            source = args.get("asked") if action == self.creates else self.source_of(rid)
            if type(source) is int:
                out["source"] = source
            if action in self.removes:
                out["reverses"] = True
            return out
        return snapshot

    def put_back(self, person, args, before) -> None:
        if before.get("id") is None:
            return
        if before["row"] is None:
            self.rows.pop(before["id"], None)
        else:
            self.rows[before["id"]] = dict(before["row"])

    def summary(self, row: dict) -> dict:
        """What a client compares besides the row (the shadow reads ``note`` / ``alarm``): per store."""
        return {}

    def read_back(self, person, args, result) -> dict | None:
        """The proof: the row AS STORED, when it is what the write meant. A driver returns ``(row id, the row it must now be)``,
        ``None`` for gone; the read-back looks again rather than trusting that."""
        rid, want = result
        row = self.rows.get(rid)
        if want is None:
            return {"id": rid, "row": None} if row is None else None
        return {"id": rid, "row": dict(row), **self.summary(row)} if row == want else None

    def undo_check(self, person, payload: dict, actor) -> str:
        """An undo puts back the row as it was before ONE action: only while the row is still what that action left. Changed
        since (a later edit, by anyone: the owner marking it private, say) or gone: refused, never a silent overwrite."""
        proof = payload.get("proof") or {}
        rid = proof.get("id")
        if rid is None or self.rows.get(rid) != proof.get("row"):
            return "it has changed since"
        return ""

    def register_all(self, kernel, specs: dict) -> None:
        """``specs``: action -> (do, check, doc, params). Every action is reversible and has the one snapshot rule."""
        for action, (do, check, doc, params) in specs.items():
            kernel.actions.register(action, do, self.read_back, needs_accept=False, snapshot=self.snapshot_for(action),
                                    undo=self.put_back, doc=doc, params=params, check=check, undo_check=self.undo_check)

    # ---- the rebuild ------------------------------------------------------------------------------------------------------------
    def rebuild(self) -> int:
        self.rows.clear()
        live: dict[int, dict] = {}
        made: set[str] = set()
        for seq, raw in self.spine._db.execute(_RECEIPTS).fetchall():
            p = json.loads(raw) if raw else {}
            live[seq] = p
            if p.get("outcome") != "verified":
                continue
            action = p.get("action")
            if action in self.actions:
                proof = p.get("proof") or {}
                rid = proof.get("id")
                if action == self.creates:
                    made.add(rid)
                if self.anchored(rid, made):
                    self.put_back(None, None, {"id": rid, "row": proof.get("row")})
            elif action == "undo":
                orig = live.get(p.get("undoes")) or {}
                before = (orig.get("undo") or {}).get("before") or {}
                if orig.get("action") in self.actions and self.anchored(before.get("id"), made):
                    self.put_back(None, None, before)
                    if orig.get("action") == self.creates:
                        made.discard(before["id"])
        return len(self.rows)

    @staticmethod
    def anchored(rid, made: set) -> bool:
        """A row's later receipts count only while the receipt that created it is live."""
        return rid in made

    def touching(self, person: str) -> list[tuple[int, str]]:
        """(seq, subject) of every live receipt over these rows whose row is ``person``'s (owner or setter), whoever acted: what
        a whole-member erase must also take, because another member's edit of a shared row carries its words."""
        out = []
        for seq, subject, raw in self.spine._db.execute(
                "SELECT seq, subject, payload FROM events WHERE kind = 'action_receipt' AND tombstoned = 0").fetchall():
            p = json.loads(raw) if raw else {}
            if p.get("action") not in self.actions:
                continue
            rows = [(p.get("proof") or {}).get("row"), ((p.get("undo") or {}).get("before") or {}).get("row")]
            if any(r and person in (r.get("owner"), r.get("by")) for r in rows):
                out.append((seq, subject))
        return out

    def held_for(self, person: str) -> int:
        return sum(1 for r in self.rows.values() if person in (r.get("owner"), r.get("by")))
