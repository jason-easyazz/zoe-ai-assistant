"""Arms H0 / H1 / H2: Hindsight (vectorize-io/hindsight 0.10.2, ``hindsight-api-slim``) behind the bench's arm interface.

The adapter speaks Hindsight's HTTP API only (``/v1/default/banks/{id}/...``): no import of the package, no fork, no patch.
The server, the scratch Postgres, the loopback embeddings shim (``embed_shim.py``) and the Gemma clone are started by
``bakeoff.py`` inside an operator-run brain window; this file never starts anything. With no server it raises
``HindsightUnavailable`` (a ``NotImplementedError``: every cell SKIPs with the reason, never PASSes).

Variants (decision record section 6; ``bank config`` = ``PATCH /v1/default/banks/{id}/config``):

    H0  concise extraction, observations ON (auto), NO Zoe layer     - what Hindsight does natively
    H1  verbatim, observations OFF, reranker OFF, Zoe layer ON        - the lean arm
    H2  concise, observations ON (consolidated per authority scope),  - the full arm: the tag-scope FENCE
        reranker OFF, Zoe layer ON

One bank per synthetic user (``zmb-<variant>-<demo_bar_xxxxxxxx>``); tags carry the Zoe provenance class
(``user:<id>``, ``class:<authority class>``, ``origin:<writer>``, ``lane:<speaker>``). ``recall`` = ``POST .../memories/recall``,
rows = ``GET .../memories/list`` joined with the Zoe layer's side table (the held-back / retired / pending rows Hindsight has no
status for). ``as_of`` raises ``NotImplementedError``: 0.10.2 has recall-time ``temporal_window`` (a RANK hint, "ranks, it does not
filter") but no belief-time filter, so "what did the store believe at T" cannot be answered (docs: api RecallRequest.temporal_window).

What a model writer hands the store. A ``system_writer`` turn's ``proposes`` are the OUTPUT of a model pass (the digest's facts);
that is what the pass hands to the memory system, so that is what is retained (Hindsight's own extraction then runs over each
sentence). The transcript behind it is only the anchor the Zoe layer judges authority against, exactly as in ``arms.z0``.

THE ZOE LAYER (``ZoeLayer``, between the two marker lines below; counted by ``zoe_layer_lines()`` for decision-rule G3, <= 1,000)
is the same set of checks ``arms.z0`` exercises, REUSING the service's own modules rather than copying them: the authority
classes and conflict wall (``memory_authority.resolve_write / find_conflict / may_override``), the identity wall
(``identity_facts``), the affect-consent gate (``MemoryService._affect_allowed``, called unbound), the write-quality gate and the
deterministic extractor (``memory_quality`` / ``memory_extractor``), and the durable hashed forget ledger
(``memory_forgotten``, in-memory backend). Each protection has a named switch (``off=``) so a negative control can turn ONE off
and the cell that claims it must go red (``tests/unit/test_zmb_hindsight_arm.py``).
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib
import json
import os
import re
import time
import types
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from .base import Arm, IngestReport, ROW_KEYS, Turn
from .z0 import DEMO_USER_RE, IDENTITIES, reader_answer

VARIANTS = {
    "H0": "concise + observations ON, no Zoe layer (what Hindsight does natively)",
    "H1": "verbatim, observations OFF, reranker OFF, Zoe gate + forget ledger in front (lean)",
    "H2": "concise + observations ON, consolidation per authority tag scope, Zoe layer ON (full)",
}
INSTALL_HINT = (
    "Hindsight is not installed or not reachable here. Install: a py3.12 venv under /home/zoe/.zoe/bakeoff-2026-10/ with "
    "`pip install 'hindsight-api-slim==0.10.2'` (done on the lab box 2026-10-05). Run: `scripts/perf/zmb/bakeoff_window.sh` (the owner's one "
    "command: scratch Postgres, loopback embeddings shim, Gemma clone on :11500, `hindsight-api`, all inside a brain-stop window), or point "
    "ZMB_HINDSIGHT_URL at a running loopback server. See docs/knowledge/bakeoff-howto.md."
)
DEFAULT_URL = "http://127.0.0.1:18888"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
API = "/v1/default/banks"

#: per-bank config each variant sets (``PATCH /banks/{id}/config``; python field names, G0 report section 3.5)
BANK_CONFIG = {
    "H0": {"retain_extraction_mode": "concise", "enable_observations": True, "enable_auto_consolidation": True,
           "enable_reranking": False},
    "H1": {"retain_extraction_mode": "verbatim", "enable_observations": False, "enable_reranking": False,
           "retain_chunk_size": 1500},
    "H2": {"retain_extraction_mode": "concise", "enable_observations": True, "enable_auto_consolidation": False,
           "enable_reranking": False},
}
#: Zoe-layer protections that can be switched OFF one at a time (negative controls)
LAYER_FEATURES = ("guest", "affect", "identity", "ledger", "authority", "gate", "scrub", "provenance", "speaker")


class HindsightUnavailable(NotImplementedError):
    """No Hindsight server (or a non-loopback URL was refused). A NotImplementedError so a cell SKIPs, never passes."""


class HindsightError(RuntimeError):
    """Hindsight answered with an error status: loud (the cell is an ERROR), counted against extraction validity."""

    def __init__(self, status: int, path: str, detail: str):
        super().__init__(f"HTTP {status} on {path}: {detail[:200]}")
        self.status, self.path = status, path


def _percentile(xs: "list[float]", q: float) -> float:
    s = sorted(xs)
    if not s:
        return 0.0
    return round(s[min(len(s) - 1, max(0, int(round(q * len(s) + 0.5)) - 1))], 2)


# ── transport and client ─────────────────────────────────────────────────────

def urllib_transport(method: str, url: str, body: "Optional[bytes]", timeout: float) -> "tuple[int, bytes]":
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - loopback only, checked by the client
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise HindsightUnavailable(f"cannot reach Hindsight at {url.split('/v1/')[0]} ({type(exc).__name__}). {INSTALL_HINT}") from exc


class HindsightClient:
    """The slice of Hindsight's HTTP API the arm uses. Loopback-only; every call is timed and counted."""

    def __init__(self, base_url: "Optional[str]" = None, transport: "Optional[Callable[..., tuple[int, bytes]]]" = None,
                 timeout: float = 600.0):
        self.base_url = (base_url or os.environ.get("ZMB_HINDSIGHT_URL") or DEFAULT_URL).rstrip("/")
        host = urllib.parse.urlparse(self.base_url).hostname or ""
        if host not in LOOPBACK_HOSTS:
            raise HindsightUnavailable(f"refusing non-loopback Hindsight URL {self.base_url!r}: the bake-off is loopback-only")
        self._transport = transport or urllib_transport
        self.timeout = timeout
        self.timings: "list[tuple[str, float]]" = []     # (kind, ms) of every call
        self.errors: "list[tuple[str, int]]" = []        # (kind, status) of every non-2xx
        self.non_loopback_requests = 0

    def call(self, kind: str, method: str, path: str, payload: Any = None, params: "Optional[dict]" = None,
             ok: "tuple[int, ...]" = (200, 201, 202)) -> Any:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        if (urllib.parse.urlparse(url).hostname or "") not in LOOPBACK_HOSTS:   # unreachable by construction; counted if not
            self.non_loopback_requests += 1
            raise HindsightUnavailable("non-loopback request refused")
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        t0 = time.monotonic()
        status, raw = self._transport(method, url, body, self.timeout)
        self.timings.append((kind, (time.monotonic() - t0) * 1000.0))
        if status not in ok:
            self.errors.append((kind, status))
            raise HindsightError(status, path, raw.decode("utf-8", "replace"))
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return {}

    # ── the calls ──
    def health(self) -> bool:
        try:
            self.call("health", "GET", "/health")
        except HindsightError:
            return False
        return True

    def put_bank(self, bank: str) -> None:
        self.call("bank", "PUT", f"{API}/{bank}", {})

    def patch_config(self, bank: str, updates: dict) -> None:
        self.call("config", "PATCH", f"{API}/{bank}/config", {"updates": updates})

    def delete_bank(self, bank: str) -> None:
        try:
            self.call("bank", "DELETE", f"{API}/{bank}")
        except HindsightError as exc:
            if exc.status != 404:
                raise

    def retain(self, bank: str, items: "list[dict]") -> dict:
        return self.call("retain", "POST", f"{API}/{bank}/memories", {"items": items, "async": False})

    def recall(self, bank: str, query: str, *, tags: "Optional[list[str]]" = None, tags_match: str = "all_strict",
               max_tokens: int = 1200, budget: str = "low") -> "list[dict]":
        body: "dict[str, Any]" = {"query": query, "max_tokens": max_tokens, "budget": budget}
        if tags:
            body["tags"], body["tags_match"] = tags, tags_match
        return list(self.call("recall", "POST", f"{API}/{bank}/memories/recall", body).get("results") or [])

    def list_units(self, bank: str) -> "list[dict]":
        out: "list[dict]" = []
        offset = 0
        while True:
            page = self.call("list", "GET", f"{API}/{bank}/memories/list", params={"limit": 100, "offset": offset})
            items = list(page.get("items") or [])
            out.extend(items)
            offset += len(items)
            if not items or offset >= int(page.get("total") or 0):
                return out

    def get_document(self, bank: str, doc_id: str) -> dict:
        try:
            return self.call("document", "GET", f"{API}/{bank}/documents/{urllib.parse.quote(doc_id, safe='')}")
        except HindsightError as exc:
            if exc.status == 404:
                return {}
            raise

    def delete_document(self, bank: str, doc_id: str) -> int:
        try:
            r = self.call("delete", "DELETE", f"{API}/{bank}/documents/{urllib.parse.quote(doc_id, safe='')}")
        except HindsightError as exc:
            if exc.status == 404:
                return 0
            raise
        return int(r.get("memory_units_deleted") or 0)

    def consolidate(self, bank: str, scopes: "Optional[list[list[str]]]" = None) -> None:
        self.call("consolidate", "POST", f"{API}/{bank}/consolidate", {"observation_scopes": scopes} if scopes else {})

    def pending_operations(self, bank: str) -> int:
        ops = self.call("operations", "GET", f"{API}/{bank}/operations", params={"limit": 50}).get("operations") or []
        return sum(1 for o in ops if str(o.get("status")) in ("pending", "running", "processing", "in_progress"))


# ── ZOE-LAYER-BEGIN ───────────────────────────────────────────────────────────

#: the ids that own no memory (``MemoryService.is_guest_memory_user`` + the voice daemon)
GUEST_IDS = frozenset({"", "guest", "anonymous", "voice-guest", "voice-daemon"})
_LAB_SALT = "zmb-lab-forget-ledger-secret-0123456789"
_NAME_RX = re.compile(r"[A-Za-z][A-Za-z'\-]*")


def _digest(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:10]


def name_pattern(entity: str) -> "re.Pattern[str]":
    """Whole-word, case-blind: the same anchoring as the tombstone and the ledger."""
    return re.compile(r"(?<!\w)" + re.escape(entity.strip()) + r"(?!\w)", re.IGNORECASE)


@dataclass
class Known:
    """One row the layer knows about (the authority wall reads these, never a vector index)."""
    id: str
    text: str
    cls: str
    status: str = "approved"
    origin: str = ""
    memory_type: str = ""
    contradicts_id: str = ""
    in_hindsight: bool = True       # False = a side-table row (held back / pending / retired): Hindsight has no status for it
    excerpt: str = ""               # provenance (ZMB A3): the owner's own words the row came from, and which turn
    turn_id: str = ""

    def as_ref(self) -> Any:        # the MemoryRef shape ``find_conflict`` reads
        return types.SimpleNamespace(id=self.id, text=self.text,
                                     metadata={"status": self.status, "authority_class": self.cls})


@dataclass
class Plan:
    """What the layer decided for ONE fact. ``kind``: retain | refuse | hold (a disputed candidate) | pending (a person
    candidate) | retire (delete the target's document, then retain ``text`` when given)."""
    kind: str
    text: str = ""
    cls: str = ""
    origin: str = ""
    memory_type: str = ""
    reason: str = ""
    target: "Optional[Known]" = None
    status: str = ""
    release_text: str = ""          # an explicit re-teach: release the forget-ledger entries this text names
    excerpt: str = ""               # provenance (ZMB A3), carried to Hindsight as item metadata + a turn tag
    turn_id: str = ""


class ZoeLayer:
    """The thin layer that no memory system provides. Stateless towards Hindsight: it only decides; the arm applies."""

    def __init__(self, off: "frozenset[str]" = frozenset()):
        bad = sorted(set(off) - set(LAYER_FEATURES))
        if bad:
            raise ValueError(f"unknown Zoe-layer control(s) {', '.join(bad)} (known: {', '.join(LAYER_FEATURES)})")
        from .. import lab_driver
        svc = lab_driver.load_service()
        self.ma, self.quality, self.extractor = svc.memory_authority, svc.memory_quality, svc.memory_extractor
        self.svc = svc
        self.idf = importlib.import_module("identity_facts")
        self.mf = importlib.import_module("memory_forgotten")
        self.persona = importlib.import_module("persona_layer")
        self.off = frozenset(off)
        self.backend = self.mf.MemoryBackend()
        self.known: "dict[str, list[Known]]" = {}
        self.prev_user_text = ""
        self._loop = asyncio.new_event_loop()

    def close(self) -> None:
        if not self._loop.is_closed():
            self._loop.close()

    def reset(self, user: str) -> None:
        self.known[user] = []
        self.prev_user_text = ""
        self.backend = self.mf.MemoryBackend()

    @contextlib.contextmanager
    def _ledger(self):
        """The real ``memory_forgotten`` over this arm's in-memory backend and a lab secret, for ONE operation."""
        old_env, old_backend = os.environ.get(self.mf.SALT_ENV), self.mf._backend
        os.environ[self.mf.SALT_ENV] = _LAB_SALT
        self.mf.set_backend(self.backend)
        try:
            yield
        finally:
            self.mf._backend = old_backend
            self.mf.reset_state()
            if old_env is None:
                os.environ.pop(self.mf.SALT_ENV, None)
            else:
                os.environ[self.mf.SALT_ENV] = old_env

    def _run(self, coro: Any) -> Any:
        return self._loop.run_until_complete(coro)

    # ── the gates (each one mirrors ``MemoryService.ingest``'s order) ──
    def is_guest(self, user: str) -> bool:
        return "guest" not in self.off and (user or "").strip().lower() in GUEST_IDS

    def affect_allowed(self, user: str, modes: "dict[str, Any]") -> bool:
        """The REAL ``MemoryService._affect_allowed`` (unbound: it reads no instance state), with the member-mode lookup scripted."""
        if "affect" in self.off:
            return True
        real = self.persona.load_member_mode

        async def scripted(uid, db=None):
            return modes.get(uid, self.persona.MemberMode())
        self.persona.load_member_mode = scripted
        try:
            return bool(self._run(self.svc.memory_service.MemoryService._affect_allowed(None, user)))
        finally:
            self.persona.load_member_mode = real

    def ledger_add(self, user: str, name: str) -> None:
        if "ledger" in self.off:
            return
        with self._ledger():
            self._run(self.mf.add(user, name, actor=user))

    def ledger_blocks(self, user: str, *texts: str) -> bool:
        if "ledger" in self.off:
            return False
        with self._ledger():
            return any(t and self._run(self.mf.matches(user, t)) for t in texts)

    def ledger_release(self, user: str, text: str) -> int:
        with self._ledger():
            return int(self._run(self.mf.release(user, text)))

    def ledger_rows(self) -> "list[dict]":
        return list(self.backend.rows.values())

    def _known_refs(self, user: str) -> "list[Any]":
        return [k.as_ref() for k in self.known.get(user, [])]

    def _name_candidate(self, user: str, fact: str, anchor: str) -> "Optional[Plan]":
        """The identity wall dropped ``User's name is <X>``; when the user's own turn merely MENTIONS X it is a PERSON to ask about
        (a pending candidate), never a user attribute (``MemoryService._third_person_candidate``)."""
        name = self.idf.asserted_user_name(fact)
        if not name or not anchor or not self.ma.enabled():
            return None
        if self.ma.resolve_write("digest", fact, anchor_text=anchor).rank >= self.ma.DERIVED_RANK:
            return None
        words = {w.lower() for w in _NAME_RX.findall(name)}
        heard = {w.lower() for w in _NAME_RX.findall(anchor)}
        if not words or not words <= heard:
            return None
        return Plan("pending", f"{name.strip()} was mentioned in conversation (who they are is not known).",
                    self.ma.MODEL_FROM_TRANSCRIPT, "digest", status="pending", reason="third-person candidate")

    def _target_for(self, user: str, attr: str) -> "Optional[Known]":
        """The owner's OLDEST approved row about ``attr`` (the original, not a later write)."""
        for k in self.known.get(user, []):
            if k.status == "approved" and self.ma.kind_of(k.text) == attr:
                return k
        return None

    def evidence(self, writer: str, res: Any, text: str, user: str, anchor: str, excerpt: str = "", turn_id: str = "",
                 teach: bool = True) -> "tuple[str, str]":
        """``(source_excerpt, user_turn_id)`` a row written from a user turn carries (ZMB A3): the REAL ``memory_authority.turn_evidence``
        (the fill-in the service's own write boundary applies: a teach is the owner's words, a model fact the sentence that supports it,
        nothing when no user sentence does) with the excerpt PII-scrubbed and cut by the service's ``scrub_source_excerpt``."""
        if "provenance" in self.off:
            return "", ""
        ex, tid = self.ma.turn_evidence(writer, res, text, user_id=user, anchor_text=anchor or None, source_excerpt=excerpt or None,
                                        user_turn_id=turn_id or None, teach=teach)
        return (self.svc.memory_service.scrub_source_excerpt(ex) or "" if ex else ""), tid or ""

    def plan_fact(self, user: str, writer: str, fact: str, *, anchor: str, op: str = "say", attr: str = "",
                  memory_type: str = "", verified: "Optional[bool]" = None, affect_ok: bool = True,
                  excerpt: str = "", turn_id: str = "") -> Plan:
        """The decision for one proposed fact from ``writer`` (a lane label: ``voice_fact``, ``chat_regex``, a model writer ...)."""
        ma = self.ma
        if self.is_guest(user):
            return Plan("refuse", fact, reason="guest: owns no memory")
        if not affect_ok and ma.is_affective(memory_type, None):
            return Plan("refuse", fact, reason="affect: not kept for this identity")
        if ("identity" not in self.off and self.idf.is_automatic_source(writer, owner=user)
                and self.idf.is_user_name_assertion(fact)):
            return self._name_candidate(user, fact, anchor) or Plan("refuse", fact, reason="identity wall")
        reteach = self.mf.is_explicit_reteach(writer, user_id=user, speaker_verified=verified)
        if not reteach and self.ledger_blocks(user, fact, anchor):
            return Plan("refuse", fact, reason="forgotten (ledger)")
        res = ma.resolve_write(writer, fact, anchor_text=anchor or None, user_id=user,
                               speaker_verified=None if "speaker" in self.off else verified)
        ex, tid = self.evidence(writer, res, fact, user, anchor, excerpt, turn_id, teach=(op != "edit"))
        base = dict(cls=res.cls, origin=writer, memory_type=memory_type, release_text=fact if reteach else "", excerpt=ex, turn_id=tid)
        guard = "authority" not in self.off and ma.enabled()
        if op in ("edit", "archive"):
            target = self._target_for(user, attr or ma.kind_of(fact))
            if target is None:
                return Plan("refuse", fact, reason=f"{op}: no approved row about {attr!r}", **base)
            if guard and not ma.may_override(res.power, target.cls):
                return Plan("refuse", fact, reason=f"{op}: {res.cls} may not override {target.cls}", target=target, **base)
            return Plan("retire", fact if op == "edit" else "", target=target, status="superseded" if op == "edit" else "archived", **base)
        hit = ma.find_conflict(fact, self._known_refs(user), res.power) if guard else None
        if not hit and guard and res.cls == ma.USER_UNVERIFIED and ma.is_self_assertion(fact):
            # a speaker the panel did not verify cannot state the OWNER's facts: a candidate the owner confirms, never served (#1895, ZMB I2)
            return Plan("pending", fact, reason="unverified speaker: a candidate", status="pending", **base)
        if hit:
            row, kind = hit
            return Plan("hold", fact, reason=f"contradicts a {kind} row", target=next(k for k in self.known[user] if k.id == row.id),
                        status="disputed", **base)
        return Plan("retain", fact, **base)

    def plan_turn(self, user: str, turn: Turn, *, verified: "Optional[bool]") -> "list[Plan]":
        """A user's own words: the deterministic extractor decides what is a storable fact (``chat_regex`` / ``voice_regex``);
        an explicit teach is the fact itself (``voice_fact``)."""
        if turn.speaker == "owner_taught":
            return [self.plan_fact(user, "voice_fact", turn.text, anchor=turn.text, memory_type=turn.memory_type, verified=verified)]
        writer = "chat_regex" if turn.speaker in ("owner_typed", "third_party", "pasted_email") else "voice_regex"
        cands = self.extractor.extract_candidates(turn.text, turn.assistant_text, prev_user_message=self.prev_user_text or None)
        self.prev_user_text = turn.text
        plans = []
        for n, c in enumerate(cands):
            if "gate" not in self.off and not self.quality.is_storable_fact(c.text)[0]:
                plans.append(Plan("refuse", c.text, reason="write-quality gate"))
                continue
            plans.append(self.plan_fact(user, writer, c.text, anchor=turn.text, memory_type=c.memory_type, verified=verified,
                                        excerpt=" ".join(turn.text.split()), turn_id=f"{_digest(turn.text)}-{n}"))
        return plans

    def plan_raw_turn(self, user: str, turn: Turn, *, verified: "Optional[bool]") -> "list[Plan]":
        """H2: Hindsight's own (concise) extraction reads the user's raw turn, so the layer only GATES it: a question, a denial echo
        or meta-rambling never reaches the model (``is_storable_fact``), and the turn is classed from its speaker."""
        if turn.speaker == "owner_taught":
            return self.plan_turn(user, turn, verified=verified)
        self.prev_user_text = turn.text
        if "gate" not in self.off and not self.quality.is_storable_fact(turn.text)[0]:
            return [Plan("refuse", turn.text, reason="write-quality gate")]
        writer = "chat_regex" if turn.speaker in ("owner_typed", "third_party", "pasted_email") else "voice_regex"
        return [self.plan_fact(user, writer, turn.text, anchor=turn.text, memory_type=turn.memory_type, verified=verified,
                               excerpt=" ".join(turn.text.split()), turn_id=f"{_digest(turn.text)}-0")]

    def remember(self, user: str, k: Known) -> None:
        self.known.setdefault(user, []).append(k)

    def retire_named(self, user: str, name: str, *, status: str = "archived") -> int:
        """Side-table rows naming a forgotten entity are archived with the sweep (nothing about it stays approved/pending/disputed)."""
        pat, n = name_pattern(name), 0
        for k in self.known.get(user, []):
            if k.status in ("approved", "pending", "disputed") and pat.search(k.text):
                k.status, k.text, k.excerpt, n = status, "[forgotten]", "", n + 1       # the side table must not keep what it was told to forget
            elif k.excerpt and pat.search(k.excerpt):
                k.excerpt = self.scrub(k.excerpt, name)
        return n

    def scrub(self, text: str, name: str) -> str:
        """The innocent remainder of a deleted document: its sentences that do not name the forgotten entity."""
        if "scrub" in self.off:
            return ""
        pat = name_pattern(name)
        return " ".join(s for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip() and not pat.search(s))

# ── ZOE-LAYER-END ─────────────────────────────────────────────────────────────


def zoe_layer_lines(path: "Optional[str]" = None) -> int:
    """Non-blank, non-comment lines between the layer markers: decision-rule G3 ('<= 1,000 new lines')."""
    src = open(path or __file__, encoding="utf-8").read().split("\n")
    begin = next(i for i, l in enumerate(src) if l.startswith("# ── ZOE-LAYER-BEGIN"))
    end = next(i for i, l in enumerate(src) if l.startswith("# ── ZOE-LAYER-END"))
    return sum(1 for l in src[begin + 1:end] if l.strip() and not l.strip().startswith("#"))


# ── the arm ──────────────────────────────────────────────────────────────────

class HindsightArm(Arm):
    capabilities = frozenset({"clock", "idle_pass", "identities", "reader", "controls"})
    #: What this arm CANNOT do, and why. A cell that needs one of these SKIPs with the reason (``cells.run_cell`` compares the declared
    #: capabilities), never ERRORs and never passes. Under the rule a skipped HARD cell keeps `hard_cells_all_ran` red: that is the honest
    #: reading of "the engine cannot answer this", and the run record says how many such cells there are.
    LACKS = {
        "conflict_pass": "Hindsight has no nightly implicit-conflict pass: a newer fact does not retire an older one by cue (H2 consolidates "
                         "observations, which is not a supersede with history)",
        "edges": "the people graph (person_relationships) is Zoe's own Postgres graph, not part of the memory engine: Hindsight has no "
                 "authority-labelled relationship edges to write or export",
        "disk": "the store is a Postgres container whose data directory is not readable by the lab: no on-disk residue scan",
    }

    def __init__(self, variant: str = "H1", *, base_url: "Optional[str]" = None,
                 transport: "Optional[Callable[..., tuple[int, bytes]]]" = None,
                 off: "frozenset[str] | set[str]" = frozenset(), layer: "Optional[bool]" = None,
                 keep_banks: bool = False, settle_timeout_s: float = 300.0, settle_poll_s: float = 1.0,
                 rss_probe: "Optional[Callable[[], dict]]" = None, reader_sycophantic: bool = False,
                 clock: "Callable[[], datetime]" = lambda: datetime.now(timezone.utc)):
        if variant not in VARIANTS:
            raise ValueError(f"unknown Hindsight variant {variant!r} (known: {', '.join(VARIANTS)})")
        self.variant = variant
        self.cfg = dict(BANK_CONFIG[variant])
        self.has_layer = (variant != "H0") if layer is None else bool(layer)
        self.off = frozenset(off)
        self.name = variant if not self.off else variant + "-off[" + ",".join(sorted(self.off)) + "]"
        if layer is False and variant != "H0":
            self.name = variant + "-nolayer"
        self.client = HindsightClient(base_url, transport)
        self.layer: "Optional[ZoeLayer]" = ZoeLayer(self.off) if self.has_layer else None
        self.keep_banks, self.settle_timeout_s, self.rss_probe = keep_banks, settle_timeout_s, rss_probe
        self.settle_poll_s = settle_poll_s
        self.sycophantic = reader_sycophantic
        self._now = clock
        self._user = ""
        self._banks: "set[str]" = set()
        self._seq = 0
        self._vclock = 0.0
        self._refused = 0
        self.retain_calls = 0
        self.retain_failures = 0
        self.retain_ms: "list[float]" = []
        self.recall_ms: "list[float]" = []

    # ── naming ──
    def bank_for(self, uid: str) -> str:
        return f"zmb-{self.variant.lower()}-{uid}".lower()

    def _ensure_bank(self, uid: str) -> str:
        bank = self.bank_for(uid)
        if bank not in self._banks:
            self.client.delete_bank(bank)          # a leftover of a crashed run: never inherit its rows
            self.client.put_bank(bank)
            self.client.patch_config(bank, self.cfg)
            self._banks.add(bank)
        return bank

    # ── lifecycle ──
    def reset(self, user_id: str) -> None:
        if not DEMO_USER_RE.match(user_id or ""):
            raise ValueError(f"refusing non-demo identity {user_id!r} (must match {DEMO_USER_RE.pattern})")
        for b in list(self._banks):                 # one live bank per cell: the previous cell's is dropped, not accumulated
            if not self.keep_banks:
                self.client.delete_bank(b)
            self._banks.discard(b)
        self._user, self._seq, self._vclock, self._refused = user_id, 0, 0.0, 0
        if self.layer:
            self.layer.reset(user_id)
            for _label, (uid, _m) in IDENTITIES.items():
                self.layer.reset(uid)
        self._ensure_bank(user_id)

    def close(self) -> None:
        if not self.keep_banks:
            for b in list(self._banks):
                with contextlib.suppress(HindsightUnavailable, HindsightError):
                    self.client.delete_bank(b)
                self._banks.discard(b)
        if self.layer:
            self.layer.close()

    def advance_clock(self, seconds: float) -> None:
        """Virtual: the Zoe layer's forget ledger is permanent (no TTL to expire) and Hindsight has no clocked fence, so there is
        nothing for a fake clock to move. The REAL t+6 min check (wait, replay, verify) is the bake-off driver's forgetting probe."""
        self._vclock += float(seconds)

    # ── writing ──
    def _tags(self, uid: str, plan: Plan, speaker: str) -> "list[str]":
        tags = [f"user:{uid}", f"class:{plan.cls}", f"origin:{plan.origin}"]
        tags += [f"lane:{speaker}"] if speaker else []
        return tags + [f"turn:{plan.turn_id}"] if plan.turn_id else tags      # the turn id also rides as a tag: a metadata-less server still says which turn

    def _retain(self, uid: str, text: str, tags: "list[str]", memory_type: str, day_offset: int = 0, excerpt: str = "",
                turn_id: str = "") -> str:
        self._seq += 1
        doc = f"d{self._seq:04d}-{_digest(uid, text)}"
        item: "dict[str, Any]" = {"content": text, "document_id": doc, "context": "the user is speaking"}
        if tags:
            item["tags"] = tags
        meta = {"memory_type": memory_type, "source_excerpt": excerpt, "user_turn_id": turn_id}      # provenance rides as item metadata (ZMB A3)
        meta = {k: v for k, v in meta.items() if v}
        if meta:
            item["metadata"] = meta
        if day_offset:
            item["timestamp"] = (self._now() - timedelta(days=day_offset)).isoformat(timespec="seconds")
        if self.cfg.get("enable_observations") and not self.cfg.get("enable_auto_consolidation") and tags:
            item["observation_scopes"] = [[t for t in tags if t.startswith(("user:", "class:"))]]   # the fence
        self.retain_calls += 1
        t0 = time.monotonic()
        try:
            self.client.retain(self.bank_for(uid), [item])
        except HindsightError:
            self.retain_failures += 1
            raise
        finally:
            self.retain_ms.append((time.monotonic() - t0) * 1000.0)
        return doc

    def _apply(self, uid: str, plan: Plan, turn: Turn, rep: IngestReport) -> None:
        L = self.layer
        if plan.kind == "refuse":
            rep.refused += 1
            rep.notes.append(plan.reason)
            return
        if plan.kind in ("hold", "pending"):
            rep.refused += 1 if plan.kind == "hold" else 0
            L.remember(uid, Known(f"zoe-{plan.kind}-{_digest(uid, plan.text)}", plan.text, plan.cls, plan.status, plan.origin,
                                  plan.memory_type, plan.target.id if plan.target else "", in_hindsight=False,
                                  excerpt=plan.excerpt, turn_id=plan.turn_id))
            rep.notes.append(plan.reason)
            return
        if plan.kind == "retire":
            t = plan.target
            self.client.delete_document(self.bank_for(uid), t.id)
            t.status, t.in_hindsight = plan.status, False
            rep.retired += 1
            if not plan.text:
                return
        doc = self._retain(uid, plan.text, self._tags(uid, plan, turn.speaker), plan.memory_type, turn.day_offset, plan.excerpt, plan.turn_id)
        L.remember(uid, Known(doc, plan.text, plan.cls, "approved", plan.origin, plan.memory_type, excerpt=plan.excerpt, turn_id=plan.turn_id))
        if plan.release_text:
            L.ledger_release(uid, plan.release_text)
        rep.written += 1

    def _one_raw(self, uid: str, t: Turn, rep: IngestReport) -> None:
        """H0 (no layer): whatever a lane hands over goes straight in. A model writer's ``edit`` / ``archive`` has no native
        equivalent in Hindsight (no edit-by-attribute), so it degrades to 'retain the proposal': newest evidence."""
        if t.speaker == "assistant":
            rep.notes.append("assistant turn: never mined")
            return
        texts = list(t.proposes) if t.speaker == "system_writer" else [t.text]
        for text in texts:
            self._retain(uid, text, [], t.memory_type, t.day_offset)
            rep.written += 1
        if t.speaker == "system_writer" and t.op in ("edit", "archive"):
            rep.notes.append(f"{t.op}: no native equivalent, retained as a new fact")

    def _one(self, uid: str, t: Turn, rep: IngestReport, *, affect_ok: bool = True) -> None:
        if not self.layer:
            return self._one_raw(uid, t, rep)
        L = self.layer
        if t.speaker == "assistant":
            rep.notes.append("assistant turn: never mined")
            return
        verified = {"owner_voice_verified": True, "panel_unverified": False}.get(t.speaker)
        if t.speaker == "system_writer":
            plans = [L.plan_fact(uid, t.writer, f, anchor=t.text, op=t.op, attr=t.attr, memory_type=t.memory_type,
                                 affect_ok=affect_ok) for f in t.proposes]
        elif self.variant == "H2":
            plans = L.plan_raw_turn(uid, t, verified=verified)
        else:
            plans = L.plan_turn(uid, t, verified=verified)
        for p in plans:
            self._apply(uid, p, t, rep)

    def _settle(self, uid: str) -> None:
        """Observations are background work (H0 auto-consolidation, H2's explicit batch): wait until the bank is idle, bounded."""
        if not self.cfg.get("enable_observations"):
            return
        bank, deadline, idle = self.bank_for(uid), time.monotonic() + self.settle_timeout_s, 0
        while time.monotonic() < deadline:
            idle = idle + 1 if self.client.pending_operations(bank) == 0 else 0
            if idle >= 2:
                return
            time.sleep(self.settle_poll_s)
        raise HindsightError(504, "operations", f"bank {bank} still busy after {self.settle_timeout_s:.0f}s")

    def _consolidate_scoped(self, uid: str) -> None:
        """H2: consolidate per authority scope, so a fact of one class can never update an observation of another
        (``all_strict`` tag isolation, retain.mdx:188)."""
        if not (self.cfg.get("enable_observations") and not self.cfg.get("enable_auto_consolidation")):
            return
        classes = sorted({k.cls for k in self.layer.known.get(uid, []) if k.in_hindsight and k.status == "approved"}) \
            if self.layer else []
        if classes:
            self.client.consolidate(self.bank_for(uid), [[f"user:{uid}", f"class:{c}"] for c in classes])

    def ingest(self, turns: "list[Turn]") -> IngestReport:
        rep = IngestReport()
        for t in turns:
            rep.turns += 1
            self._one(self._user, t, rep)
        self._finish(self._user)
        self._refused += rep.refused
        return rep

    def _finish(self, uid: str) -> None:
        if self.bank_for(uid) not in self._banks:      # a guest has no bank: nothing was written, nothing to settle
            return
        self._consolidate_scoped(uid)
        self._settle(uid)

    def run_idle_pass(self, transcript: str, proposes: "list[str]") -> "dict[str, Any]":
        """The nightly pass: the model's extraction over the day's transcript (scripted as ``proposes``) handed to the store
        under the digest's own label, anchored to the transcript. Hindsight has no digest of its own to run."""
        rep = IngestReport()
        self._one(self._user, Turn(text=transcript, speaker="system_writer", writer="digest", proposes=tuple(proposes)), rep)
        self._finish(self._user)
        self._refused += rep.refused
        return {"retained": rep.written, "refused": rep.refused, "retired": rep.retired}

    def ingest_as(self, identity: str, turns: "list[Turn]") -> IngestReport:
        if identity not in IDENTITIES:
            raise ValueError(f"unknown identity {identity!r} (known: {', '.join(IDENTITIES)})")
        uid = IDENTITIES[identity][0]
        rep = IngestReport()
        ok = True
        if self.layer:
            persona = self.layer.persona
            modes = {u: persona.MemberMode(mode=m, minor=mn) for _l, (u, (m, mn)) in IDENTITIES.items()}
            ok = self.layer.affect_allowed(uid, modes)
            if not self.layer.is_guest(uid):
                self._ensure_bank(uid)
        else:
            self._ensure_bank(uid)
        for t in turns:
            rep.turns += 1
            self._one(uid, t, rep, affect_ok=ok)
        self._finish(uid)
        return rep

    # ── reading ──
    def _unit_row(self, u: dict, side: "dict[str, Known]", by_id: "Optional[dict[str, dict]]" = None) -> "dict[str, Any]":
        tags = [str(x) for x in (u.get("tags") or [])]
        tag = lambda p: next((x[len(p):] for x in tags if x.startswith(p)), "")  # noqa: E731
        k = side.get(str(u.get("document_id") or ""))
        meta = dict(u.get("metadata") or {})
        for src_id in (u.get("source_memory_ids") or []) if by_id else []:      # an observation is derived: its provenance is its source fact's
            src = (by_id.get(str(src_id)) or {}).get("metadata") or {}
            if src.get("source_excerpt") and not meta.get("source_excerpt"):
                meta["source_excerpt"], meta["user_turn_id"] = src["source_excerpt"], src.get("user_turn_id", "")
        status = {"valid": "approved", "": "approved"}.get(str(u.get("state") or ""), "superseded")
        if k and k.status != "approved":
            status = k.status
        return {"id": str(u.get("id") or ""), "text": str(u.get("text") or ""), "status": status,
                "authority_class": tag("class:"), "origin": tag("origin:"), "contradicts_id": "",
                "entity_type": "", "memory_type": str(meta.get("memory_type") or (k.memory_type if k else "")),
                "user_id": tag("user:"),
                # provenance (ZMB A3): what the store HOLDS (Hindsight's own metadata and tags), never what the side table remembers
                "source_excerpt": str(meta.get("source_excerpt") or ""), "user_turn_id": str(meta.get("user_turn_id") or tag("turn:"))}

    def _rows_for(self, uid: str) -> "list[dict[str, Any]]":
        if self.layer and self.layer.is_guest(uid):
            return []
        side = {k.id: k for k in (self.layer.known.get(uid, []) if self.layer else [])}
        units = self.client.list_units(self.bank_for(uid)) if self.bank_for(uid) in self._banks else []
        by_id = {str(u.get("id")): u for u in units}
        rows = [self._unit_row(u, side, by_id) for u in units]
        first_unit = {}                                  # a held row links to the row the owner can see (a unit id), not to the document id
        for u in units:
            first_unit.setdefault(str(u.get("document_id") or ""), str(u.get("id") or ""))
        held = [{"id": k.id, "text": k.text, "status": k.status, "authority_class": k.cls, "origin": k.origin,
                 "contradicts_id": first_unit.get(k.contradicts_id, k.contradicts_id), "entity_type": "", "memory_type": k.memory_type,
                 "user_id": uid, "source_excerpt": k.excerpt, "user_turn_id": k.turn_id}
                for k in (self.layer.known.get(uid, []) if self.layer else []) if not k.in_hindsight]
        return rows + held

    def stats(self) -> "dict[str, Any]":
        rows = self._rows_for(self._user)
        counts: "dict[str, int]" = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return {"rows": rows, "counts": counts, "writes_refused": self._refused, "row_keys": list(ROW_KEYS),
                "measure": self.measure()}

    def stats_as(self, identity: str) -> "dict[str, Any]":
        return {"rows": self._rows_for(IDENTITIES[identity][0]), "counts": {}, "writes_refused": 0}

    def measure(self) -> "dict[str, Any]":
        """Counters the bake-off driver reads for G0/G1: retain validity, latencies, egress (client side), RSS if probed."""
        calls = max(self.retain_calls, 0)
        out: "dict[str, Any]" = {
            "retain_calls": calls, "retain_failures": self.retain_failures,
            "retain_ok_rate": round(1 - self.retain_failures / calls, 4) if calls else None,
            "retain_p50_ms": _percentile(self.retain_ms, 0.5), "retain_p95_ms": _percentile(self.retain_ms, 0.95),
            "recall_n": len(self.recall_ms), "recall_p50_ms": _percentile(self.recall_ms, 0.5),
            "recall_p95_ms": _percentile(self.recall_ms, 0.95),
            "http_calls": len(self.client.timings), "http_errors": len(self.client.errors),
            "egress": {"base_url": self.client.base_url, "non_loopback_requests": self.client.non_loopback_requests},
        }
        if self.rss_probe:
            with contextlib.suppress(Exception):
                out["rss"] = self.rss_probe()
        return out

    def recall(self, query: str, k: int = 10) -> "list[dict[str, Any]]":
        uid = self._user
        if self.layer and self.layer.is_guest(uid):
            return []
        t0 = time.monotonic()
        try:
            results = self.client.recall(self.bank_for(uid), query, tags=[f"user:{uid}"] if self.layer else None)
        finally:
            self.recall_ms.append((time.monotonic() - t0) * 1000.0)
        side = {x.id: x for x in (self.layer.known.get(uid, []) if self.layer else [])}
        rows = [self._unit_row({**r, "state": "valid"}, side) for r in results]
        if self.layer:
            ma = self.layer.ma
            rows = [r for r in rows if r["authority_class"] not in ("user_unverified", "quoted_third_party")]
            rows.sort(key=lambda r: -ma.RANK.get(r["authority_class"], 0))     # stable: Hindsight's order within a class
        return rows[:k]

    def answer(self, query: str, k: int = 5) -> str:
        return reader_answer(self.recall(query, k), query, sycophantic=self.sycophantic)

    # ── forgetting ──
    def _sweep(self, uid: str, entity: str) -> "tuple[int, list[tuple[str, str]]]":
        """Delete every document naming ``entity`` (cascade to its memories and, natively, the observations built on them).
        Returns (documents deleted, [(doc id, source text)])."""
        bank, pat = self.bank_for(uid), name_pattern(entity)
        units = self.client.list_units(bank)
        docs = {str(u.get("document_id")) for u in units if u.get("document_id")}
        hit: "list[tuple[str, str]]" = []
        for d in sorted(docs):
            text = str((self.client.get_document(bank, d) or {}).get("original_text") or "")
            names = any(pat.search(f"{u.get('text') or ''} {u.get('entities') or ''} {(u.get('metadata') or {}).get('source_excerpt') or ''}")
                        for u in units if u.get("document_id") == d)          # the stored excerpt is the owner's words: it can name her too
            if names or pat.search(text):
                hit.append((d, text))
        for d, _t in hit:
            self.client.delete_document(bank, d)
        return len(hit), hit

    def forget(self, entity: str) -> str:
        uid = self._user
        if self.layer:
            self.layer.ledger_add(uid, entity)          # the fence first: nothing may write the name while the sweep runs
        n, hit = self._sweep(uid, entity)
        if self.layer:
            self.layer.retire_named(uid, entity)
            for d, text in hit:                          # the innocent remainder of a deleted document is kept, scrubbed
                rest = self.layer.scrub(text, entity)
                k = next((x for x in self.layer.known.get(uid, []) if x.id == d), None)
                if rest and k is not None:
                    ex = self.layer.scrub(k.excerpt, entity) if k.excerpt else ""      # the excerpt keeps only what does not name her
                    tags = [f"user:{uid}", f"class:{k.cls}", f"origin:{k.origin}"] + ([f"turn:{k.turn_id}"] if k.turn_id else [])
                    doc = self._retain(uid, rest, tags, k.memory_type, 0, ex, k.turn_id)
                    self.layer.remember(uid, Known(doc, rest, k.cls, "approved", k.origin, k.memory_type, excerpt=ex, turn_id=k.turn_id))
        self._finish(uid)
        return f"Forgot everything about {entity}: {n} document(s) removed."

    def run_conflict_pass(self) -> "dict[str, Any]":
        raise NotImplementedError(self.LACKS["conflict_pass"])

    def write_edge(self, a: str, b: str, rel: str, group: str, authority: str, origin: str) -> None:
        raise NotImplementedError(self.LACKS["edges"])

    def edges(self) -> "list[dict[str, Any]]":
        raise NotImplementedError(self.LACKS["edges"])

    def hard_delete(self) -> int:
        raise NotImplementedError(self.LACKS["disk"])

    def disk_residue(self, tokens: "list[str]") -> "dict[str, Any]":
        raise NotImplementedError(self.LACKS["disk"])

    def as_of(self, query: str, ts: str) -> "list[dict[str, Any]]":
        raise NotImplementedError("Hindsight 0.10.2 has no belief-time read: recall's temporal_window only RANKS memories by their "
                                  "event dates ('this ranks, it does not filter'), so 'what the store believed at ts' is not answerable")
