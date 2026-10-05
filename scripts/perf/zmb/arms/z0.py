"""Arm Z0: the current ``MemoryService`` (+ its deterministic extractor, forget handler and walls),
in-process over the lab's in-memory collection.

Z0 is the control arm of the bake-off. ``Z0Arm(off=frozenset({...}))`` is the same arm with named
features switched OFF (``lab_driver.CONTROLS``): with ``off`` = every control it is **Z0-off**, the
negative control whose S1 signature (the owner's row superseded by a model's) must appear. The controls
are applied around each operation (never left on), so a throwaway arm cannot poison the next one.

What each ``Turn`` becomes (the lab has no brain, so a model writer's output is SCRIPTED via ``proposes``):

* ``owner_taught``  -> ``ingest(text, source="voice_fact")`` (an explicit "remember that ...")
* ``owner_typed``   -> the real ``memory_extractor.extract_candidates`` -> the write-quality gate ->
  ``ingest(source="chat_regex")`` (the lab skips the reconcile / person-link steps: they need Postgres)
* ``owner_voice_verified`` / ``panel_unverified`` -> the same extraction as ``voice_regex`` with
  ``speaker_verified`` True / False
* ``third_party`` / ``pasted_email`` -> treated as the OWNER's typed words - which is what the live
  pipeline does today (it cannot tell a pasted email from the owner; a known target)
* ``assistant``     -> never mined, never written
* ``system_writer`` -> each proposed fact through ``ingest`` (``op=say``), ``review(edit)`` or
  ``review(archive)`` under the writer's own label, with the turn text as the user-evidence anchor

Z0 runs with the flags the live service runs with, switched on around every operation (``_LIVE_FLAGS``): the code
defaults are OFF, ``services/zoe-data/.env`` turns them ON, and a bench that measured the defaults would be measuring
a system that is not deployed. The ``supersede`` control switches ``ZOE_MEMORY_IMPLICIT_SUPERSEDE`` back off.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib
import os
import importlib.util
import re
import sys
import types
from typing import Any

from .base import Arm, IngestReport, OPTIONAL_ROW_KEYS, ROW_KEYS, Turn

#: the flags ``services/zoe-data/.env`` turns ON that the code leaves OFF (a bench of the defaults would measure
#: a system that is not deployed): the implicit supersede (nightly conflict pass, ``invalid_at``) and temporal edges
_LIVE_FLAGS = {"ZOE_MEMORY_IMPLICIT_SUPERSEDE": "1", "ZOE_TEMPORAL_RELATIONSHIPS_ENABLED": "1"}

DEMO_USER_RE = re.compile(r"^demo_bar_[0-9a-f]{8}$")

_STOP = frozenset("""a an the is are was were be been am do does did what whats when where who whom whose why how which
my our your their his her its me i we you he she they it of to in on at for from with about and or any some tell
please remind know have has had can could would will shall should still now currently""".split())


def content_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9']+", (text or "").lower())
            if t not in _STOP and len(t) > 2}


def reader_answer(rows: "list[dict[str, Any]]", query: str, *, sycophantic: bool = False) -> str:
    """The lab's scripted READER over a recall packet. Honest: answer only from a row that carries every
    content word of the question, else decline. Sycophantic (the ``reader`` control): always answer from
    the nearest row. It stands in for the brain so the canary scorer can be exercised brain-free; it is an
    instrument check, never a statement about Zoe's real reply."""
    if sycophantic:
        return rows[0]["text"] if rows else "I don't have that saved."
    need = content_tokens(query)
    for r in rows:
        if need and need <= content_tokens(r.get("text", "")) | _bare(r.get("text", "")):
            return r["text"]
    return "I don't have that saved."


def _digest(*parts: str) -> str:
    """A STABLE short id (``hash()`` is salted per process: a lab run must be reproducible)."""
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:10]


def _bare(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9']+", (text or "").lower())}


#: identity label -> (the user id the lab writes as, (member mode, is a minor)). ``guest`` / ``voice-guest``
#: are the service's own guest sentinels (``user_filters.GUEST_USERS``): they own no memory.
IDENTITIES = {
    "consenting_owner": ("demo_bar_00000001", ("companion", False)),
    "owner_no_mode": ("demo_bar_00000002", ("unset", False)),
    "minor": ("demo_bar_00000003", ("kid", True)),
    "guest": ("guest", ("unset", False)),
    "voice_guest": ("voice-guest", ("unset", False)),
}


class Z0Arm(Arm):
    name = "Z0"
    capabilities = frozenset({"clock", "controls", "reader", "identities", "idle_pass", "conflict_pass", "edges"})
    #: ``disk`` = the arm can run a cell over REAL Chroma and byte-scan what is left on disk. Needs chromadb
    #: (not installed in the slim CI lane: the disk cells SKIP there with the reason, never pass).
    if importlib.util.find_spec("chromadb") is not None:
        capabilities = capabilities | {"disk"}

    def __init__(self, off: "frozenset[str] | set[str]" = frozenset(), name: str | None = None):
        from .. import lab_driver
        self._lab = lab_driver
        self.svc = lab_driver.load_service()
        self.off = frozenset(off)
        if name:
            self.name = name
        elif self.off:
            self.name = "Z0-off" if self.off == frozenset(lab_driver.CONTROLS) else \
                "Z0-off[" + ",".join(sorted(self.off)) + "]"
        self._lab_service = None
        self._loop = asyncio.new_event_loop()
        self._user = ""
        self._clock = 0.0
        self._prev_user = ""
        self._refused = 0
        self._edge_db = None
        self._heap_scrub_ours = False

    # ── lifecycle ─────────────────────────────────────────────────────────
    def reset(self, user_id: str, *, disk: bool = False) -> None:
        if not DEMO_USER_RE.match(user_id or ""):
            raise ValueError(f"refusing non-demo identity {user_id!r} (must match {DEMO_USER_RE.pattern})")
        if self._lab_service is not None:
            self._lab_service.close()
        self._close_edge_db()
        self._lab_service = self._lab.LabService(self.svc, tag=self.name, disk=disk)
        if disk and not self._heap_scrub_ours:
            # The disk cells measure the RECOMMENDED deployment: host heap scrubbing on (HNSW heap residue is a
            # separate class with its own fix; unscrubbed, chromadb 0.6.3 left a name in length.bin in 5 of 60
            # runs - docs/knowledge/forgotten-text-physical-erase.md section 4). Restored on close().
            import memory_residue
            self._heap_scrub_ours = (not memory_residue.heap_scrub_on()) and memory_residue.enable_heap_scrub()
        self._user = user_id
        self._clock = 0.0
        self._prev_user = ""
        self._refused = 0
        self.svc.memory_tombstones.clear_all(user_id)

    def _close_edge_db(self) -> None:
        if self._edge_db is not None and not self._loop.is_closed():
            try:
                self._run(self._edge_db.close())
            except Exception:  # noqa: BLE001 - an in-memory database: nothing to lose
                pass
        self._edge_db = None

    def close(self) -> None:
        self._close_edge_db()
        if self._lab_service is not None:
            self._lab_service.close()
            self._lab_service = None
        if self._heap_scrub_ours:
            import memory_residue
            memory_residue.disable_heap_scrub()
            self._heap_scrub_ours = False
        if not self._loop.is_closed():
            self._loop.close()

    @property
    def service(self):
        if self._lab_service is None:
            raise RuntimeError("call reset(user_id) first")
        return self._lab_service.service

    # ── plumbing ──────────────────────────────────────────────────────────
    @contextlib.contextmanager
    def _ctl(self):
        """Controls off + the fake tombstone clock, for ONE operation."""
        mt = self.svc.memory_tombstones
        real_time = mt.time
        clock = self._clock
        mt.time = types.SimpleNamespace(monotonic=lambda: real_time.monotonic() + clock)
        stub = types.ModuleType("pending_suggestions")

        async def no_offers(_uid, _name):
            return 0
        stub.resolve_person_offers_by_name = no_offers
        had = sys.modules.get("pending_suggestions")
        sys.modules["pending_suggestions"] = stub
        saved_env = {k: os.environ.get(k) for k in _LIVE_FLAGS}
        os.environ.update(_LIVE_FLAGS)
        try:
            with self._lab.controls_off(self.off, self.svc):
                yield
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            mt.time = real_time
            if had is None:
                sys.modules.pop("pending_suggestions", None)
            else:
                sys.modules["pending_suggestions"] = had

    def _run(self, coro):
        return self._loop.run_until_complete(coro)

    def advance_clock(self, seconds: float) -> None:
        self._clock += float(seconds)

    # ── rows ──────────────────────────────────────────────────────────────
    def _row(self, rid: str, doc: str, meta: dict) -> "dict[str, Any]":
        return {"id": rid, "text": doc or "", "status": str(meta.get("status") or ""),
                "authority_class": str(meta.get("authority_class") or ""),
                "origin": str(meta.get("origin") or ""),
                "contradicts_id": str(meta.get("contradicts_id") or ""),
                "entity_type": str(meta.get("entity_type") or ""),
                "memory_type": str(meta.get("memory_type") or ""),
                "user_id": str(meta.get("user_id") or meta.get("wing") or ""),
                # provenance and the validity interval (``OPTIONAL_ROW_KEYS``): what the store holds, "" if nothing
                "source_excerpt": str(meta.get("source_excerpt") or ""),
                "user_turn_id": str(meta.get("user_turn_id") or ""),
                "valid_from": meta.get("valid_from") if meta.get("valid_from") is not None else "",
                "invalid_at": meta.get("invalid_at") if meta.get("invalid_at") is not None else "",
                "supersedes_id": str(meta.get("supersedes_id") or ""),
                "superseded_by_id": str(meta.get("superseded_by_id") or "")}

    def _rows(self) -> "list[dict[str, Any]]":
        col = self._lab_service.col
        return [self._row(i, d, m) for i, (d, m) in sorted(col.rows.items())
                if (m.get("user_id") or m.get("wing")) == self._user]

    def stats(self) -> "dict[str, Any]":
        rows = self._rows()
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return {"rows": rows, "counts": counts, "writes_refused": self._refused,
                "row_keys": list(ROW_KEYS)}

    # ── the five calls ────────────────────────────────────────────────────
    def ingest(self, turns: "list[Turn]") -> IngestReport:
        rep = IngestReport()
        with self._ctl():
            for t in turns:
                rep.turns += 1
                self._run(self._one(t, rep))
        self._refused += rep.refused
        return rep

    async def _ingest(self, text: str, source: str, rep: IngestReport, **kw) -> Any:
        ref = await self.service.ingest(text, user_id=self._user, source=source, **kw)
        if ref is None:
            rep.refused += 1
        else:
            rep.written += 1
        return ref

    def _extract(self, t: Turn, prev: str):
        return self.svc.memory_extractor.extract_candidates(
            t.text, t.assistant_text, prev_user_message=prev or None)

    async def _one(self, t: Turn, rep: IngestReport) -> None:
        sp = t.speaker
        if sp == "assistant":
            rep.notes.append("assistant turn: never mined")
            return
        if sp == "owner_taught":
            # the live teach path (expert_dispatch store_fact) stamps a stable turn id and passes NO excerpt
            norm = re.sub(r"\s+", " ", t.text.lower()).strip()
            await self._ingest(t.text, "voice_fact", rep, confidence=0.9,
                               user_turn_id="fact-" + hashlib.sha1(f"{self._user}|{norm}".encode()).hexdigest()[:16])
            return
        if sp in ("owner_typed", "owner_voice_verified", "panel_unverified", "third_party", "pasted_email"):
            source = "chat_regex" if sp in ("owner_typed", "third_party", "pasted_email") else "voice_regex"
            verified = {"owner_voice_verified": True, "panel_unverified": False}.get(sp)
            try:
                from memory_quality import is_storable_fact
            except Exception:  # noqa: BLE001
                is_storable_fact = lambda _t: (True, "")  # noqa: E731
            cands = self._extract(t, self._prev_user)
            self._prev_user = t.text
            for n, c in enumerate(cands):
                ok, _why = is_storable_fact(c.text)
                if not ok:
                    rep.refused += 1
                    continue
                kw: dict[str, Any] = dict(memory_type=c.memory_type, confidence=c.confidence,
                                          source_excerpt=" ".join(t.text.split()),
                                          user_turn_id=f"{_digest(t.text)}-{n}")
                if verified is not None:
                    kw["speaker_verified"] = verified
                await self._ingest(c.text, source, rep, **kw)
            return
        # system_writer: the scripted output of a model pass, under the writer's own label
        writer = t.writer
        for n, fact in enumerate(t.proposes):
            if t.op == "say":
                extra = {"memory_type": t.memory_type} if t.memory_type else {}
                await self._ingest(fact, writer, rep, anchor_text=t.text, confidence=0.7,
                                   user_turn_id=f"w{_digest(writer, fact, self._user)}-{n}", **extra)
                continue
            target = self._target_for(t.attr or fact)
            if target is None:
                rep.notes.append(f"{t.op}: no approved row about {t.attr!r} to act on")
                continue
            if t.op == "edit":
                got = await self.service.review(target, decision="edit", edits=fact, actor=writer,
                                                anchor_text=t.text)
            else:
                got = await self.service.review(target, decision="archive", actor=writer)
            if got is None:
                rep.refused += 1
            else:
                rep.retired += 1

    def _target_for(self, attr_or_fact: str) -> "str | None":
        """The owner's approved row about ``attr`` (kind_of vocabulary: home, work, age, ...)."""
        ma = self.svc.memory_authority
        col = self._lab_service.col
        best: "tuple[float, str] | None" = None
        for r in self._rows():
            if r["status"] == "approved" and ma.kind_of(r["text"]) == attr_or_fact:
                # the OLDEST approved row about the attribute: the owner's original, not a later write
                key = (float(col.rows[r["id"]][1].get("added_ts") or 0.0), r["id"])
                if best is None or key < best:
                    best = key
        return best[1] if best else None

    def run_idle_pass(self, transcript: str, proposes: "list[str]") -> "dict[str, Any]":
        """The REAL nightly digest (``memory_digest.run_memory_digest``: dedup, anchor validation, the
        contradiction pass, ``MemoryService`` writes) over ``transcript``; only the model calls are scripted
        (the fact extraction returns ``proposes``; the contradiction judge says yes, as the incident's did)."""
        md = importlib.import_module("memory_digest")
        stub = types.ModuleType("zoe_agent")

        async def no_blob(*_a, **_k):
            return ""
        stub._mempalace_load_user_facts = no_blob
        stub._invalidate_user_facts_cache = lambda *a, **k: None

        async def todays(*_a, **_k):
            return transcript

        async def extract(_text):
            return [{"fact": f, "type": "profile"} for f in proposes]

        async def contradiction(*_a, **_k):
            return True

        async def no_emotions(*_a, **_k):
            return 0
        saved = {n: getattr(md, n) for n in ("_load_todays_messages", "_extract_facts_with_gemma",
                                              "_is_contradiction", "_emotional_memory_pass")}
        had = sys.modules.get("zoe_agent")
        sys.modules["zoe_agent"] = stub
        md._load_todays_messages, md._extract_facts_with_gemma = todays, extract
        md._is_contradiction, md._emotional_memory_pass = contradiction, no_emotions
        try:
            with self._ctl():
                return self._run(md.run_memory_digest(self._user))
        finally:
            for n, fn in saved.items():
                setattr(md, n, fn)
            if had is None:
                sys.modules.pop("zoe_agent", None)
            else:
                sys.modules["zoe_agent"] = had

    def run_conflict_pass(self) -> "dict[str, Any]":
        """The REAL nightly implicit-conflict pass (``memory_digest._implicit_conflict_pass`` ->
        ``memory_supersede.nightly_conflict_pass``): a newer fact that changes an older one (a change cue on the same
        topic, or a different home) retires it - ``status=superseded``, ``invalid_at`` - and never deletes it. It is a
        no-op while ``ZOE_MEMORY_IMPLICIT_SUPERSEDE`` is off (the ``supersede`` control), and then reports zeros."""
        md = importlib.import_module("memory_digest")
        with self._ctl():
            out = self._run(md._implicit_conflict_pass(self._user))
        return dict(out) if out else {"pairs": 0, "superseded": 0, "enabled": False}

    # ── the people graph (A8): the real writer over an in-memory SQLite ────────────────────────────────────
    _EDGE_DDL = (
        "CREATE TABLE people (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, relationship TEXT, "
        "circle TEXT, context TEXT, notes TEXT, visibility TEXT, deleted INTEGER NOT NULL DEFAULT 0, "
        "is_partial INTEGER NOT NULL DEFAULT 0, last_contacted_at TEXT)",
        "CREATE TABLE person_relationships (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, person_a_id TEXT NOT NULL, "
        "person_b_id TEXT NOT NULL, rel_type TEXT NOT NULL, rel_a_to_b TEXT NOT NULL, rel_b_to_a TEXT NOT NULL, "
        "rel_group TEXT NOT NULL, notes TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
        # migrations 0015 (temporal edges) and 0037 (the writer's authority / origin stamp)
        "ALTER TABLE person_relationships ADD COLUMN valid_from TEXT",
        "ALTER TABLE person_relationships ADD COLUMN valid_to TEXT",
        "ALTER TABLE person_relationships ADD COLUMN superseded_by TEXT",
        "CREATE UNIQUE INDEX person_relationships_pair_active ON person_relationships(user_id, person_a_id, "
        "person_b_id) WHERE valid_to IS NULL",
        "ALTER TABLE person_relationships ADD COLUMN authority TEXT",
        "ALTER TABLE person_relationships ADD COLUMN origin TEXT",
    )

    async def _open_edge_db(self):
        import aiosqlite
        db = await aiosqlite.connect(":memory:")
        db.row_factory = aiosqlite.Row
        for ddl in self._EDGE_DDL:
            await db.execute(ddl)
        await db.commit()
        return db

    async def _write_edge(self, a: str, b: str, rel: str, group: str, authority: str, origin: str) -> None:
        pe = importlib.import_module("person_extractor")
        if self._edge_db is None:
            self._edge_db = await self._open_edge_db()
        db = self._edge_db
        for name in (a, b):  # real, non-partial people (a partial stub is never resolved: it would fork the pair)
            async with db.execute("SELECT 1 FROM people WHERE user_id=? AND name=?", (self._user, name)) as cur:
                if await cur.fetchone() is None:
                    await db.execute("INSERT INTO people (id, user_id, name, deleted, is_partial, visibility) "
                                     "VALUES (?,?,?,0,0,'personal')", (f"p-{_digest(self._user, name)}", self._user, name))
        await db.commit()
        await pe._write_relationship(self._user, a, b, rel, group, db, authority=authority, origin=origin)

    def write_edge(self, a: str, b: str, rel: str, group: str, authority: str, origin: str) -> None:
        """The REAL ``person_extractor._write_relationship`` (temporal edges, the authority wall, the held-back
        candidate through ``MemoryService.record_candidate``) over an in-memory SQLite with the 0007/0015/0037 shapes."""
        with self._ctl():
            self._run(self._write_edge(a, b, rel, group, authority, origin))

    def edges(self) -> "list[dict[str, Any]]":
        async def read():
            if self._edge_db is None:
                return []
            sql = ("SELECT pa.name AS a, pb.name AS b, r.rel_type, r.valid_to, r.authority, r.origin "
                   "FROM person_relationships r JOIN people pa ON pa.id = r.person_a_id "
                   "JOIN people pb ON pb.id = r.person_b_id WHERE r.user_id=? ORDER BY r.created_at, r.id")
            async with self._edge_db.execute(sql, (self._user,)) as cur:
                return [{"a": r["a"], "b": r["b"], "rel_type": r["rel_type"], "current": r["valid_to"] is None,
                         "authority": r["authority"] or "", "origin": r["origin"] or ""} for r in await cur.fetchall()]
        return self._run(read())

    def ingest_as(self, identity: str, turns: "list[Turn]") -> IngestReport:
        """Apply the turns as a household identity, with the member-mode lookup scripted per identity
        (the real ``_affect_allowed`` gate runs; only the Postgres read behind it is replaced)."""
        if identity not in IDENTITIES:
            raise ValueError(f"unknown identity {identity!r} (known: {', '.join(IDENTITIES)})")
        uid = IDENTITIES[identity][0]
        persona = importlib.import_module("persona_layer")
        modes = {u: persona.MemberMode(mode=m, minor=mn) for _l, (u, (m, mn)) in IDENTITIES.items()}

        async def load_member_mode(user_id, db=None):
            return modes.get(user_id, persona.MemberMode())
        real = persona.load_member_mode
        persona.load_member_mode = load_member_mode
        saved_user, rep = self._user, IngestReport()
        self._user = uid
        try:
            with self._ctl():
                for t in turns:
                    rep.turns += 1
                    self._run(self._one(t, rep))
        finally:
            self._user = saved_user
            persona.load_member_mode = real
        return rep

    def stats_as(self, identity: str) -> "dict[str, Any]":
        uid = IDENTITIES[identity][0]
        col = self._lab_service.col
        rows = [self._row(i, d, m) for i, (d, m) in sorted(col.rows.items())
                if (m.get("user_id") or m.get("wing")) == uid]
        return {"rows": rows, "counts": {}, "writes_refused": 0}

    def recall(self, query: str, k: int = 10) -> "list[dict[str, Any]]":
        with self._ctl():
            refs = self._run(self.service.search(query, user_id=self._user, limit=k))
        return [self._row(r.id, r.text, r.metadata) for r in refs]

    def answer(self, query: str, k: int = 5) -> str:
        """The scripted reader over this arm's recall packet (see ``reader_answer``)."""
        rows = self.recall(query, k)
        with self._ctl():  # the control scope is what flips the reader, exactly like every other feature
            return reader_answer(rows, query, sycophantic=self._lab.READER["sycophantic"])

    def forget(self, entity: str) -> str:
        ir = importlib.import_module("intent_router")
        with self._ctl():
            reply = self._run(ir.execute_intent(ir.Intent("memory_forget_entity", {"name": entity}),
                                                self._user))
        return reply or ""

    # ── the disk cells (capability ``disk``) ────────────────────────────────
    def hard_delete(self) -> int:
        """The audited hard delete (``MemoryService.delete_user``) of this cell's user, with the controls
        applied. Returns the rows removed."""
        with self._ctl():
            return int(self._run(self.service.delete_user(self._user, actor="admin", reason="rtbf")) or 0)

    def disk_residue(self, tokens: "list[str]") -> "dict[str, Any]":
        """Byte-scan a COPY of this arm's real palace for each token: ``{"tokens": {token: {"total", "files",
        "sqlite_pages"}}}`` (counts only). The palace is a throwaway directory under the lab's scratch root."""
        import memory_residue  # service module (stdlib only); on the path once the lab is loaded
        return memory_residue.scan_palace(self._lab_service.data_dir, list(tokens), scratch=self._lab.scratch_root())

    def as_of(self, query: str, ts: str) -> "list[dict[str, Any]]":
        """Rows as the store believed them at ``ts``: the REAL ``MemoryService.search(as_of=...)`` - the rows whose
        half-open validity interval ``[valid_from, invalid_at)`` contains the instant, replaced (``superseded``) rows
        included (audit P2.1; ``memory_temporal``)."""
        with self._ctl():
            refs = self._run(self.service.search(query, user_id=self._user, limit=10, as_of=ts))
        return [self._row(r.id, r.text, r.metadata) for r in refs]
