"""Scenarios over the memory rows, run the same way on ``main`` and on a branch (``tests/test_memory_same_as_main.py``).

The rows are not SQL: they are one palace collection behind ``memory_service.MemoryService``. ``Col`` is a RECORDING stand-in
for that collection: it honours ``where``, keeps every row with its FULL metadata, and logs each write call (upsert, update,
delete) with the whole metadata it carried, so a demoted status, authority, provenance or visibility shows in the record.

A scenario is a short script against one fresh service: rows a MEMBER authored are stored first, then one writer acts. Its
record is every collection write, every audit entry (action, actor, the status before and after), what the call returned or
raised, and the rows as they stand at the end. Nothing here asserts: the record of ``main`` is the assertion.

Invented words and ids only.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import re
from typing import Any

ANN, BEN = "member-ann", "member-ben"
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_UNDO: list = []
#: stamps of WHICH BUILD of a derived classifier judged the row (a version, a digest of its word lists): they follow what the
#: process has loaded, not what a writer did. The judgement itself (``sensitivity``, ``gate_t``) stays in the record.
_VERSIONS = ("sensitivity_v", "sensitivity_h", "gate_v", "gate_h")
_OPS = {"$eq": lambda a, b: a == b, "$ne": lambda a, b: a != b, "$in": lambda a, b: a in b, "$nin": lambda a, b: a not in b,
        "$gt": lambda a, b: a is not None and a > b, "$gte": lambda a, b: a is not None and a >= b,
        "$lt": lambda a, b: a is not None and a < b, "$lte": lambda a, b: a is not None and a <= b}


def _match(meta: dict, where) -> bool:
    if not where:
        return True
    for key, want in where.items():
        if key == "$and":
            ok = all(_match(meta, w) for w in want)
        elif key == "$or":
            ok = any(_match(meta, w) for w in want)
        elif isinstance(want, dict):
            ok = all(_OPS[op](meta.get(key), v) for op, v in want.items())
        else:
            ok = meta.get(key) == want
        if not ok:
            return False
    return True


def _words(text: str) -> set:
    return set(re.findall(r"[a-z0-9']+", (text or "").lower()))


class Col:
    """The palace collection: rows with full metadata, every write logged."""

    def __init__(self, name: str, log: list):
        self.name, self.log, self.rows = name, log, {}

    def upsert(self, ids, documents=None, metadatas=None, **_kw):
        for n, i in enumerate(ids):
            doc = documents[n] if documents is not None else self.rows.get(i, ("", {}))[0]
            meta = dict(metadatas[n]) if metadatas is not None else dict(self.rows.get(i, ("", {}))[1])
            self.log.append([self.name, "upsert", i, doc, meta])
            self.rows[i] = (doc, meta)

    add = upsert

    def update(self, ids, documents=None, metadatas=None, **_kw):
        for n, i in enumerate(ids):
            if i not in self.rows:                  # as Chroma: an unknown id is skipped
                self.log.append([self.name, "update-missing", i])
                continue
            doc = documents[n] if documents is not None else self.rows[i][0]
            meta = dict(metadatas[n]) if metadatas is not None else dict(self.rows[i][1])
            self.log.append([self.name, "update", i, doc, meta])
            self.rows[i] = (doc, meta)

    def delete(self, ids=None, where=None, **_kw):
        gone = [i for i in (ids if ids is not None else list(self.rows)) if i in self.rows and _match(self.rows[i][1], where)]
        for i in gone:
            del self.rows[i]
        self.log.append([self.name, "delete", sorted(gone)])

    def _pick(self, ids, where):
        keys = [i for i in ids if i in self.rows] if ids is not None else list(self.rows)
        return [k for k in keys if _match(self.rows[k][1], where)]

    def get(self, ids=None, where=None, include=None, limit=None, offset=None, **_kw):
        if isinstance(ids, str):
            ids = [ids]
        keys = self._pick(ids, where)[(offset or 0):]
        if limit is not None:
            keys = keys[:limit]
        return {"ids": keys, "documents": [self.rows[i][0] for i in keys], "metadatas": [dict(self.rows[i][1]) for i in keys]}

    def query(self, query_texts=None, query_embeddings=None, n_results=10, where=None, include=None, **_kw):
        want = _words((query_texts or [""])[0])
        scored = sorted(((1.0 - len(want & _words(self.rows[i][0])) / (len(want | _words(self.rows[i][0])) or 1), i)
                         for i in self._pick(None, where)))[:n_results]
        keys = [i for _d, i in scored]
        return {"ids": [keys], "documents": [[self.rows[i][0] for i in keys]], "metadatas": [[dict(self.rows[i][1]) for i in keys]],
                "distances": [[d for d, _i in scored]]}

    def count(self):
        return len(self.rows)


def _mask(value):
    if isinstance(value, str):
        return _STAMP.sub("<t>", value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        return "<t>" if value > 1e9 else round(value, 4)
    if isinstance(value, int):
        return "<t>" if value > 10 ** 9 else value
    if isinstance(value, dict):
        return {str(k): ("<v>" if k in _VERSIONS else _mask(v)) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_mask(v) for v in value]
    return _mask(str(value))


def _plain(value):
    return json.loads(json.dumps(_mask(value), default=str))


def _set(obj, name, value) -> None:
    _UNDO.append((obj, name, getattr(obj, name, _UNDO)))
    setattr(obj, name, value)


def _restore() -> None:
    while _UNDO:
        obj, name, value = _UNDO.pop()
        if value is _UNDO:
            delattr(obj, name)
        else:
            setattr(obj, name, value)


class World:
    """One fresh service on a recording collection. ``w.log`` is the record."""

    def __init__(self):
        import memory_service

        self.ms = memory_service
        self.log: list = []
        self.col, self.audit = Col("rows", self.log), Col("audit", [])
        self.svc = memory_service.MemoryService(data_dir="/nonexistent/zoe-memory-scenarios")
        self.svc._collection = lambda: self.col
        self.svc._audit_collection = lambda: self.audit

        async def audit(**kw):
            b, a = kw.get("before") or {}, kw.get("after") or {}
            self.log.append(["audit", kw.get("action"), kw.get("actor"), kw.get("user_id"), b.get("status"), a.get("status")])

        async def opted_in(_uid):
            return False

        async def marked(user_id, **kw):
            self.log.append(["erase-mark", user_id, kw.get("actor")])

        self.svc._append_audit = audit
        self.svc._schedule_tick = lambda *a, **k: None      # access ticks (bookkeeping on every recall, written later) are not recorded
        # The sensitivity label of a new row is settled later by a background classifier whose answers this process caches:
        # whether a row carries the label's first or settled form depends on what ran before. Not a writer's doing: left out.
        import restraint

        _set(restraint, "stamp", lambda md, text: None)
        _set(memory_service._erase_watermark, "record", marked)
        _set(memory_service, "_user_opted_out", opted_in)
        _set(memory_service, "get_memory_service", lambda: self.svc)
        self.takes_acting = "acting" in inspect.signature(memory_service.MemoryService.ingest).parameters

    def who(self, kw: dict, member) -> dict:
        """``main`` has no "who is acting"; a branch that requires it is told what this scenario's caller is."""
        if not self.takes_acting:
            return kw
        import memory_actor

        return {**kw, "acting": memory_actor.member(member) if member else memory_actor.AUTOMATIC}

    async def call(self, method: str, *args, member=None, **kw):
        """One service call, recorded with what it returned or raised."""
        try:
            out = await getattr(self.svc, method)(*args, **self.who(kw, member))
        except Exception as exc:  # noqa: BLE001 - a refusal is part of the record
            self.log.append(["raised", method, type(exc).__name__, str(exc)[:120]])
            return None
        self.log.append(["returned", method, self._brief(out)])
        return out

    @staticmethod
    def _brief(out):
        if hasattr(out, "id") and hasattr(out, "text"):
            return {"id": out.id, "text": out.text, "status": (getattr(out, "metadata", None) or {}).get("status")}
        return out

    async def said(self, text: str, *, user=ANN, source="voice_fact", **kw):
        """A row the MEMBER authored (their own words), stored and returned."""
        return await self.call("ingest", text, member=user, user_id=user, source=source, confidence=0.9,
                               source_excerpt=kw.pop("excerpt", text), **kw)

    def record(self) -> list:
        end = [["row", i, doc, meta] for i, (doc, meta) in sorted(self.col.rows.items())]
        return _plain(self.log + end)


# ── the writers, by the labels each call site passes (tests/memory_writers.py holds the sites) ────────────────────────────────
#: (label, ingest keywords): how each MEMBER entry point stores what a member said
MEMBER_REMEMBER = {
    "spoken_teach": dict(source="brain_tool", origin="explicit_teach"),
    "spoken_brain_tool": dict(source="brain_tool"),
    "fast_tier": dict(source="voice_fact"),
    "mcp_tool": dict(source="mcp"),
    "agent_tool": dict(source="zoe_agent"),
    "skybridge": dict(source="skybridge_action"),
    "screen_proposal": dict(source="manual", origin="proposal", status="pending"),
    "curator_add": dict(source="curator", origin="curator"),
    "day_review": dict(source="omi_review", origin="omi_review", authority="user_confirmed"),
    "correction_new": dict(source="conversation_correction"),
    "note_mirror": dict(source="note_created", memory_type="note"),
    "journal_mirror": dict(source="journal_created", memory_type="journal"),
    "person_mirror": dict(source="person_created", memory_type="person", entity_type="person", entity_id="per0001"),
}
#: (label, actor): how each MEMBER entry point corrects a row
MEMBER_CORRECT = {"conversation_correction": "conversation_correction", "the_member": ANN, "curator": "curator",
                  "value_correction": "value_correction", "hold_the_fact": ANN}
#: (label, ingest keywords): the AUTOMATIC writers that create rows
AUTOMATIC_INGEST = {
    "turn_digest": dict(source="turn_digest"), "voice_turn_digest": dict(source="voice_turn_digest"),
    "digest": dict(source="digest"), "digest_held": dict(source="digest", hold="observation"),
    "synthesis": dict(source="synthesis", memory_type="insight"), "music_digest": dict(source="music_digest"),
    "chat_regex": dict(source="chat_regex"), "chat_regex_fallback": dict(source="chat_regex_fallback"),
    "conversation": dict(source="conversation"), "voice_regex": dict(source="voice_regex"),
    "person_extractor_llm": dict(source="conversation", origin="person_extractor_llm"),
    "pasted_content": dict(source="pasted_content"), "profile_analysis": dict(source="profile-analysis"),
    "hindsight": dict(source="hindsight_retain_candidate", status="pending"),
    "baseline": dict(source="mempalace_baseline"), "packet_measure": dict(source="prompt_packet_measure"),
}
#: the AUTOMATIC actors that edit, supersede or merge
AUTOMATIC_ACTORS = ("turn_digest", "digest", "consolidation", "implicit_supersede", "chat_regex", "conversation",
                    "person_extractor_llm", "decay_sweep", "system", "operator", "quote_retire")

OLD = "User's dog is named Teddy."
NEW = "User's dog is named Rex."
OTHER = "User likes quiet mornings."


def _remember(label):
    async def run(w: World):
        await w.call("ingest", OLD, member=ANN, user_id=ANN, confidence=0.9, source_excerpt="my dog is named Teddy",
                     **MEMBER_REMEMBER[label])
    return run


def _remember_again(label):
    async def run(w: World):
        await w.said(OLD)
        await w.call("ingest", NEW, member=ANN, user_id=ANN, confidence=0.9, source_excerpt="my dog is named Rex",
                     **MEMBER_REMEMBER[label])
    return run


def _correct(label):
    async def run(w: World):
        row = await w.said(OLD)
        await w.call("review", row.id, member=ANN, decision="edit", edits=NEW, actor=MEMBER_CORRECT[label],
                     source_excerpt="no, my dog is named Rex")
    return run


def _auto_ingest(label, seed_source):
    """An automatic writer stores a fact that CONTRADICTS a row the member authored."""
    async def run(w: World):
        await w.said(OLD, source=seed_source)
        await w.call("ingest", NEW, user_id=ANN, confidence=0.8, **AUTOMATIC_INGEST[label])
    return run


def _auto_ingest_same(label):
    """An automatic writer stores the SAME fact the member stated (a duplicate must not demote the member's row)."""
    async def run(w: World):
        await w.said(OLD)
        await w.call("ingest", OLD, user_id=ANN, confidence=0.5, **AUTOMATIC_INGEST[label])
    return run


def _auto_edit(actor, seed_source):
    async def run(w: World):
        row = await w.said(OLD, source=seed_source)
        await w.call("review", row.id, decision="edit", edits=NEW, actor=actor)
    return run


def _auto_supersede(actor, seed_source):
    async def run(w: World):
        row = await w.said(OLD, source=seed_source)
        new = await w.call("ingest", NEW, user_id=ANN, source="digest", confidence=0.8)
        if new is not None:
            await w.call("supersede_by", ANN, row.id, new.id, actor=actor)
    return run


def _auto_decide(actor, decision):
    async def run(w: World):
        row = await w.said(OLD)
        await w.call("review", row.id, decision=decision, actor=actor)
    return run


async def _auto_merge_into_inferred(w: World):
    """The night merge asked to archive the member's row as a duplicate of an inferred keeper."""
    row = await w.said(OLD)
    keeper = await w.call("ingest", "User has a dog called Teddy.", user_id=ANN, source="digest", confidence=0.8)
    if keeper is not None:
        await w.call("archive_duplicate", row.id, keeper.id, actor="consolidation")


async def _auto_merge_into_member(w: World):
    dup = await w.call("ingest", "User has a dog called Teddy.", user_id=ANN, source="digest", confidence=0.8)
    row = await w.said(OLD)
    if dup is not None:
        await w.call("archive_duplicate", dup.id, row.id, actor="consolidation")


async def _auto_retire_with_quote(w: World):
    row = await w.said(OLD)
    await w.call("retire_with_quote", ANN, row.id, quote="we gave Teddy away", turn_ref="turn0001", lane="chat", cue="gave away")


async def _auto_sweep(w: World):
    await w.said(OLD)
    await w.call("ingest", OTHER, user_id=ANN, source="digest", confidence=0.4)
    await w.call("sweep_soft_archive", user_id=ANN, actor="decay_sweep", min_age_days=0, score_threshold=1.0)


async def _auto_candidate(w: World):
    row = await w.said(OLD)
    await w.call("record_candidate", NEW, user_id=ANN, writer="person_extractor_llm", contradicts=row.id, kind="other")


async def _auto_relink(w: World):
    row = await w.said("Kip is User's brother.", memory_type="person", entity_type="person", entity_id="per0001")
    await w.call("relink_entity", ANN, row.id, "person", "per0002")


async def _auto_archive_entity(w: World):
    await w.said("Kip is User's brother.", memory_type="person", entity_type="person", entity_id="per0001")
    await w.call("ingest", "Kip likes kites.", user_id=ANN, source="conversation", origin="person_extractor_llm",
                 memory_type="person", entity_type="person", entity_id="per0001")
    await w.call("archive_by_entity", "per0001", ANN, member=ANN)


async def _auto_challenge(w: World):
    row = await w.call("ingest", OTHER, user_id=ANN, source="synthesis", memory_type="insight", confidence=0.6)
    if row is not None:
        await w.call("flag_challenged", row.id, member=ANN, user_id=ANN, utterance="that is not true")


async def _member_approve_held(w: World):
    held = await w.call("ingest", OTHER, user_id=ANN, source="digest", confidence=0.6, status="pending")
    if held is not None:
        await w.call("review", held.id, member=ANN, decision="approve", actor=ANN)


async def _member_reject_held(w: World):
    held = await w.call("ingest", OTHER, user_id=ANN, source="digest", confidence=0.6, status="pending")
    if held is not None:
        await w.call("review", held.id, member=ANN, decision="reject", actor=ANN)


async def _member_forget_row(w: World):
    row = await w.said(OLD)
    await w.said(OTHER)
    await w.call("review", row.id, member=ANN, decision="reject", actor=ANN)
    await w.call("erase_rows", ANN, [row.id], member=ANN, actor=ANN)


async def _member_archive_row(w: World):
    row = await w.said(OLD)
    await w.call("review", row.id, member=ANN, decision="archive", actor=ANN)


async def _member_edits_anothers_row(w: World):
    row = await w.said(OLD, user=BEN)
    await w.call("review", row.id, member=ANN, decision="edit", edits=NEW, actor=ANN)


async def _member_household_row(w: World):
    row = await w.call("ingest", "The bins go out on Tuesday.", member=ANN, user_id=ANN, source="voice_fact", confidence=0.9,
                       scope="shared", source_excerpt="the bins go out on Tuesday")
    if row is not None:
        await w.call("review", row.id, member=BEN, decision="edit", edits="The bins go out on Monday.", actor=BEN)


async def _member_curated_standing(w: World):
    row = await w.said(OLD)
    await w.call("set_curated_standing", ANN, [row.id], member=ANN, protect=True, actor=ANN)


async def _guest_remember(w: World):
    await w.call("ingest", OLD, member="guest", user_id="guest", source="voice_fact", confidence=0.9)


def scenarios() -> dict:
    out: dict = {}
    for label in MEMBER_REMEMBER:
        out[f"member_remember_{label}"] = _remember(label)
        out[f"member_remember_again_{label}"] = _remember_again(label)
    for label in MEMBER_CORRECT:
        out[f"member_correct_{label}"] = _correct(label)
    for label in AUTOMATIC_INGEST:
        out[f"auto_ingest_over_stated_{label}"] = _auto_ingest(label, "voice_fact")
        out[f"auto_ingest_over_confirmed_{label}"] = _auto_ingest(label, "review_ui")
        out[f"auto_ingest_same_{label}"] = _auto_ingest_same(label)
    for actor in AUTOMATIC_ACTORS:
        out[f"auto_edit_stated_{actor}"] = _auto_edit(actor, "voice_fact")
        out[f"auto_edit_confirmed_{actor}"] = _auto_edit(actor, "review_ui")
        out[f"auto_supersede_stated_{actor}"] = _auto_supersede(actor, "voice_fact")
        out[f"auto_archive_stated_{actor}"] = _auto_decide(actor, "archive")
        out[f"auto_reject_stated_{actor}"] = _auto_decide(actor, "reject")
    out.update(
        auto_merge_member_row_into_inferred=_auto_merge_into_inferred, auto_merge_inferred_into_member_row=_auto_merge_into_member,
        auto_retire_with_quote=_auto_retire_with_quote, auto_sweep=_auto_sweep, auto_candidate=_auto_candidate,
        auto_relink=_auto_relink, auto_archive_entity=_auto_archive_entity, member_challenge=_auto_challenge,
        member_approve_held=_member_approve_held, member_reject_held=_member_reject_held, member_forget_row=_member_forget_row,
        member_archive_row=_member_archive_row,
        member_edits_anothers_row=_member_edits_anothers_row, member_household_row=_member_household_row,
        member_curated_standing=_member_curated_standing, guest_remember=_guest_remember)
    try:
        import memory_entry_scenarios

        out.update(memory_entry_scenarios.scenarios())
    except ImportError:
        pass
    return out


def _isolated() -> None:
    """Refuse to run outside the test configuration: a rejected or forgotten row reaches stores that default under the live
    ``~/.zoe`` (the reject counters, the shadow's parked forgets). ``tests/conftest.py`` points each at a temporary directory."""
    import os

    live = os.path.join(os.path.expanduser("~"), ".zoe")
    for var in ("ZOE_MEMORY_REJECT_LEDGER", "ZOE_OS_SHADOW_DIR", "MEMPALACE_DATA_DIR", "ZOE_LOG_DIR"):
        value = os.environ.get(var, "")
        if not value or os.path.abspath(value).startswith(live):
            raise RuntimeError(f"memory scenarios need the test configuration ({var} is not isolated): import tests/conftest.py first")


_STORES = ("erase_watermark", "exact_words", "night_store", "struggle_detector")     # Postgres in production; in process here


def _fresh(done: bool = False) -> None:
    """Every scenario starts from nothing: a forget made by an earlier scenario shields this process against the same words
    (``memory_forgotten``'s overlay, ``turn_forget``'s retracted turns), so without this a record would depend on what ran
    before it. The stores a forget fails closed on are the in-process ones, as in ``tests/conftest.py``."""
    import importlib

    import memory_forgotten
    import turn_forget

    memory_forgotten.reset_state()
    turn_forget.reset()
    for name in _STORES:
        mod = importlib.import_module(name)
        mod.set_backend(None if done else mod.MemoryBackend())


def run(name: str) -> list:
    _isolated()

    async def go():
        _fresh()
        w = World()
        try:
            await scenarios()[name](w)
            for task in list(getattr(w.svc, "_background_tasks", ()) or ()):
                try:
                    await asyncio.wait_for(task, 5)
                except Exception:  # noqa: BLE001
                    pass
            return w.record()
        finally:
            _restore()
            _fresh(done=True)

    return asyncio.run(go())


def run_all() -> dict:
    return {name: run(name) for name in sorted(scenarios())}


def pack(records: dict) -> dict:
    """The record as it is stored: each distinct entry once (a seeded row is the same entry in most scenarios), and per
    scenario the list of entry numbers. The rows-at-the-end are left out: they are the last write of each id."""
    entries: dict = {}
    names = {}
    for name in sorted(records):
        names[name] = [entries.setdefault(json.dumps(e, separators=(",", ":"), sort_keys=True), len(entries))
                       for e in records[name] if e[0] != "row"]
    return {"entries": [json.loads(e) for e in entries], "scenarios": names}


def unpack(packed: dict) -> dict:
    return {name: [packed["entries"][i] for i in ids] for name, ids in packed["scenarios"].items()}


def writes_only(record: list) -> list:
    return [e for e in record if e[0] != "row"]


def dump(packed: dict, path: str) -> None:
    ent, names = packed["entries"], sorted(packed["scenarios"])
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"entries": [\n')
        fh.write(",\n".join(", ".join(json.dumps(e, separators=(",", ":"), sort_keys=True) for e in ent[i:i + 4])
                            for i in range(0, len(ent), 4)))
        fh.write('\n], "scenarios": {\n')
        fh.write(",\n".join(", ".join(f"{json.dumps(n)}: {json.dumps(packed['scenarios'][n], separators=(',', ':'))}" for n in names[i:i + 6])
                            for i in range(0, len(names), 6)))
        fh.write("\n}}\n")


if __name__ == "__main__":      # python3 <this file> <out.json>, with the CWD the zoe-data to record (an export of main)
    import sys

    sys.path[:0] = [".", "tests"]
    import conftest  # noqa: F401 - the CWD's test configuration: every live store path pinned to a temporary directory

    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    import memory_scenarios

    dump(pack(memory_scenarios.run_all()), sys.argv[1])
