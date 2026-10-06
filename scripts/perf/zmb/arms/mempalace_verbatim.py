"""Arm MV: the VERBATIM tier of the HM design - MemPalace used as a LIBRARY, behind the bench's arm interface.

What it is (docs/research/memory-arm-hm-hindsight-mempalace-2026-10-06.md, Part A.1): ``mempalace==3.10.0`` exposes
no write API of its own for a conversation turn, but its storage layer IS importable: ``mempalace.palace.get_collection``
returns a first-class Chroma collection (``upsert`` / ``query`` / ``get`` / ``delete``), ``mempalace.searcher.search_memories``
is the hybrid reader, and a "drawer" is just a document with ``wing`` / ``room`` metadata. The CLI miner is not needed
and is not used. This adapter writes one drawer per user turn, one WING per household member (the account id), rooms
``voice`` / ``chat`` (ordinary recall), ``unverified`` / ``quoted`` (quarantine, explicit requests only).

Two stores sit behind the same ``VerbatimStore`` protocol:

* ``MemPalaceLibraryStore``  - the real thing (needs the bake-off venv: ``/home/zoe/.zoe/bakeoff-2026-10/mempalace-venv``).
  It sets ``HOME`` / ``MEMPALACE_CONFIG_DIR`` to a SCRATCH home for its lifetime because MemPalace keeps its write locks
  under ``~/.mempalace/locks`` and its config under ``~/.mempalace`` - never the live ones.
* ``InMemoryVerbatimStore``  - a TEST DOUBLE (token overlap, no embeddings). It exists so the adapter's POLICY (what is
  stored, where, as what; what a forget deletes) is tested in the slim CI lane. It makes NO claim about retrieval quality.

Nothing here is imported by the runner until ``make_arm("MV"/"HM")`` is asked for. Never touches the live palace.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Protocol

from .base import Arm, IngestReport, ROW_KEYS, Turn
from .hm_policy import (DEFAULT_ROOMS, GUEST_IDS, ROOM_QUOTED, ROOM_UNVERIFIED, Controls, Decision, HashedLedger,
                        classify)

BAKEOFF_VENV = Path("/home/zoe/.zoe/bakeoff-2026-10/mempalace-venv")
INSTALL_HINT = (
    "MemPalace is not importable here. To run the real verbatim tier: use the bake-off venv "
    f"({BAKEOFF_VENV}: py3.12, `pip install mempalace==3.10.0`, chromadb 1.5.9) with a scrubbed HOME, e.g. "
    "`bash /home/zoe/.zoe/bakeoff-2026-10/mp_run.sh <script>`; the model files must already be in "
    "$HOME/.cache/chroma/onnx_models (no network at run time: HF_HUB_OFFLINE=1)."
)
DAY_S = 86400.0
BASE_TS = 1_790_000_000.0          # 2026-09-21: a fixed origin so a backdated run is reproducible


class StoreUnavailable(RuntimeError):
    """The real MemPalace library cannot be opened in this interpreter."""


class VerbatimStore(Protocol):
    kind: str

    def add(self, rid: str, wing: str, room: str, text: str, meta: "dict[str, Any]") -> None: ...
    def search(self, query: str, wing: str, k: int, rooms: "tuple[str, ...] | None") -> "list[dict[str, Any]]": ...
    def get_all(self, wing: "str | None") -> "list[dict[str, Any]]": ...
    def delete(self, ids: "list[str]") -> int: ...
    def count(self) -> int: ...
    def erase_physical(self) -> int: ...
    def residue(self, needle: str) -> int: ...
    def close(self) -> None: ...


_TOK = re.compile(r"[a-z0-9']+")
_STOP = frozenset("a an the is are was were be am do does did what whats when where who whom my our your of to in on at "
                  "for from with about and or i me we you it that this there please tell find sentence said say".split())


def _stem(t: str) -> str:
    return t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t


def _toks(text: str) -> "set[str]":
    """Content tokens, crudely stemmed ('lives' ~ 'live'). The in-memory double's only notion of relevance."""
    return {_stem(t) for t in _TOK.findall((text or "").lower()) if t not in _STOP and len(t) > 1}


class InMemoryVerbatimStore:
    """TEST DOUBLE of the verbatim tier: a dict plus token-overlap ranking. No embeddings, no disk, no network."""
    kind = "in-memory-double"

    def __init__(self) -> None:
        self._rows: "dict[str, dict[str, Any]]" = {}

    def add(self, rid, wing, room, text, meta):
        self._rows[rid] = {"id": rid, "text": text, "wing": wing, "room": room, "meta": dict(meta)}

    def search(self, query, wing, k, rooms):
        q = _toks(query)
        scored = []
        for r in self._rows.values():
            if r["wing"] != wing or (rooms is not None and r["room"] not in rooms):
                continue
            overlap = len(q & _toks(r["text"]))
            if overlap:
                scored.append((overlap / (1 + 0.05 * len(_toks(r["text"]))), r["id"], r))
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [dict(r, score=round(s, 4)) for s, _i, r in scored[:k]]

    def get_all(self, wing):
        return [dict(r) for r in self._rows.values() if wing is None or r["wing"] == wing]

    def delete(self, ids):
        n = 0
        for i in ids:
            n += 1 if self._rows.pop(i, None) is not None else 0
        return n

    def count(self):
        return len(self._rows)

    def erase_physical(self):
        return 0                          # a dict has no residue: the cell that needs a disk store SKIPs on this double

    def residue(self, needle):
        return sum(1 for r in self._rows.values() if needle.lower() in r["text"].lower())

    def ledger_residue(self, ledger, user):
        return sum(1 for r in self._rows.values() if r["wing"] == user and ledger.matches(user, r["text"]))

    def close(self):
        self._rows.clear()


#: run in a CLEAN child process by ``MemPalaceLibraryStore.erase_physical`` (argv: old palace dir, new palace dir)
_REBUILD_CHILD = """
import sys
from mempalace.palace import get_collection
old, new = sys.argv[1], sys.argv[2]
col = get_collection(old, create=False, read_only=True)
got = col.get(include=["documents", "metadatas", "embeddings"])
ncol = get_collection(new, create=True)
ids = list(got["ids"])
for j in range(0, len(ids), 25):
    ncol.upsert(ids=ids[j:j + 25], documents=got["documents"][j:j + 25], metadatas=got["metadatas"][j:j + 25],
                embeddings=[list(map(float, e)) for e in got["embeddings"][j:j + 25]])
print(len(ids))
"""


class MemPalaceLibraryStore:
    """MemPalace 3.10.0 as a library: ``get_collection`` + ``upsert`` / ``query`` / ``get`` / ``delete``."""
    kind = "mempalace-library"

    def __init__(self, palace_dir: "str | Path", *, home: "str | Path | None" = None):
        self.palace_dir = Path(palace_dir)
        self._root, self._gen = self.palace_dir, 0
        self._probe_needle = os.environ.get("ZMB_PROBE_NEEDLE", "")    # a diagnostic: see WHEN a name reaches the files
        self.after_child = self.after_reopen = 0
        self.palace_dir.mkdir(parents=True, exist_ok=True)
        self.home = Path(home) if home else self.palace_dir.parent / "mp-home"
        self._saved = {k: os.environ.get(k) for k in ("HOME", "MEMPALACE_CONFIG_DIR", "ANONYMIZED_TELEMETRY",
                                                      "HF_HUB_OFFLINE")}
        self._pin_model_path()                  # BEFORE the HOME switch: a missing model must never be downloaded
        self._scratch_env()
        try:
            from mempalace.palace import get_collection  # noqa: WPS433 - lazy: only the real store needs it
            self.col = get_collection(str(self.palace_dir), create=True)
        except ImportError as exc:
            self._restore_env()
            raise StoreUnavailable(f"{exc}. {INSTALL_HINT}") from None

    def _pin_model_path(self) -> None:
        """Chroma's MiniLM class computes ``DOWNLOAD_PATH = ~/.cache/chroma/...`` ONCE, at import, and downloads the
        model from a public S3 bucket when that path is empty. Switching HOME afterwards (this adapter does, for the
        lock directory) would point a LATER import at an empty scratch cache. So: import it now, under the real HOME,
        pin the path to the real cache, and refuse (``StoreUnavailable``) if the model files are not there - this
        adapter never touches the network (found the hard way: a second arm in one process downloaded 79 MB)."""
        try:
            from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
        except ImportError as exc:
            raise StoreUnavailable(f"{exc}. {INSTALL_HINT}") from None
        real = Path(self._saved["HOME"] or Path.home()) / ".cache" / "chroma" / "onnx_models" / ONNXMiniLM_L6_V2.MODEL_NAME
        if not ((real / ONNXMiniLM_L6_V2.EXTRACTED_FOLDER_NAME / "model.onnx").is_file()
                and (real / ONNXMiniLM_L6_V2.EXTRACTED_FOLDER_NAME / "tokenizer.json").is_file()):
            if not (Path(ONNXMiniLM_L6_V2.DOWNLOAD_PATH) / ONNXMiniLM_L6_V2.EXTRACTED_FOLDER_NAME / "model.onnx").is_file():
                raise StoreUnavailable("the MiniLM ONNX model is not in the chroma cache and this adapter will not "
                                       f"download it ({real}). {INSTALL_HINT}")
            return                                                  # already pinned by an earlier store: keep it
        ONNXMiniLM_L6_V2.DOWNLOAD_PATH = real

    def _scratch_env(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        real_cache = Path(self._saved["HOME"] or Path.home()) / ".cache" / "chroma"
        scratch_cache = self.home / ".cache" / "chroma"
        if real_cache.is_dir() and not scratch_cache.exists():      # the model files, read-only use
            scratch_cache.parent.mkdir(parents=True, exist_ok=True)
            scratch_cache.symlink_to(real_cache)
        os.environ.update({"HOME": str(self.home), "MEMPALACE_CONFIG_DIR": str(self.home / ".mempalace-cfg"),
                           "ANONYMIZED_TELEMETRY": "False", "HF_HUB_OFFLINE": "1", "ORT_DISABLE_TELEMETRY": "1"})

    def _restore_env(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def add(self, rid, wing, room, text, meta):
        m = {"wing": wing, "room": room, "source_file": f"hm:{wing}:{room}", "added_by": "hm-arm", **meta}
        self.col.upsert(ids=[rid], documents=[text], metadatas=[m])

    def search(self, query, wing, k, rooms):
        where: "dict[str, Any]" = {"wing": wing}
        if rooms is not None:
            where = {"$and": [{"wing": wing}, {"room": {"$in": list(rooms)}}]}
        r = self.col.query(query_texts=[query], n_results=max(1, k), where=where,
                           include=["documents", "metadatas", "distances"])
        out = []
        for i, d, m, dist in zip(r["ids"][0], r["documents"][0], r["metadatas"][0], r["distances"][0]):
            m = m or {}
            out.append({"id": i, "text": d or "", "wing": m.get("wing", ""), "room": m.get("room", ""),
                        "meta": m, "score": round(1.0 - float(dist), 4)})
        return out

    def get_all(self, wing):
        kw = {"where": {"wing": wing}} if wing else {}
        g = self.col.get(include=["documents", "metadatas"], **kw)
        return [{"id": i, "text": d or "", "wing": (m or {}).get("wing", ""), "room": (m or {}).get("room", ""),
                 "meta": m or {}} for i, d, m in zip(g["ids"], g["documents"], g["metadatas"])]

    def delete(self, ids):
        have = set(self.col.get(ids=list(ids), include=[])["ids"]) if ids else set()
        if have:
            self.col.delete(ids=sorted(have))
        return len(have)

    def count(self):
        return int(self.col.count())

    def erase_physical(self):
        """Rebuild the palace from its surviving rows into a FRESH directory in a CLEAN CHILD PROCESS, reusing the
        stored vectors (no embedding pass: about 1 ms per row, measured), then delete the old directory.

        Why a rebuild: deleting a drawer through the API leaves the text in ``chroma.sqlite3`` (free pages, the FTS5
        index and the ``embeddings_queue`` log): measured, ``VACUUM`` + WAL checkpoint cut 16 byte-hits of a forgotten
        name to 4, all in ``embeddings_queue``; only a rebuild reached 0.

        Why a CHILD process: it never held the forgotten text, so what it writes cannot contain it - measured, 0 of 60
        palaces had the name right after the child finished and right after the parent re-opened them. It is NOT
        sufficient on its own: the name then appeared in ``data_level0.bin`` (the HNSW vector file) in 13 of 60 runs
        with an in-process rebuild and in 21 and 17 of 60 with the child, after the PARENT's later reads/flushes -
        hnswlib writes its pre-allocated block, whose unused bytes are uninitialised heap that still holds the freed
        text. With ``MALLOC_PERTURB_=85 PYTHONMALLOC=malloc`` (``hardened_heap()``) the same loop gave 0 of 60.
        So: child rebuild + a scrubbing allocator in the host + ``HashedLedger.scan_bytes`` as the verifier (it needs
        no plaintext).
        Returns the number of rows carried over."""
        import shutil
        import subprocess
        import sys
        old = self.palace_dir
        self._gen += 1
        new = self._root.parent / f"{self._root.name}-gen{self._gen}"
        self.col = None
        try:                                              # release the cached client of the old path first
            import mempalace.palace as mp
            mp._DEFAULT_BACKEND._drain_clients()
            import chromadb
            chromadb.api.client.SharedSystemClient.clear_system_cache()
        except Exception:                                 # noqa: BLE001 - best effort; the child opens read-only anyway
            pass
        done = subprocess.run([sys.executable, "-c", _REBUILD_CHILD, str(old), str(new)], env=dict(os.environ),
                              capture_output=True, text=True, timeout=300)
        if done.returncode != 0:
            from mempalace.palace import get_collection
            self.col = get_collection(str(old), create=False)     # keep serving from the old palace; the caller sees it
            self._gen -= 1
            raise RuntimeError("palace rebuild failed: " + (done.stderr or done.stdout)[-300:])
        n = int(done.stdout.strip().splitlines()[-1])
        shutil.rmtree(old, ignore_errors=True)
        self.palace_dir = new
        self.after_child = self._probe_needle and self.residue(self._probe_needle)     # diagnostics only
        from mempalace.palace import get_collection
        self.col = get_collection(str(new), create=False)
        self.after_reopen = self._probe_needle and self.residue(self._probe_needle)
        return n

    def residue(self, needle):
        """Case-insensitive byte count of ``needle`` over every file of the palace directory."""
        pat = re.compile(re.escape(needle.encode()), re.IGNORECASE)
        return sum(len(pat.findall(f.read_bytes())) for f in self.palace_dir.rglob("*") if f.is_file())

    def ledger_residue(self, ledger: HashedLedger, user: str) -> int:
        """Files of the palace in which the LEDGER still matches a forgotten entity (no plaintext needed)."""
        return sum(1 for f in self.palace_dir.rglob("*") if f.is_file() and ledger.scan_bytes(user, f.read_bytes()))

    def close(self):
        self.col = None
        self._restore_env()


def hardened_heap() -> bool:
    """Is THIS process running with a scrubbing allocator (``MALLOC_PERTURB_`` set and Python routed through libc
    malloc)? Measured: without it, a forgotten name reappeared in the HNSW vector file (``data_level0.bin``,
    uninitialised heap that still held the freed text) in 13-21 of 60 forget+rebuild runs; with
    ``MALLOC_PERTURB_=85 PYTHONMALLOC=malloc`` in 0 of 60."""
    return bool(os.environ.get("MALLOC_PERTURB_")) and os.environ.get("PYTHONMALLOC") == "malloc"


def library_available() -> bool:
    """Can the real MemPalace library be imported in THIS interpreter?"""
    try:
        import mempalace  # noqa: F401
        import chromadb  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _name_pattern(entity: str) -> "re.Pattern[str]":
    """Case-blind, hyphen/space-tolerant whole-word pattern for a forgotten name ('Mari-sol' = 'Marisol')."""
    letters = [re.escape(c) for c in re.sub(r"[\s\-_]+", "", entity.strip())]
    body = r"[\s\-_]?".join(letters)
    return re.compile(rf"(?<!\w){body}(?:'s|s)?(?!\w)", re.IGNORECASE)


class MemPalaceVerbatimArm(Arm):
    """The verbatim tier as an ``Arm``: ``ingest`` / ``recall`` / ``forget`` / ``as_of`` / ``stats``.

    ``ingest`` stores one chunk per VERIFIED user turn with NO model call (the owner's design: the verbatim tier is
    written instantly). Quarantine rooms hold unverified / pasted text; guests own no wing."""
    name = "MV"
    capabilities = frozenset({"clock", "identities", "verbatim"})

    def __init__(self, store: "VerbatimStore | None" = None, *, controls: "Controls | None" = None,
                 ledger: "HashedLedger | None" = None, palace_dir: "str | Path | None" = None):
        self.controls = controls or Controls()
        self.ledger = ledger if ledger is not None else HashedLedger()
        self._palace_dir = palace_dir
        self._tmp: "tempfile.TemporaryDirectory | None" = None
        self._store_arg = store
        self.store: "VerbatimStore | None" = None
        self._user = ""
        self._seq = 0
        self._clock = 0.0
        self.writes_refused = 0
        self.model_calls = 0            # the verbatim write path makes none; a cell reads this

    # ── lifecycle ─────────────────────────────────────────────────────────
    def _open(self) -> "VerbatimStore":
        if self._store_arg is not None:
            return self._store_arg
        if self._palace_dir is None:
            self._tmp = tempfile.TemporaryDirectory(prefix="zmb-mv-")
            base = Path(self._tmp.name)
        else:
            base = Path(self._palace_dir)
        try:
            return MemPalaceLibraryStore(base / "palace")
        except StoreUnavailable as exc:
            raise NotImplementedError(f"arm MV: {exc}") from None

    def reset(self, user_id: str) -> None:
        self.close()
        self.store = self._open()
        self._user = user_id
        self._seq = 0
        self._clock = 0.0
        self.writes_refused = 0
        self.model_calls = 0

    def close(self) -> None:
        if self.store is not None:
            self.store.close()
            self.store = None
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None

    def advance_clock(self, seconds: float) -> None:
        self._clock += float(seconds)

    def _need(self) -> "VerbatimStore":
        if self.store is None:
            raise RuntimeError("call reset(user_id) first")
        return self.store

    # ── rows ──────────────────────────────────────────────────────────────
    def _row(self, hit: "dict[str, Any]") -> "dict[str, Any]":
        m = hit.get("meta") or {}
        room = hit.get("room", "")
        return {"id": hit["id"], "text": hit["text"], "status": "approved" if room in DEFAULT_ROOMS else "pending",
                "authority_class": str(m.get("authority_class") or ""), "origin": f"verbatim:{room}",
                "contradicts_id": "", "entity_type": "", "memory_type": "verbatim",
                "user_id": hit.get("wing", ""), "room": room, "filed_ts": float(m.get("filed_ts") or 0.0),
                "score": hit.get("score", 0.0)}

    def stats(self) -> "dict[str, Any]":
        rows = [self._row(h) for h in self._need().get_all(self._user)]
        rows.sort(key=lambda r: (r["filed_ts"], r["id"]))
        counts: "dict[str, int]" = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return {"rows": rows, "counts": counts, "writes_refused": self.writes_refused, "row_keys": list(ROW_KEYS),
                "model_calls": self.model_calls}

    def stats_as(self, identity: str) -> "dict[str, Any]":
        saved, self._user = self._user, self._identity_user(identity)
        try:
            return self.stats()
        finally:
            self._user = saved

    # ── identities (the account a turn belongs to) ────────────────────────
    @staticmethod
    def _identity_user(identity: str) -> str:
        table = {"consenting_owner": "demo_bar_00000001", "owner_no_mode": "demo_bar_00000002",
                 "minor": "demo_bar_00000003", "guest": "guest", "voice_guest": "voice-guest",
                 "harness": "demo_bar_00000004"}
        if identity not in table:
            raise ValueError(f"unknown identity {identity!r} (known: {', '.join(table)})")
        return table[identity]

    # ── the five calls ────────────────────────────────────────────────────
    def decide(self, turn: Turn, user: str) -> Decision:
        return classify(turn.speaker, user, self.controls)

    def write_chunk(self, user: str, turn: Turn, d: Decision) -> str:
        """One drawer, no model call. Returns its id."""
        self._seq += 1
        rid = "v" + hashlib.sha1(f"{user}|{d.room}|{self._seq}|{turn.text}".encode()).hexdigest()[:14]
        ts = BASE_TS + turn.day_offset * DAY_S + self._clock + self._seq * 0.001
        self._need().add(rid, user, d.room, turn.text,
                         {"authority_class": d.authority_class, "speaker": turn.speaker,
                          "speaker_verified": d.authority_class == "user_stated", "filed_ts": ts,
                          "filed_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)),
                          "memory_type_hint": turn.memory_type})
        return rid

    def ingest(self, turns: "list[Turn]") -> IngestReport:
        return self._ingest_for(self._user, turns)

    def ingest_as(self, identity: str, turns: "list[Turn]") -> IngestReport:
        return self._ingest_for(self._identity_user(identity), turns)

    def _ingest_for(self, user: str, turns: "list[Turn]") -> IngestReport:
        rep = IngestReport()
        for t in turns:
            rep.turns += 1
            d = self.decide(t, user)
            if not d.store:
                rep.refused += 1
                rep.notes.append(d.reason)
                continue
            if self.controls.ledger_write_check and self.ledger.matches(user, t.text):
                rep.refused += 1
                rep.notes.append("names a forgotten entity")
                continue
            self.write_chunk(user, t, d)
            rep.written += 1
        self.writes_refused += rep.refused
        return rep

    def search(self, query: str, k: int = 10, *, rooms: "tuple[str, ...] | None" = DEFAULT_ROOMS,
               user: "str | None" = None) -> "list[dict[str, Any]]":
        """Rows for ``query`` in ``user``'s wing. ``rooms=None`` includes the quarantine rooms (explicit request)."""
        who = user or self._user
        if not self.controls.isolate_wing:
            hits = self._unscoped(query, k, rooms)
        else:
            hits = self._need().search(query, who, k, rooms)
            hits = [h for h in hits if h.get("wing") == who]          # defence in depth: never trust the filter alone
        return [self._row(h) for h in hits]

    def _unscoped(self, query: str, k: int, rooms) -> "list[dict[str, Any]]":
        """NEGATIVE CONTROL only: a read that forgot the wing filter (every household member's chunks)."""
        merged: "list[dict[str, Any]]" = []
        for w in sorted({h["wing"] for h in self._need().get_all(None)}):
            merged += self._need().search(query, w, k, rooms)
        merged.sort(key=lambda h: -h.get("score", 0.0))
        return merged[:k]

    def recall(self, query: str, k: int = 10) -> "list[dict[str, Any]]":
        return self.search(query, k)

    def forget(self, entity: str) -> str:
        """What MemPalace ALONE can do: delete the user's chunks whose text names ``entity`` (a case-blind lexical
        match; a Zoe-side id index is the HM design's second route). Returns the confirmation."""
        pat = _name_pattern(entity)
        ids = [h["id"] for h in self._need().get_all(self._user) if pat.search(h["text"])]
        n = self._need().delete(ids)
        return f"deleted {n} verbatim chunk(s) naming that entity"

    def forget_ids(self, ids: "list[str]") -> int:
        return self._need().delete(list(ids))

    def sweep_ledger(self, user: "str | None" = None) -> "list[str]":
        """Delete every chunk of ``user`` whose text the ledger matches (catches what the name match missed and
        what arrived later); returns the deleted ids. The ledger holds hashes only: this is the no-plaintext route."""
        who = user or self._user
        ids = [h["id"] for h in self._need().get_all(who) if self.ledger.matches(who, h["text"])]
        self._need().delete(ids)
        return ids

    def as_of(self, query: str, ts: str) -> "list[dict[str, Any]]":
        """Append-only store: as-of is a filter on the filing time (what had been said by ``ts``)."""
        import calendar
        cutoff = calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
        return [r for r in self.search(query, 50) if r["filed_ts"] <= cutoff]
