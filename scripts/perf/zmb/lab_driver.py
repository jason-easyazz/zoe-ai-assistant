"""The in-process LAB driver: the real ``MemoryService`` over an in-memory collection.

No network, no brain, no Postgres, no Chroma, no live store. It is the same idea as the memory
tests' ``_InMemoryCollection`` / ``_Col`` (``services/zoe-data/tests/test_memory_opt_out_endpoints.py``,
``test_memory_authority.py``): the REAL ingest / review / forget / supersede logic runs against a dict,
so a wall that is broken shows up as a broken wall. This module adds what a benchmark needs on top:

* ``LabCollection`` - a Chroma-shaped dict store that honours ``where`` (``$or`` / ``$and`` / ``$eq`` /
  ``$ne`` / ``$in`` / ``$nin`` / ``$gte``) and answers ``query`` with a deterministic bag-of-words distance
  (a stand-in for the embedder: store logic is measured here, retrieval QUALITY is not);
* ``Controls`` - the negative controls. Each is a named feature the benchmark claims protects something;
  switching it OFF in-process (no restart, nothing global left behind) must turn every cell that depends on
  it red. A control that leaves its cell green means the cell does not measure the feature, and the runner
  refuses to report the run ("instrument not instrumented").
* ``scratch pin`` - the process-wide stores that default to the OPERATOR's real files (the palace, the
  reject ledger, the STT log) are pointed at a throwaway directory BEFORE any service module is imported,
  and a lab never opens a live palace path (``live_store_guard`` is the backstop, ``ZOE_HARNESS=1``).
"""
from __future__ import annotations

import atexit
import contextlib
import importlib
import itertools
import math
import os
import re
import shutil
import sys
import tempfile
import types
from pathlib import Path
from typing import Any, Iterator

REPO = Path(__file__).resolve().parents[3]
SERVICE_DIR = REPO / "services" / "zoe-data"

#: the features the benchmark can switch off (``--control``), what each one is
CONTROLS = {
    "authority": "ZOE_MEMORY_AUTHORITY=off - a model write may supersede / archive what the user said",
    "identity": "the automatic-writer name wall removed - a digest may assert the owner's name",
    "tombstone": "the forget tombstone removed - a late extractor write may resurrect a forgotten name",
    "sweep": "the forget sweep archives nothing while still claiming it did",
    "affect": "ZOE_AFFECT_CONSENT_GATE=off - feelings are recorded for guests and children",
    "speaker": "the speaker-gate verdict dropped on the voice lane - an unconfirmed panel voice's self-fact is written as the owner's own statement",
    "extractor": "a lazy extractor that flips day/month, guesses roles, mines questions and assistant text",
    "gate": "the write-quality gate removed - questions, meta-rambling and transcript echoes are stored",
    "reader": "a reader that always answers from the nearest row instead of declining",
    "supersede": "ZOE_MEMORY_IMPLICIT_SUPERSEDE=off - the nightly implicit-conflict pass retires nothing (a changed fact never replaces the old one)",
    "invalidate": "a superseded row is DELETED instead of invalidated (no history is kept)",
    "retrieval": "search ignores the query and returns the newest rows (the ranking / owner filter is broken)",
    "provenance": "the write boundary drops source_excerpt and user_turn_id (a row no longer says which turn it came from)",
    "entailment": "the verbatim-anchor rule removed (wall ON): a per-turn model reading of the owner's own change of mind cannot retire the owner's row, it waits as a disputed candidate",
    "topic": "the same-topic guard removed: a change retires every older fact, about anyone",
    "event_time": "the stated-validity parser switched off: valid_from is always the capture time, never the date the person said",
    "history": "the history read switched off: a replaced fact is kept but a question about how things used to be never sees it",
    "physical_erase": "ZOE_MEMORY_PHYSICAL_ERASE=0 - a hard delete / forget removes the row through the API and leaves the text on disk",
    "retire_cue": "the quote-retire prefilter removed: any sentence opens the door to the judge, a mention, a question or a plan included (S10x)",
    "retire_judge": "no judge: the retirement takes the retrieval's top-1 row whatever the sentence says (the naive rule; wrong on 34 of 40 non-changes)",
    "retire_speaker": "the verified-owner check removed: a voice turn the speaker gate did not confirm may retire the owner's row",
    "retire_ownwords": "the own-words wall removed from quote-retire: pasted text and a third person's quoted speech are read as the owner's sentence",
    "retire_offered": "the offered-rows check removed: the judge may name ANY row, not one of the three it was shown",
    "retire_owner_row": "the owner's-own-row check removed: another person's copy of a fact is offered and retired",
    "retire_shadow": "ZOE_QUOTE_RETIRE=shadow applies the retirement: the decision is logged AND written",
    "retire_quote": "the verbatim sentence is not kept with the retirement: a row is retired with no evidence of why",
    "retire_forget": "the verbatim sentence is kept where the forget sweep never looks: a forgotten name stays in the cited quote",
}

_DISK_SEQ = itertools.count(1)

_PIN_ENV = {
    "MEMPALACE_DATA_DIR": "mempalace",
    "ZOE_VOICE_STT_LOG": "voice_stt.jsonl",
    "ZOE_MEMORY_REJECT_LEDGER": "memory-reject-ledger.json",
}
_scratch_root: str | None = None


class LabRefusal(RuntimeError):
    """The lab was asked to do something that could reach a live store."""


def scratch_root() -> str:
    global _scratch_root
    if _scratch_root is None:
        _scratch_root = tempfile.mkdtemp(prefix="zmb-lab-")
        atexit.register(shutil.rmtree, _scratch_root, ignore_errors=True)  # no /tmp litter per run
    return _scratch_root


def pin_scratch_stores() -> None:
    """Point every per-user live store at a throwaway directory. Unconditional, and BEFORE the
    first import of a service module (the paths are read at import). When the service modules are
    already loaded (a pytest session under ``services/zoe-data/tests`` pinned them), verify instead."""
    if "memory_service" in sys.modules:
        live = sys.modules.get("live_store_guard")
        data = getattr(sys.modules["memory_service"], "_MEMPALACE_DATA", "")
        if live is not None and data and live.is_live_palace(str(data)):
            raise LabRefusal(f"memory_service was imported against the LIVE palace ({data}); the lab "
                             "must be started before it, or from a pinned (conftest) session")
        return
    root = scratch_root()
    for env, leaf in _PIN_ENV.items():
        os.environ[env] = os.path.join(root, leaf)
    os.environ.setdefault("ZOE_HARNESS", "1")  # live_store_guard: a declared harness, synthetic ids only


def _service_path() -> None:
    # FIRST on the path, not merely on it: a maintenance script loaded earlier in the same process
    # (zoe-nightly-dreaming.py) puts the LIVE checkout's services/zoe-data at sys.path[0], and a lab that
    # imported the service modules from there would measure the wrong tree (this checkout's own_words wall
    # would be missing from the arm that is supposed to carry it)
    p = str(SERVICE_DIR)
    if sys.path[:1] != [p]:
        if p in sys.path:
            sys.path.remove(p)
        sys.path.insert(0, p)


def load_service() -> types.SimpleNamespace:
    """Import the service modules the lab drives (after the scratch pin) and return them."""
    pin_scratch_stores()
    _service_path()
    mods = {n: importlib.import_module(n) for n in (
        "memory_service", "memory_authority", "memory_tombstones", "memory_extractor",
        "memory_quality", "identity_facts", "live_store_guard", "memory_temporal", "memory_retire")}
    return types.SimpleNamespace(**mods)


# ── the in-memory collection ─────────────────────────────────────────────────

def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9']+", (text or "").lower()) if len(t) > 1}


def _cmp(value: Any, cond: Any) -> bool:
    if not isinstance(cond, dict):
        return value == cond
    for op, want in cond.items():
        if op == "$eq" and not value == want:
            return False
        if op == "$ne" and not value != want:
            return False
        if op == "$in" and value not in want:
            return False
        if op == "$nin" and value in want:
            return False
        if op in ("$gt", "$gte", "$lt", "$lte"):
            try:
                ok = {"$gt": value > want, "$gte": value >= want, "$lt": value < want,
                      "$lte": value <= want}[op]
            except TypeError:
                ok = False
            if not ok:
                return False
    return True


def matches(meta: dict, where: dict | None) -> bool:
    if not where:
        return True
    for key, want in where.items():
        if key == "$and":
            if not all(matches(meta, w) for w in want):
                return False
        elif key == "$or":
            if not any(matches(meta, w) for w in want):
                return False
        elif not _cmp(meta.get(key), want):
            return False
    return True


class LabCollection:
    """A Chroma-shaped dict store (see the module docstring). Deterministic: ties sort by id."""

    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, dict]] = {}

    def upsert(self, *, ids, documents=None, metadatas=None, **_kw):
        documents = documents or [""] * len(ids)
        metadatas = metadatas or [{}] * len(ids)
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[i] = (d, dict(m))

    add = upsert

    def update(self, *, ids, metadatas=None, documents=None, **_kw):
        for n, i in enumerate(ids):
            if i not in self.rows:
                continue
            doc, meta = self.rows[i]
            if documents is not None:
                doc = documents[n]
            if metadatas is not None:
                meta = dict(metadatas[n])
            self.rows[i] = (doc, meta)

    def delete(self, *, ids=None, where=None, **_kw):
        for i in list(ids or [k for k, (_d, m) in self.rows.items() if matches(m, where)]):
            self.rows.pop(i, None)

    def get(self, *, ids=None, where=None, include=None, limit=None, offset=0, **_kw):
        keys = [i for i in ids if i in self.rows] if ids is not None else sorted(self.rows)
        keys = [k for k in keys if matches(self.rows[k][1], where)]
        if offset:
            keys = keys[offset:]
        if limit is not None:
            keys = keys[:limit]
        return {"ids": keys, "documents": [self.rows[i][0] for i in keys],
                "metadatas": [dict(self.rows[i][1]) for i in keys]}

    def count(self) -> int:
        return len(self.rows)

    def query(self, *, query_texts, n_results=10, where=None, include=None, **_kw):
        out_ids, out_docs, out_metas, out_dist = [], [], [], []
        for q in query_texts:
            qt = _tokens(q)
            scored = []
            for i, (d, m) in self.rows.items():
                if not matches(m, where):
                    continue
                dt = _tokens(d)
                overlap = len(qt & dt) / math.sqrt(max(len(qt), 1) * max(len(dt), 1))
                scored.append((1.0 - overlap, i))
            scored.sort()
            top = scored[:max(n_results, 0)]
            out_ids.append([i for _s, i in top])
            out_docs.append([self.rows[i][0] for _s, i in top])
            out_metas.append([dict(self.rows[i][1]) for _s, i in top])
            out_dist.append([s for s, _i in top])
        return {"ids": out_ids, "documents": out_docs, "metadatas": out_metas, "distances": out_dist}


# ── a REAL Chroma collection (the disk cells) ────────────────────────────────

def _hash_vec(text: str) -> list[float]:
    """A deterministic 384-d stand-in embedding (the disk cells measure what is left ON DISK, not retrieval)."""
    import hashlib
    h = hashlib.sha256((text or "").encode("utf-8")).digest()
    return [((h[i % 32] + i) % 97) / 97.0 + 0.01 for i in range(384)]


def chroma_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("chromadb") is not None


class DiskCollection:
    """The service's drawers collection over REAL Chroma in a throwaway directory, same call surface as
    ``LabCollection`` (``rows`` included). Embeddings are hash vectors: retrieval QUALITY is not measured here,
    the bytes Chroma leaves behind are."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    @property
    def rows(self) -> "dict[str, tuple[str, dict]]":
        got = self._raw.get(include=["documents", "metadatas"])
        return {i: (d or "", dict(m or {})) for i, d, m in zip(got["ids"], got["documents"], got["metadatas"])}

    def _embed(self, kw: dict) -> dict:
        docs = kw.get("documents")
        if kw.get("embeddings") is None and docs is not None:
            kw = dict(kw, embeddings=[_hash_vec(d) for d in docs])
        return kw

    def upsert(self, **kw):
        return self._raw.upsert(**self._embed(kw))

    def add(self, **kw):
        return self._raw.add(**self._embed(kw))

    def update(self, **kw):
        return self._raw.update(**self._embed(kw))

    def delete(self, **kw):
        return self._raw.delete(**kw)

    def get(self, **kw):
        kw.setdefault("include", ["documents", "metadatas"])
        return self._raw.get(**kw)

    def count(self) -> int:
        return int(self._raw.count())

    def query(self, **kw):
        texts = kw.pop("query_texts", None)
        if texts is not None:
            kw["query_embeddings"] = [_hash_vec(t) for t in texts]
        kw.setdefault("include", ["documents", "metadatas", "distances"])
        return self._raw.query(**kw)


# ── a REAL Chroma collection with the REAL embedder (arm Z0e: retrieval QUALITY, as live) ────

MINILM_MODEL = Path.home() / ".cache" / "chroma" / "onnx_models" / "all-MiniLM-L6-v2" / "onnx" / "model.onnx"


def embedder_available() -> bool:
    """Chroma importable AND Chroma's MiniLM ONNX model already on disk: Z0e never downloads one (a missing model is a SKIP)."""
    return chroma_available() and MINILM_MODEL.is_file()


class EmbedCollection(DiskCollection):
    """The drawers collection over an in-process Chroma with the service's own MiniLM embedding function (``memory_service._drawers_embedding_function``):
    documents and queries are embedded by Chroma, exactly as in the live palace (l2 space, the live default). ``LabCollection`` ranks by bag-of-words, which
    measures the store's filters and not retrieval; this is the arm that does."""

    def _embed(self, kw: dict) -> dict:
        return kw

    def query(self, **kw):
        kw.setdefault("include", ["documents", "metadatas", "distances"])
        return self._raw.query(**kw)


def make_embed_collection(svc: types.SimpleNamespace) -> "EmbedCollection":
    if not embedder_available():
        raise NotImplementedError("arm Z0e needs chromadb and Chroma's MiniLM ONNX model already on disk "
                                  f"({MINILM_MODEL}); this arm never downloads one")
    import chromadb
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"          # a newer onnxruntime uploads telemetry to Microsoft: the lab never does
    ef = svc.memory_service._drawers_embedding_function()
    client = chromadb.EphemeralClient()
    return EmbedCollection(client.create_collection(f"zmb-z0e-{next(_DISK_SEQ)}-{os.getpid()}", embedding_function=ef))


# ── the lazy extractor (the negative control for extraction / abstention) ────

_FEMALE_NAMES = frozenset({"dana", "tove", "priya", "marisol", "anika", "odile", "ines", "saoirse",
                           "ottoline", "philippa", "mikaela"})
_MALE_NAMES = frozenset({"leo", "ravi", "teodor", "percival", "ignatius", "barnaby", "oskar", "tomas"})
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December")
_CAP_RE = re.compile(r"\b([A-Z][a-z]{2,})\b")


def lazy_extract_candidates(user_message: str, assistant_response: str = "",
                            prev_user_message: str | None = None):
    """Deliberately wrong in the four ways the bench has anti-needles for: it reads a numeric date
    month-first, guesses a person's role from their first name, turns a question into a fact, mines the
    assistant's own sentence, and pins every pronoun fact on the user. The real extractor is
    ``memory_extractor.extract_candidates``; this replaces it ONLY under the ``extractor`` control."""
    from memory_extractor import MemoryCandidate  # the service's own candidate type

    out: list[MemoryCandidate] = []
    msg = (user_message or "").strip()
    excerpt = msg[:220]

    def add(text: str, mtype: str = "fact") -> None:
        out.append(MemoryCandidate(text=text, memory_type=mtype, confidence=0.7, source_excerpt=excerpt))

    for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", msg):  # month-first, the wrong order
        mo, d, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if 1 <= mo <= 12:
            add(f"User's birthday is {_MONTHS[mo - 1]} {d} {y}")
    for name in _CAP_RE.findall(msg):  # a role from a first name
        low = name.lower()
        if low in _FEMALE_NAMES:
            add(f"User's wife is named {name}")
        elif low in _MALE_NAMES:
            add(f"User's husband is named {name}")
    count_list = re.search(r"\b(?:I|we) have (\w+) (kids|children)\b", msg, re.I)
    if count_list:  # the 2026-10-05 loss: a list of names after a count is reduced to the count (B9)
        add(f"User has {count_list.group(1)} {count_list.group(2)}")
    elif re.search(r"\bkids?\b|\bchildren\b", msg, re.I):  # everything named near 'kids' is a child
        for name in _CAP_RE.findall(msg):
            add(f"User's child is named {name}")
    if msg.endswith("?"):  # a question stored as a fact
        add("User asked: " + msg.rstrip("?").strip())
    elif re.match(r"^(?:do|can|could) you remember\b|^(?:what|who|when|where|how)\b", msg, re.I):
        # a spoken question (no '?', as the panel's speech-to-text delivers it) stored as a teach (E1b)
        add("User asked me to remember: " + re.sub(r"^(?:do|can|could) you remember\s*", "", msg, flags=re.I).strip())
    if (assistant_response or "").strip():  # the assistant's own words mined as the user's
        add("User: " + assistant_response.strip().rstrip("."))
    for rm in re.finditer(r"\bremember that ([^.\n]+)", msg, re.I):
        # a teach read off the raw text whoever wrote it: a pasted email's embedded instruction, a typed
        # 'system:' line (poisoning I1 / I1b / I4: the own-words wall is part of the real extractor)
        add("User asked me to remember: " + rm.group(1).strip())
    for lm in re.finditer(r"\b(?:says?|said)\b[:,]?\s*\"?I live in ([^\".,;!?\n]+)", msg):
        # a QUOTED third person's first-person words pinned on the owner (poisoning I2.attributed)
        add(f"User lives in {lm.group(1).strip()}")
    pm = re.search(r"\bmy (?:dog|cat|pet|rabbit|bird) (?:is named|is called|is) ([A-Z][a-z]+)", msg)
    if pm:  # a pet recorded as a child
        add(f"User's child is named {pm.group(1)}")
    if re.match(r"^(she|he)\b", msg, re.I) and msg:  # a pronoun fact pinned on the user
        add("User " + re.sub(r"^(she|he)\s+(is|'s)\s+", "is ", msg, flags=re.I).rstrip("."))
    if re.search(r"\bdon'?t live in ([A-Z][a-z]+)", msg):  # negation ignored
        city = re.search(r"\bdon'?t live in ([A-Z][a-z]+)", msg).group(1)
        add(f"User lives in {city}")
    return out


# ── controls ─────────────────────────────────────────────────────────────────

#: the scripted reader's mode (``reader`` control): the arm reads it at call time, inside the control scope
READER = {"sycophantic": False}
#: the scripted brain's mode for quote-retire (``retire_judge`` control): ``naive`` = no judgement, take the retrieval's top-1
RETIRE = {"naive": False}

@contextlib.contextmanager
def controls_off(features: "frozenset[str] | set[str]", svc: types.SimpleNamespace) -> Iterator[None]:
    """Switch the named features OFF for the duration of the block, then restore EXACTLY what was there
    (env and module attributes). Unknown feature names raise (a typo must not silently control nothing)."""
    unknown = sorted(set(features) - set(CONTROLS))
    if unknown:
        raise ValueError(f"unknown control(s) {', '.join(unknown)} (known: {', '.join(CONTROLS)})")
    undo: list[Any] = []

    def setenv(key: str, value: str) -> None:
        old = os.environ.get(key)
        os.environ[key] = value
        undo.append(lambda: (os.environ.__setitem__(key, old) if old is not None
                             else os.environ.pop(key, None)))

    def patch(obj: Any, name: str, value: Any) -> None:
        old = getattr(obj, name)
        setattr(obj, name, value)
        undo.append(lambda: setattr(obj, name, old))

    try:
        if "authority" in features:
            setenv("ZOE_MEMORY_AUTHORITY", "off")
        if "affect" in features:
            setenv("ZOE_AFFECT_CONSENT_GATE", "off")
        if "identity" in features:
            patch(svc.memory_service, "_identity_assertion_blocked", lambda *a, **k: False)
        if "speaker" in features:
            real_ingest = svc.memory_service.MemoryService.ingest
            real_edit = svc.memory_service.MemoryService.review

            async def ingest_no_verdict(self, *a, **kw):
                kw.pop("speaker_verified", None)   # the voice lane reports nothing: today's behaviour
                return await real_ingest(self, *a, **kw)

            async def review_no_verdict(self, *a, **kw):
                kw.pop("speaker_verified", None)
                return await real_edit(self, *a, **kw)
            patch(svc.memory_service.MemoryService, "ingest", ingest_no_verdict)
            patch(svc.memory_service.MemoryService, "review", review_no_verdict)
        if "tombstone" in features:
            patch(svc.memory_tombstones, "matching_tombstone", lambda *a, **k: None)
            patch(svc.memory_tombstones, "add", lambda *a, **k: None)
        if "sweep" in features:
            real_review = svc.memory_service.MemoryService.review

            async def review_no_forget_archive(self, mem_id, *, decision, actor, **kw):
                if decision == "archive" and str(kw.get("note") or "").startswith("forget_entity:"):
                    return None  # "archived" without archiving: the handler still says it forgot
                return await real_review(self, mem_id, decision=decision, actor=actor, **kw)
            patch(svc.memory_service.MemoryService, "review", review_no_forget_archive)
        if "physical_erase" in features:
            setenv("ZOE_MEMORY_PHYSICAL_ERASE", "0")
        if "extractor" in features:
            patch(svc.memory_extractor, "extract_candidates", lazy_extract_candidates)
        if "gate" in features:
            patch(svc.memory_quality, "is_storable_fact", lambda _t: (True, ""))
        if "reader" in features:
            old_mode = READER["sycophantic"]
            READER["sycophantic"] = True
            undo.append(lambda: READER.__setitem__("sycophantic", old_mode))
        if "supersede" in features:
            setenv("ZOE_MEMORY_IMPLICIT_SUPERSEDE", "off")
        if "invalidate" in features:
            ms = svc.memory_service.MemoryService
            real_write_row, real_sup = ms._write_row, ms._supersede_by_sync

            def write_row_no_history(self, mem_id, text, metadata):
                if str((metadata or {}).get("status") or "") == "superseded":
                    self._collection().delete(ids=[mem_id])      # "invalidated" by deleting the row
                    return None
                return real_write_row(self, mem_id, text, metadata)

            def supersede_by_deleting(self, user_id, old_id, new_id):
                done = real_sup(self, user_id, old_id, new_id)
                if done:
                    self._collection().delete(ids=[old_id])
                return done
            patch(ms, "_write_row", write_row_no_history)
            patch(ms, "_supersede_by_sync", supersede_by_deleting)
        if "event_time" in features:
            patch(svc.memory_temporal, "parse_validity", lambda *a, **k: svc.memory_temporal.Validity())
        if "history" in features:
            patch(svc.memory_temporal, "is_history_question", lambda *_a, **_k: False)
        if "retrieval" in features:
            ms = svc.memory_service.MemoryService

            def newest_first_search(self, query, user_id, limit, depth_by_pid=None):
                import datetime as _dt
                rows = self._visible_rows(user_id, _dt.datetime.now(_dt.timezone.utc))
                rows.sort(key=lambda r: (float((r.metadata or {}).get("added_ts") or 0.0), r.id), reverse=True)
                return rows[:limit]
            patch(ms, "_semantic_search", newest_first_search)
        if "provenance" in features:
            ms = svc.memory_service.MemoryService
            raw_build = ms.__dict__["_build_metadata"]        # a staticmethod: restore the descriptor itself
            real_build = ms._build_metadata

            def build_without_provenance(**k):
                md = real_build(**k)
                md.pop("source_excerpt", None)
                md.pop("user_turn_id", None)
                return md
            ms._build_metadata = staticmethod(build_without_provenance)
            undo.append(lambda: setattr(ms, "_build_metadata", raw_build))
        if "entailment" in features:
            # the verbatim-anchor rule removed: a per-turn model writer never carries user_stated power, so the owner's own change
            # of mind is held back as a disputed candidate (with the wall ON; with the wall OFF nothing is held back at all)
            patch(importlib.import_module("memory_authority"), "entailing_span", lambda fact, user_text: None)
        if "retire_cue" in features:
            mr = importlib.import_module("memory_retire")
            patch(mr, "_is_change_sentence", lambda sentence: "any" if sentence.strip() else None)
        if "retire_judge" in features:
            old_naive = RETIRE["naive"]
            RETIRE["naive"] = True
            undo.append(lambda: RETIRE.__setitem__("naive", old_naive))
        if "retire_speaker" in features:
            patch(importlib.import_module("memory_retire"), "check_speaker", lambda lane, user_id, verified: "")
        if "retire_ownwords" in features:
            patch(importlib.import_module("memory_retire"), "own_part", lambda text: ((text or "").strip(), False))
        if "retire_offered" in features:
            patch(importlib.import_module("memory_retire"), "check_offered", lambda row_id, offered: bool(row_id))
        if "retire_owner_row" in features:
            patch(importlib.import_module("memory_retire"), "subject_ok", lambda quote, row_text: True)
        if "retire_shadow" in features:
            mr = importlib.import_module("memory_retire")
            real_decide = mr.decide

            async def decide_applying_in_shadow(svc, user_id, prep, **kw):
                if kw.get("mode_override") == "shadow":
                    kw["mode_override"] = "enforce"
                return await real_decide(svc, user_id, prep, **kw)
            patch(mr, "decide", decide_applying_in_shadow)
        if "retire_quote" in features or "retire_forget" in features:
            ms_cls = svc.memory_service.MemoryService
            real_sync = ms_cls._retire_with_quote_sync
            keep_quote = "retire_quote" not in features

            def retire_without_quote(self, user_id, old_id, quote, turn_ref, lane, cue):
                done = real_sync(self, user_id, old_id, quote, turn_ref, lane, cue)
                if done:
                    col = self._collection()
                    got = col.get(ids=[old_id], include=["metadatas", "documents"])
                    meta = dict((got.get("metadatas") or [{}])[0] or {})
                    held = meta.get("retire_quote", "")
                    meta["retire_quote"] = ""          # blanked, not popped: a real Chroma update MERGES metadata and would keep the key
                    if keep_quote:
                        meta["quote_elsewhere"] = held     # retire_forget: kept, but where the forget sweep never looks
                    col.update(ids=[old_id], metadatas=[meta])
                return done
            patch(ms_cls, "_retire_with_quote_sync", retire_without_quote)
        if "topic" in features:
            sup = importlib.import_module("memory_supersede")
            patch(sup, "same_topic", lambda new, old: True)
            patch(sup, "exclusive_conflict", lambda new, old: True)
        yield
    finally:
        for fn in reversed(undo):
            fn()


# ── a service wired to a lab collection ──────────────────────────────────────

class LabService:
    """A real ``MemoryService`` whose store, audit lane and opt-out lookup never leave memory, plus the
    module patches the in-process forget handler needs. ``close()`` restores every patched attribute
    (the lab leaves nothing global behind, so it is safe inside a pytest session or a long process)."""

    def __init__(self, svc: types.SimpleNamespace, tag: str = "bench", disk: bool = False, embed: bool = False):
        self.svc = svc
        self._embed_col = None
        self._undo: list[Any] = []
        self._disk = disk
        data_dir = os.path.join(scratch_root(), f"palace-{tag}-{os.getpid()}"
                                + (f"-disk{next(_DISK_SEQ)}" if disk else ""))   # a fresh path per disk lab: chroma caches clients by path
        if svc.live_store_guard.is_live_palace(data_dir):  # cannot happen; refuse rather than assume
            raise LabRefusal(f"lab data dir {data_dir} resolves to the live palace")
        self.data_dir = data_dir
        if disk:
            shutil.rmtree(data_dir, ignore_errors=True)
            os.makedirs(data_dir)
            client = svc.memory_service._palace_client(data_dir)   # the service's own cached client: one SQLite
            self.col = DiskCollection(client.get_or_create_collection("mempalace_drawers"))
        elif embed:
            self.col = self._embed_col = make_embed_collection(svc)
        else:
            self.col = LabCollection()
        self.service = svc.memory_service.MemoryService(data_dir=data_dir)
        col = self.col
        self.service._collection = lambda: col

        async def no_audit(**_kw):
            return None

        async def no_tick(*_a, **_k):
            return None

        async def not_opted_out(_uid):
            return False

        self.service._append_audit = no_audit
        self.service._tick_access = no_tick
        self.service.tick_access = no_tick
        self.service.tick_consolidation = no_tick
        self._patch(svc.memory_service, "_user_opted_out", not_opted_out)
        if not disk:
            # the in-memory lab has no files to erase: F1-F4 keep measuring the archive step the handler takes
            # first; the disk cells (F5 / F6) run the erase over REAL Chroma
            self._patch(svc.memory_service, "physical_erase_enabled", lambda: False)
        # the forget intent handler looks the service up through the module singleton at call time
        self._patch(svc.memory_service, "get_memory_service", lambda: self.service)

    def _patch(self, obj: Any, name: str, value: Any) -> None:
        old = getattr(obj, name)
        setattr(obj, name, value)
        self._undo.append(lambda: setattr(obj, name, old))

    def close(self) -> None:
        while self._undo:
            self._undo.pop()()
        if self._embed_col is not None:
            try:
                self._embed_col._raw._client.delete_collection(self._embed_col._raw.name)       # the ephemeral client lives as long as the process: drop the collection
            except Exception:  # noqa: BLE001
                pass
            self._embed_col = None
        if self._disk:   # a real palace is a directory on disk: forget the cached client and delete the files
            key = os.path.realpath(self.data_dir)
            try:
                self.svc.memory_service._AUDIT_CLIENTS.pop(key, None)
                try:
                    from chromadb.api.shared_system_client import SharedSystemClient
                    SharedSystemClient.clear_system_cache()
                except Exception:  # noqa: BLE001 - older chroma: the per-path cache is harmless once the path is unique
                    pass
            finally:
                shutil.rmtree(self.data_dir, ignore_errors=True)
