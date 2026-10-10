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

Z0 runs with the flags the live service runs with, switched on around every operation (``people_graph.LIVE_FLAGS``): the code
defaults are OFF, ``services/zoe-data/.env`` turns them ON, and a bench that measured the defaults would be measuring
a system that is not deployed. The ``supersede`` control switches ``ZOE_MEMORY_IMPLICIT_SUPERSEDE`` back off.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import hashlib
import importlib
import os
import importlib.util
import re
import sys
import types
from typing import Any

from .base import Arm, IngestReport, OPTIONAL_ROW_KEYS, ROW_KEYS, Turn
from .people_graph import PeopleGraph, live_context

_LAB_SALT = "zmb-lab-forget-ledger-secret-0123456789"      # the lab ledger secret (synthetic; the same string the Hindsight and ZMA arms use)
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
    capabilities = frozenset({"clock", "controls", "reader", "identities", "idle_pass", "conflict_pass", "edges", "quote_retire",
                              # the capability axes: Z0 DECLARES all four and is MEASURED on them (a known gap is a target, never a skip)
                              "exact_words", "observations", "multi_hop", "protocol"})
    #: ``disk`` = the arm can run a cell over REAL Chroma and byte-scan what is left on disk. Needs chromadb
    #: (not installed in the slim CI lane: the disk cells SKIP there with the reason, never pass).
    if importlib.util.find_spec("chromadb") is not None:
        capabilities = capabilities | {"disk"}

    def __init__(self, off: "frozenset[str] | set[str]" = frozenset(), name: str | None = None, embed: bool = False, *,
                 night: bool = False, night_url: str = "", night_model: str = "", night_ctx: int = 8192, night_chunk_tokens: int = 0,
                 night_max_calls: int = 7, night_decode_tok_s: float = 0.0, night_prefill_tok_s: float = 0.0,
                 night_temperature: "float | None" = None, night_seed: "int | None" = None):
        from .. import lab_driver
        self.embed = embed
        #: Z0n: Z0 + the night mind (``night_mind.py``) as the nightly reflection, with its OWN model (the lab's fake brain, or the clone at ``night_url``):
        #: K2 / K3 stop being scripted-model SKIPs. Plain Z0 keeps the scripted digest (its measured baseline is unchanged).
        self.night = bool(night)
        self.night_url, self.night_model, self.night_ctx = night_url, night_model, int(night_ctx)
        self.night_chunk_tokens = int(night_chunk_tokens or (400 if night_url else 150))
        self.night_max_calls = int(night_max_calls)
        #: the server's MEASURED rates (the window probes and passes them): they size every call's HTTP budget through ``night_mind.Config.timeout_for``, in the
        #: pass AND in K12's labelling. 0 = the env / the module defaults (the live 4B's).
        self.night_decode_tok_s, self.night_prefill_tok_s = float(night_decode_tok_s or 0.0), float(night_prefill_tok_s or 0.0)
        #: sampling of the clone's calls (None = the production temperature / the server's own seed): ``zoe-night-mind.py --cells`` pins both per run so a verdict reproduces
        self.night_temperature, self.night_seed = night_temperature, night_seed
        #: lifetime counters of every model call the arm made (NOT cleared by ``reset``: a cells run resets per cell), so ``--cells`` reports what the model did
        self.night_totals: "dict[str, int]" = {}
        self.nightly_model = "own" if self.night else "scripted"
        self.takes_lies = self.night and not night_url          # the lab's fake brain makes the planted mistakes; a real model makes its own
        self._night_turns: "list[tuple[int, int, str]]" = []       # (seq, day_offset, text): every owner turn, plus night-only routine commands
        self._night_seq = 0
        self.night_ran = False
        self.last_night: "dict[str, Any]" = {}
        if embed:       # Z0e: the same MemoryService over a real Chroma + MiniLM; the disk cells stay Z0's (their embeddings are hash vectors on purpose)
            self.capabilities = frozenset((set(self.capabilities) - {"disk"}) | {"embedder"})
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
        self._cur_day = 0
        self.graph = PeopleGraph(self._run)       # the people graph (A8): arms.people_graph, shared with the Hindsight arms' Zoe layer
        self._heap_scrub_ours = False
        self._salt_ours = False

    # ── lifecycle ─────────────────────────────────────────────────────────
    def reset(self, user_id: str, *, disk: bool = False) -> None:
        if not DEMO_USER_RE.match(user_id or ""):
            raise ValueError(f"refusing non-demo identity {user_id!r} (must match {DEMO_USER_RE.pattern})")
        if self._lab_service is not None:
            self._lab_service.close()
        self.graph.close()
        self._lab_service = self._lab.LabService(self.svc, tag=self.name, disk=disk, embed=self.embed and not disk)
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
        self._night_turns, self._night_seq, self.night_ran, self.last_night = [], 0, False, {}
        ns = importlib.import_module("night_store")                # the night mind's tables: the lab's own in-process store (the SQL one is Postgres)
        ns.set_backend(ns.MemoryBackend())
        importlib.import_module("night_mind")._reset_state()
        self.svc.memory_tombstones.clear_all(user_id)
        xw = importlib.import_module("exact_words")        # the owner's verbatim turns (j): the lab's own in-process index, as the tests' (the SQL one is Postgres)
        xw.set_backend(xw.MemoryBackend())
        mf = importlib.import_module("memory_forgotten")   # the durable forget ledger (F3): ON, in memory, with a LAB secret. The 2026-10-08 baseline ran with the
        if not mf.configured():                            # secret unset, so only the 300 s tombstone shielded and F3 failed 3 of 3 seeds - an instrument gap, not a defect
            os.environ[mf.SALT_ENV] = _LAB_SALT
            self._salt_ours = True
        mf.set_backend(mf.MemoryBackend())

    def close(self) -> None:
        mf = importlib.import_module("memory_forgotten")
        mf.set_backend(None)
        if self._salt_ours:
            os.environ.pop(mf.SALT_ENV, None)
            self._salt_ours = False
        importlib.import_module("exact_words").set_backend(None)
        importlib.import_module("night_store").set_backend(None)
        self.graph.close()
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
        try:
            with live_context(), self._lab.controls_off(self.off, self.svc):
                yield
        finally:
            mt.time = real_time

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
                "superseded_by_id": str(meta.get("superseded_by_id") or ""),
                "retire_quote": str(meta.get("retire_quote") or ""),
                "retired_by": str(meta.get("retired_by") or ""),
                "quote_elsewhere": str(meta.get("quote_elsewhere") or "")}

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
                if self.night and t.speaker in ("owner_typed", "owner_voice_verified"):
                    self._night_seq += 1
                    self._night_turns.append((self._night_seq, int(t.day_offset or 0), t.text))
                self._run(self._one(t, rep))
        self._refused += rep.refused
        return rep

    async def _ingest(self, text: str, source: str, rep: IngestReport, **kw) -> Any:
        if self._cur_day and "captured_at" not in kw:      # a turn said N days ago is captured N days ago (the service's own restore parameter)
            kw["captured_at"] = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=self._cur_day)).isoformat()
        ref = await self.service.ingest(text, user_id=self._user, source=source, **kw)
        if ref is None:
            rep.refused += 1
        else:
            rep.written += 1
        return ref

    #: False when another store holds the owner's verbatim words (ZMA: MemPalace is the verbatim tier): Z0 extracts, it does not keep a second copy
    index_exact = True

    def exact_index_copies(self, needle: str) -> int:
        """How many entries of Z0's own exact-words index hold ``needle`` (the integration cell counts a second verbatim copy here)."""
        xw = importlib.import_module("exact_words")
        return len(self._run(xw.get_backend().rows_matching(self._user, needle)))

    async def _index_exact(self, t: Turn, source: str, verified: "bool | None") -> None:
        """The post-turn hook of ``memory_extractor.extract_and_ingest``: the owner's verbatim words into the exact-words index, said ``day_offset`` days ago."""
        if not self.index_exact:
            return
        xw = importlib.import_module("exact_words")
        when = _dt.datetime.now(_dt.timezone.utc).timestamp() - float(self._cur_day or 0) * 86400.0
        await xw.index_turn(self._user, t.text, said_at=when, source=source, speaker_verified=verified)

    def _extract(self, t: Turn, prev: str):
        return self.svc.memory_extractor.extract_candidates(
            t.text, t.assistant_text, prev_user_message=prev or None)

    async def _one(self, t: Turn, rep: IngestReport) -> None:
        self._cur_day = int(t.day_offset or 0)
        sp = t.speaker
        if sp == "assistant":
            rep.notes.append("assistant turn: never mined")
            return
        if sp == "owner_taught":
            await self._index_exact(t, "voice_fact", None)
            # the live teach path (expert_dispatch store_fact) stamps a stable turn id and passes NO excerpt
            norm = re.sub(r"\s+", " ", t.text.lower()).strip()
            await self._ingest(t.text, "voice_fact", rep, confidence=0.9,
                               user_turn_id="fact-" + hashlib.sha1(f"{self._user}|{norm}".encode()).hexdigest()[:16])
            return
        if sp in ("owner_typed", "owner_voice_verified", "panel_unverified", "third_party", "pasted_email"):
            source = "chat_regex" if sp in ("owner_typed", "third_party", "pasted_email") else "voice_regex"
            verified = {"owner_voice_verified": True, "panel_unverified": False}.get(sp)
            await self._index_exact(t, source, verified)
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

    def run_idle_pass(self, transcript: str, proposes: "list[str]", *, judge: bool = True) -> "dict[str, Any]":
        """The REAL nightly digest (``memory_digest.run_memory_digest``: dedup, anchor validation, the
        contradiction pass, ``MemoryService`` writes) over ``transcript``; only the model calls are scripted
        (the fact extraction returns ``proposes``; the contradiction judge says yes, as the incident's did -
        ``judge=False`` scripts it to say "no contradiction": the reflection cells (k) are about what the digest
        KEEPS of a night's proposals, not about it retiring one proposal with the next)."""
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
            return bool(judge)

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

    # ── quote-backed retirement (S10x): the real prefilter, candidates and wall; only the brain's CHOICE is scripted ────
    #: lane + speaker verdict per bench speaker: a typed chat turn (the authenticated owner), a spoken turn the speaker gate
    #: confirmed / refused / said nothing about
    RETIRE_SPEAKERS = {"owner_typed": ("chat", None), "owner_voice_verified": ("voice", True),
                       "panel_unverified": ("voice", False), "voice_no_verdict": ("voice", None)}

    def quote_retire(self, text: str, *, lane: str = "chat", speaker_verified: "bool | None" = None,
                     brain: "dict[str, Any] | None" = None, mode: str = "enforce") -> "dict[str, Any]":
        """One candidate state change through ``memory_retire`` (``prepare`` -> the scripted brain's choice -> ``decide``), in the
        live lane's shape. ``brain``: ``{"pick_text": exact row text}`` (an honest judge naming the row, or none when it was not
        offered), ``{"pick": n}``, ``{"top1": true}`` (a hostile judge: always the first row offered), ``{"judge": async callable}`` (a real model's choice), ``{"row_containing": text}``
        (a hostile judge naming any stored approved row by id). The ``retire_judge`` control replaces every choice by the retrieval's
        top-1. Counts and ids only."""
        with self._ctl():
            return self._run(self._quote_retire(text, lane, speaker_verified, dict(brain or {}), mode))

    def cue_gate(self, text: str) -> bool:
        """The REAL prefilter (``memory_retire.pick_quote``), under the arm's controls."""
        with self._ctl():
            return importlib.import_module("memory_retire").pick_quote(text) is not None

    async def _quote_retire(self, text, lane, speaker_verified, brain, mode) -> "dict[str, Any]":
        mr = importlib.import_module("memory_retire")
        prep = await mr.prepare(self.service, self._user, text, lane=lane, speaker_verified=speaker_verified,
                                mode_override=mode)
        if prep.decision is not None:
            d = prep.decision
            return {"action": d.action, "reason": d.reason, "offered": [], "chosen": ""}
        offered = [r.id for r in prep.candidates]
        pick: "int | None" = None
        row_id: "str | None" = None
        if self._lab.RETIRE["naive"]:
            pick = 1                                            # the naive rule: no judgement, the top-1
        elif "judge" in brain:                                  # a REAL judge: an async callable (quote, rows) -> pick | None (S10x live tier)
            pick = await brain["judge"](prep.quote, prep.candidates)
            pick = 0 if pick is None else pick
        elif "pick_text" in brain:
            pick = next((i for i, r in enumerate(prep.candidates, 1) if r.text == brain["pick_text"]), 0)
        elif "pick" in brain:
            pick = int(brain["pick"])
        elif brain.get("top1"):
            pick = 1
        elif "row_containing" in brain:
            want = str(brain["row_containing"])
            row_id = next((r["id"] for r in self._rows() if want in r["text"] and r["status"] == "approved"), "")
        d = await mr.decide(self.service, self._user, prep, pick=pick, row_id=row_id, mode_override=mode)
        return {"action": d.action, "reason": d.reason, "offered": offered, "chosen": d.row_id}

    # ── the people graph (A8): the real writer over an in-memory SQLite (arms.people_graph) ────────────────
    def write_edge(self, a: str, b: str, rel: str, group: str, authority: str, origin: str) -> None:
        """The REAL ``person_extractor._write_relationship`` (temporal edges, the authority wall, the held-back
        candidate through ``MemoryService.record_candidate``) over an in-memory SQLite with the 0007/0015/0037 shapes."""
        with self._ctl():
            self.graph.write(self._user, a, b, rel, group, authority, origin)

    def edges(self) -> "list[dict[str, Any]]":
        return self.graph.edges(self._user)

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

    # ── the capability axes ───────────────────────────────────────────────────
    def recall_exact(self, query: str, k: int = 5) -> "list[dict[str, Any]]":
        """(j) What Z0's recall packet holds for "what exactly did I say about ...": the owner's own VERBATIM turns that best match
        (``exact_words.lookup``: the index the for-prompt packet's "Your own words" block reads, each with the day it was said), then the
        ordinary top rows' TEXT (the extracted fact, as the brain sees it) and the day each was captured. Before the index (2026-10-07) only
        the rows were there and the whole turn survived only as ``source_excerpt`` on the rows the extractor happened to extract: J1 / J2 0 of 20."""
        col = self._lab_service.col
        now = _dt.datetime.now(_dt.timezone.utc).timestamp()
        out = []
        xw = importlib.import_module("exact_words")
        if xw.wants(query):          # the for-prompt packet's exact-words block: the owner's own verbatim turns, each with the day it was said
            with self._ctl():
                hits = self._run(xw.lookup(self._user, query, k=k))
            out += [{"text": h.text, "day_offset": round((now - h.said_at) / 86400.0)} for h in hits]
        for r in self.recall(query, max(k - len(out), 1)):
            ts = (col.rows.get(r["id"]) or ("", {}))[1].get("added_ts")
            out.append({"text": r["text"], "day_offset": round((now - float(ts)) / 86400.0) if ts else None})
        return out

    # ── the night mind (Z0n) ──────────────────────────────────────────────────────────────────────────────────────
    def add_night_turns(self, texts: "list[str]", day_offset: int) -> None:
        """Owner turns that reach ONLY the night pass (routine commands: they are in ``chat_messages`` but no memory extractor mines them)."""
        self._need_night()
        for text in texts:
            self._night_seq += 1
            self._night_turns.append((self._night_seq, int(day_offset), text))

    def _night_transcript(self, now: "_dt.datetime"):
        """The lab's ``chat_messages`` for the night pass: every owner turn in time order with an id and the time it was said (``day_offset`` days ago)."""
        md = importlib.import_module("memory_digest")
        rows = sorted(self._night_turns, key=lambda r: (-r[1], r[0]))
        turns = [(f"lab-{seq:05d}", text) for seq, _d, text in rows]
        times = [(now - _dt.timedelta(days=d) + _dt.timedelta(seconds=seq % 3600)).isoformat() for seq, d, _t in rows]
        return md.Transcript("\n".join(t for _i, t in turns), turns, times)

    def turn_text(self, turn_id: str) -> "str | None":
        """The text of one of this store's owner turns by id (the citation-validity cell K8 checks every observation's pointer here)."""
        self._need_night()
        for seq, _d, text in self._night_turns:
            if turn_id == f"lab-{seq:05d}":
                return text
        return None

    def reflect_pass(self, propose: "list[str] | None" = None, seed: str = "zmb-v1") -> "dict[str, Any]":
        """The night: the REAL nightly digest (``memory_digest.run_memory_digest``) over the lab's owner turns, with fact extraction stubbed out and the night
        mind switched on - its ``night_mind.run_for_user`` hook does the reflection. The model is the lab's fake brain (``propose`` = the mistakes it
        makes: the planted fabricated links, stale values, hedges and 'you told me's), or the clone at ``night_url`` with nothing planted."""
        if not self.night:
            raise NotImplementedError(f"arm {self.name} has no own-model reflection pass (use Z0n)")
        from .. import life as lifemod
        from ..night_brain import FakeNightBrain
        md, nm = importlib.import_module("memory_digest"), importlib.import_module("night_mind")
        now = _dt.datetime.now(_dt.timezone.utc)
        transcript = self._night_transcript(now)
        stub = types.ModuleType("zoe_agent")

        async def no_blob(*_a, **_k):
            return ""
        stub._mempalace_load_user_facts = no_blob
        stub._invalidate_user_facts_cache = lambda *a, **k: None

        async def todays(*_a, **_k):
            return transcript

        async def no_facts(_text):
            return []

        async def no_emotions(*_a, **_k):
            return 0
        saved = {n: getattr(md, n) for n in ("_load_todays_messages", "_extract_facts_with_gemma", "_emotional_memory_pass")}
        had = sys.modules.get("zoe_agent")
        sys.modules["zoe_agent"] = stub
        md._load_todays_messages, md._extract_facts_with_gemma, md._emotional_memory_pass = todays, no_facts, no_emotions
        cfg = self._night_cfg(chunk_tokens=self.night_chunk_tokens, max_calls=self.night_max_calls)
        persona = importlib.import_module("persona_layer")             # the real affect gate runs; only the Postgres read behind it is scripted: an adult member
        real_mode = persona.load_member_mode

        async def adult_member(user_id, db=None):
            return persona.MemberMode(mode="companion", minor=False)
        persona.load_member_mode = adult_member
        prev_llm = nm.set_llm(None if self.night_url else FakeNightBrain(lies=tuple(propose or ()), life=lifemod.life(seed)))
        prev_cfg = nm.set_config(cfg)
        prev_env = os.environ.get("ZOE_NIGHT_MIND")
        os.environ["ZOE_NIGHT_MIND"] = "enforce"                   # the arm turns the pass on; the ``night_mind`` control (applied inside ``_ctl``) turns it off again
        try:
            with self._ctl():
                out = self._run(md.run_memory_digest(self._user))
        finally:
            for n, fn in saved.items():
                setattr(md, n, fn)
            if had is None:
                sys.modules.pop("zoe_agent", None)
            else:
                sys.modules["zoe_agent"] = had
            nm.set_llm(prev_llm)
            nm.set_config(prev_cfg)
            persona.load_member_mode = real_mode
            if prev_env is None:
                os.environ.pop("ZOE_NIGHT_MIND", None)
            else:
                os.environ["ZOE_NIGHT_MIND"] = prev_env
        night = dict(out.get("night_mind") or {})
        self.last_night = night
        self._tally(night)
        self.night_ran = True
        flat = {k: v for k, v in night.items() if isinstance(v, (int, float, bool))}
        # the digest ran for its own reasons too; a pass that did not RUN proves nothing (``cells.run_cell`` raises on skipped_reason / error)
        rep = {**{k: v for k, v in out.items() if k != "night_mind"}, **flat}
        if out.get("skipped_reason"):
            rep["skipped_reason"] = out["skipped_reason"]
        if night.get("status") in ("error", "llm_unreachable"):
            rep["error"] = f"night_mind:{'llm_timeout' if night.get('llm_timeout') else night.get('status')}:{night.get('error', '')}"
        elif night.get("status") == "skipped":
            rep["skipped_reason"] = f"night_mind:{night.get('skipped_reason')}"
        elif night.get("status") == "off" and "night_mind" not in self.off:
            rep["error"] = "night_mind:off"
        return rep

    def _night_cfg(self, **kw: Any):
        """The night mind's Config for this arm: the clone's URL / model / context and the measured rates the window passed (one place, so no code path can
        build a config that forgets them)."""
        nm = importlib.import_module("night_mind")
        return nm.config_from_env(url=self.night_url or "http://127.0.0.1:1", model=self.night_model, ctx_tokens=self.night_ctx,
                                  decode_tok_s=self.night_decode_tok_s or None, prefill_tok_s=self.night_prefill_tok_s or None,
                                  temperature=self.night_temperature, seed=self.night_seed, **kw)

    def _tally(self, counters: "dict[str, Any]") -> None:
        for k in ("calls", "moments_calls", "threads_calls", "calls_invalid", "prompt_tokens", "completion_tokens", "observations_written", "moments_verified"):
            v = counters.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                self.night_totals[k] = self.night_totals.get(k, 0) + int(v)

    def _night_snapshot(self, all_threads: bool = False):
        nm = importlib.import_module("night_mind")
        return self._run(nm.snapshot(self._user, all_threads=all_threads))

    def _need_night(self) -> None:
        if not self.night:
            raise NotImplementedError(f"arm {self.name}: the nightly model is scripted in this lab, so there is no night pass to read (use Z0n)")

    def threads(self) -> "list[dict[str, Any]]":
        """(k) Every night thread (title, status, policy, source_ref, anchors): the structure the cells K9 / K10 / K11 read."""
        self._need_night()
        return [dict(t) for t in self._run(importlib.import_module("night_store").get_backend().threads(self._user))]

    def changes(self) -> "list[dict[str, Any]]":
        """(k) What changed in the last pass (new / advanced / resolved / quiet, with the cited turn ids)."""
        self._need_night()
        return list(self.last_night.get("changes") or [])

    def morning_plan(self, days: int = 14) -> "list[dict[str, Any]]":
        """(k) ``night_mind.plan_mornings``: ``days`` simulated mornings against the stored threads, every raise ignored (K10)."""
        self._need_night()
        nm = importlib.import_module("night_mind")
        threads = self.threads()
        obs = self._run(importlib.import_module("night_store").get_backend().observations(self._user))
        quotes: "dict[str, list[str]]" = {}
        for o in obs:
            quotes.setdefault(o["thread_id"], []).append(o["quote"])
        with self._ctl():
            plan = nm.plan_mornings(threads, quotes, _dt.date.today(), days)
        return plan

    def moment_labels(self, texts: "list[str]") -> "list[dict[str, Any]]":
        """(k) Stage 2 alone over labelled turns (K12): the kind / feeling / weight the model gave each turn it picked, by quote."""
        self._need_night()
        nm = importlib.import_module("night_mind")
        from ..night_brain import FakeNightBrain
        now = _dt.datetime.now(_dt.timezone.utc)
        turns = [nm.Turn(f"lbl-{i}", t, now) for i, t in enumerate(texts)]
        cfg = self._night_cfg()
        prev = nm.set_llm(None if self.night_url else FakeNightBrain())
        counts = {k: 0 for k in nm.COUNT_KEYS}
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        out: "list[dict[str, Any]]" = []
        calls = 0
        try:
            with self._ctl():
                for n, chunk in enumerate(nm.chunk_turns(turns, nm.Config(ctx_tokens=cfg.ctx_tokens, chunk_tokens=self.night_chunk_tokens).chunk_budget)):
                    # K12 measures the CALIBRATION of the labels, not the pass's selectivity (K6): every labelled line is a moment worth labelling, so the cap is the
                    # number of lines (with the production cap of 8, twelve labelled lines could never reach the cell's 75 % matched bar: 8 / 12 = 67 %)
                    cap = max(nm.MAX_MOMENTS_PER_CHUNK, len(chunk))
                    prompt = nm.MOMENTS_USER.format(lines="\n".join(nm._line(f"m{i}", t) for i, t in enumerate(chunk, 1)), cap=cap)
                    calls += 1
                    raw = self._run(nm._complete([{"role": "system", "content": nm.MOMENTS_SYSTEM}, {"role": "user", "content": prompt}], nm.moment_max_tokens(cap), cfg, usage))
                    for m in nm.parse_moments(raw, chunk, n, counts, cap) or []:
                        out.append({"quote": m.quote, "kind": m.kind, "feeling": m.feeling, "weight": m.weight})
        finally:
            nm.set_llm(prev)
            self._tally({"calls": calls, "moments_calls": calls, **usage})
        return out

    def observations(self, query: str = "") -> "dict[str, Any]":
        """(k) Z0's derived statements: the approved rows a MODEL wrote (the nightly digest and the per-turn digest) - not what the owner said and not
        what the wall held back (a ``disputed`` / ``pending`` / ``superseded`` row is not a belief the packet serves). ``stated_by`` is the
        owner's own three-way view of the class (``memory_authority.authority_of``). The lab scripts the nightly model: ``model`` = ``scripted``."""
        if self.night:
            return self._night_observations(query)
        ma = self.svc.memory_authority
        derived = [r for r in self._rows() if r["status"] == "approved"
                   and r["authority_class"] in (ma.USER_STATED_DERIVED, ma.MODEL_FROM_TURN, ma.MODEL_FROM_TRANSCRIPT)]
        items = [{"id": r["id"], "text": r["text"], "stated_by": "user" if ma.authority_of(r["authority_class"]) in (ma.USER_STATED, ma.USER_CONFIRMED) else "inferred"}
                 for r in derived]
        if query:
            q = content_tokens(query)
            items = sorted(items, key=lambda i: (-len(q & content_tokens(i["text"])), i["id"]))[:5]
            items = [i for i in items if q & content_tokens(i["text"])]
        return {"items": items, "model": "scripted"}

    def _night_observations(self, query: str) -> "dict[str, Any]":
        """The night mind's CURRENT observations (the owner's quotes, each a pointer to its turn). No query: the newest two per thread (what the card holds);
        a query: ``night_mind.lookup`` - the very ranking the recall packet's block uses - at most five. ``model: own`` (the pass's model, not a script)."""
        nm = importlib.import_module("night_mind")
        with self._ctl():                                        # the control scope: the bench's copying fault lifts the serving filters too
            threads, obs = self._night_snapshot(all_threads=bool(query))      # a message that names a thread is answered from every thread; the card (no query) holds the stories only
            return self._night_export(nm, threads, obs, query)

    def _night_export(self, nm: Any, threads: "list[dict]", obs: "list[dict]", query: str) -> "dict[str, Any]":
        today = _dt.date.today()
        if query:
            rows = nm.lookup(threads, obs, query, today, limit=5, per_thread=3)
        else:
            by: "dict[str, list[dict]]" = {}
            for o in obs:
                by.setdefault(o["thread_id"], []).append(o)
            tmap = {t["id"]: t for t in threads}
            rows = []
            for tid, items in by.items():
                for o in sorted(items, key=lambda r: (r["said_at"], r["id"]), reverse=True)[:(None if "echo" in nm.FAULTS else 2)]:
                    rows.append({**o, "thread": tmap.get(tid, {})})
        items = [{"id": o["id"], "text": o["quote"], "stated_by": "user", "thread": o["thread_id"], "turn_id": o["turn_id"], "day": o["day"],
                  "kind": o["kind"], "weight": o["weight"], "feeling": o["feeling"]} for o in rows]
        return {"items": items, "model": "own"}

    def recall_linked(self, query: str, k: int = 8) -> "list[dict[str, Any]]":
        """(l) Z0's packet for a question that needs two facts: its ordinary recall. The relational block (people / dates from Postgres, behind
        ``ZOE_MEMORY_COMPOSE_ENABLED``) is not in the lab, and it holds the people graph, not these facts. The second hop is
        ``multi_hop_recall`` (subjects of a comparison searched one by one; a bridge through the entity the first fact names), the
        same function the for-prompt packet calls."""
        mh = importlib.import_module("multi_hop_recall")

        async def search(q: str, limit: int = k):
            return await self.service.search(q, user_id=self._user, limit=limit)
        with self._ctl():
            refs = self._run(mh.expand(search, query, self._run(search(query, k)), limit=k))
        return [self._row(r.id, r.text, r.metadata) for r in refs]

    def protocol_answer(self, prompt: str, anchor: "tuple[str, ...]", fired: bool, k: int = 5) -> str:
        """(m, lab half) The scripted reader over Z0's packet when recall fired; nothing to answer from when it did not."""
        from .. import life as lifemod
        if not fired:
            return lifemod.DECLINE
        rows = self.recall(prompt, k)
        with self._ctl():                       # the control scope flips the reader exactly as it does for ``answer``
            return lifemod.anchored_reader(rows, anchor, sycophantic=self._lab.READER["sycophantic"])

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
