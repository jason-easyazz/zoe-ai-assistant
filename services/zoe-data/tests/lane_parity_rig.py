"""Lane-parity rig: the SAME utterance through the REAL chat path and the REAL voice path, in one process.

Not a test module (no ``test_`` prefix) - ``tests/test_voice_lane_parity.py`` and ``scripts/perf/lane_parity.py`` drive it.

What is real: ``routers.chat.chat_stream_generator`` (the typed lane) and ``routers.voice_tts.voice_command`` (the spoken lane, called
with ``text`` exactly as the Pi daemon posts it after STT), plus everything they call in front of the brain - ``fast_tiers.resolve``
and its tiers, the intent router, the contacts handlers, the provenance / correction / hold tiers, the memory service (a throwaway
palace; ``tests/conftest.py`` pins it).

What is faked, and why (so the rig can never write to a live store):
  * the database - an in-memory fake that understands the few tables these turns touch (``people``, ``chat_messages``,
    ``chat_feedback``) and RECORDS every statement it does not understand (``Rig.db.unhandled``), so a silent gap shows up;
  * the brain - one stub at ``brain_dispatch.brain_streaming`` (both lanes call it) that records what the lane would have sent
    and answers a fixed sentinel, so "reached the brain" is a measured fact and no model is loaded;
  * TTS, broadcasts, the post-turn memory passes (an LLM job in production), panel identity lookups.

A turn's ``Outcome`` is the lane's observable result: the reply, the stage that produced it (``tier:<name>`` for the shared
fast_tiers core, ``voice:<stage>`` / ``chat:<stage>`` for a lane-private stage, ``brain`` when the turn fell through) and the writes it caused.
"""
from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional

BRAIN_SENTINEL = "<<BRAIN>>"

#: the conversation flags the LIVE service runs with (read from its env on 2026-10-10, values reduced to on/off), so the rig measures
#: the lane the household actually hears. Names only; nothing is written anywhere.
LIVE_FLAGS = {
    "ZOE_EXPERT_ENABLED": "1", "ZOE_EXPERT_MODE": "active", "ZOE_EXPERT_ALLOW_WRITES": "1", "ZOE_EXPERT_ACTIVE_DOMAINS": "people,memory",
    "ZOE_CORRECTION_APPLY": "1", "ZOE_VERIFY_ON_CHALLENGE": "1", "ZOE_CONTACTS_CONVERSATIONAL": "1",
    "ZOE_HOLD_THE_FACT": "enforce", "ZOE_SELF_MODEL": "enforce", "ZOE_ROUTER_ENABLED": "1", "ZOE_INTENT_ROUTER_GATE": "0",
    # the seam is real and the transport under it is the stub: wire 2, not streaming, so one stub sees every brain turn
    "ZOE_BRAIN_BACKEND": "flue", "ZOE_FLUE_WIRE": "2", "ZOE_FLUE_STREAM_ENABLED": "0",
}


class MiniPatch:
    """The slice of pytest's ``monkeypatch`` the rig uses (``setattr`` incl. dotted string targets, ``setenv``, ``delenv``, ``undo``), so
    ``scripts/perf/lane_parity.py`` runs on the service interpreter, which has no pytest."""

    _MISSING = object()

    def __init__(self) -> None:
        self._undo: list = []

    def setattr(self, target, name_or_value, value=_MISSING, raising: bool = True):
        import importlib

        if value is self._MISSING:                      # setattr("pkg.mod.attr", value)
            path, value = str(target), name_or_value
            mod_path, _, name = path.rpartition(".")
            target = importlib.import_module(mod_path)
        else:
            name = name_or_value
        old = getattr(target, name, self._MISSING)
        if old is self._MISSING and raising:
            raise AttributeError(name)
        self._undo.append(("attr", target, name, old))
        setattr(target, name, value)

    def setenv(self, key, value):
        import os

        self._undo.append(("env", key, os.environ.get(key)))
        os.environ[key] = str(value)

    def delenv(self, key, raising: bool = True):
        import os

        if key in os.environ:
            self._undo.append(("env", key, os.environ.get(key)))
            del os.environ[key]

    def undo(self) -> None:
        import os

        for item in reversed(self._undo):
            if item[0] == "attr":
                _, target, name, old = item
                if old is self._MISSING:
                    delattr(target, name)
                else:
                    setattr(target, name, old)
            else:
                _, key, old = item
                if old is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old
        self._undo.clear()


class _Call:
    def __init__(self, awaitable):
        self._awaitable = awaitable

    def __await__(self):
        return self._awaitable.__await__()

    async def __aenter__(self):
        return await self._awaitable

    async def __aexit__(self, *exc):
        return False


class _Cur:
    def __init__(self, rows=()):
        self._rows = list(rows)

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)


class FakeDB:
    """Just enough SQL for a conversational turn. Anything else is recorded in ``unhandled`` and answers empty."""

    def __init__(self) -> None:
        self.people: list[dict] = []
        self.messages: list[dict] = []
        self.feedback: list[dict] = []
        self.unhandled: list[str] = []
        self.statements: list[tuple[str, tuple]] = []

    def add_message(self, session_id, role, content, user_id=None) -> str:
        mid = f"m{len(self.messages)}"
        self.messages.append({"id": mid, "session_id": session_id, "role": role, "content": content, "user_id": user_id})
        return mid

    # asyncpg-style helpers some call sites use directly
    async def fetch(self, sql, *args):
        return await (await self.execute(sql, args)).fetchall()

    async def fetchrow(self, sql, *args):
        return await (await self.execute(sql, args)).fetchone()

    async def fetchval(self, sql, *args):
        row = await self.fetchrow(sql, *args)
        return row[0] if row else None

    async def commit(self):
        return None

    def execute(self, sql, *args):
        """Awaitable AND async-context-manager, like the production compat wrapper (``await db.execute(..)`` and ``async with
        db.execute(..) as cur`` are both used)."""
        return _Call(self._execute(sql, *args))

    async def _execute(self, sql, *args):
        # aiosqlite style execute(sql, (a, b)) and asyncpg style execute(sql, a, b) are both used across the codebase
        params = tuple(args[0]) if len(args) == 1 and isinstance(args[0], (tuple, list)) else tuple(args)
        self.statements.append((sql, params))
        s = " ".join(str(sql).split())
        low = s.lower()
        if low.startswith("insert into users") or low.startswith("insert into chat_sessions"):
            return _Cur()
        if low.startswith("insert into people"):
            self.people.append({"id": f"p{len(self.people)}", "name": params[2], "relationship": params[3], "deleted": 0})
            return _Cur()
        if low.startswith("update people set name"):
            for p in self.people:
                if p["id"] == params[3] and not p.get("deleted"):
                    p["name"], p["relationship"] = params[0], params[1] or p.get("relationship")
            return _Cur()
        if "lower(name) = lower(?)" in low:
            return _Cur([p for p in self.people if p["name"].lower() == str(params[1]).lower()][:1])
        if "lower(name) like lower(?)" in low:
            pre = str(params[1]).rstrip("%").lower()
            return _Cur([p for p in self.people if p["name"].lower().startswith(pre)])
        if low.startswith("select") and "from people" in low:
            if "lower(trim(relationship)) in" in low:
                aliases = set(params[:-1])
                return _Cur(sorted((p for p in self.people if (p.get("relationship") or "").strip().lower() in aliases),
                                   key=lambda p: p["name"]))
            if "name ilike" in low:
                pat = str(params[0]).strip("%").replace("\\", "").lower()
                return _Cur([p for p in self.people if pat in p["name"].lower()])
            return _Cur(list(self.people))
        if low.startswith("select id from chat_messages") and "role = 'assistant'" in low:
            sid = params[0] if params else ""
            rows = [m for m in self.messages if m["session_id"] == sid and m["role"] == "assistant"][::-1][:1]
            return _Cur([(m["id"],) for m in rows])
        if low.startswith("select") and "from chat_messages" in low and "session_id" in low:
            sid = params[0] if params else ""
            rows = [m for m in self.messages if m["session_id"] == sid][::-1][:6]
            return _Cur([(m["role"], m["content"]) for m in rows])
        if low.startswith("insert into chat_feedback"):
            self.feedback.append({"params": params})
            return _Cur()
        self.unhandled.append(s[:120])
        return _Cur()


@dataclass
class Outcome:
    lane: str
    text: str
    reply: str = ""
    stage: str = ""                 # tier:<tier>:<intent> | voice:<stage> | chat:<stage> | brain
    brain_reached: bool = False
    brain_message: str = ""
    brain_kwargs: dict = field(default_factory=dict)
    pending_confirmation: bool = False
    people: list = field(default_factory=list)
    feedback_rows: int = 0

    def row(self) -> dict:
        return {"lane": self.lane, "stage": self.stage, "reply": self.reply, "brain": self.brain_reached}


def _sse_text(lines: list) -> str:
    """The assistant text out of an AG-UI SSE stream (chunk / content deltas)."""
    out = []
    for raw in lines:
        for part in str(raw).splitlines():
            if not part.startswith("data:"):
                continue
            try:
                ev = json.loads(part[5:].strip())
            except Exception:  # noqa: BLE001
                continue
            if ev.get("type") in ("TEXT_MESSAGE_CHUNK", "TEXT_MESSAGE_CONTENT") and ev.get("delta"):
                out.append(ev["delta"])
    return "".join(out)


class Rig:
    """``rig = Rig().install(monkeypatch)``; then ``await rig.chat(text)`` / ``await rig.voice(text)``."""

    def __init__(self, user: str = "demo_lane_parity") -> None:
        self.user = user
        self.db = FakeDB()
        self.chat_session = "parity-chat"
        self.voice_session = "parity-voice"
        self.panel = "parity-panel"
        self.brain_calls: list = []
        self.seam_calls = 0
        self.seam_voice_mode = False
        self.brain_reply = BRAIN_SENTINEL      # what the stubbed model says; a cell sets it to a world-fact answer
        self.searches: list = []
        self.exact_turns: list = []
        self.serve_on_brain = False
        self._last_tier: Optional[Any] = None

    # ── install ──────────────────────────────────────────────────────────────
    def install(self, monkeypatch, flags: Optional[dict] = None) -> "Rig":
        for k, v in {**LIVE_FLAGS, **(flags or {})}.items():
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, str(v))
        import brain_dispatch
        import zoe_flue_client
        import database
        import db_pool
        import fast_tiers
        import intent_router
        from routers import chat as chat_mod
        from routers import voice_tts as vt

        db = self.db

        @contextlib.asynccontextmanager
        async def ctx(*_a, **_k):
            yield db

        async def get_db_gen(*_a, **_k):
            yield db

        for mod in (database, db_pool):
            monkeypatch.setattr(mod, "get_db_ctx", ctx, raising=False)
            monkeypatch.setattr(mod, "get_db", get_db_gen, raising=False)

        async def noop(*_a, **_k):
            return None

        monkeypatch.setattr(intent_router, "_notify_ui_channel", noop, raising=False)

        # the stored transcript both lanes write (verify-on-challenge and "why did you say that" read it)
        async def save_message(session_id, role, content, *a, user_id=None, **k):
            db.add_message(session_id, role, content, user_id)
            return True          # chat reads False as "the save failed" and takes its recovery path

        monkeypatch.setattr(chat_mod, "_save_chat_message", save_message)
        monkeypatch.setattr(chat_mod, "_ensure_user_and_chat_session", noop)
        monkeypatch.setattr(chat_mod, "_record_run_state", noop)
        monkeypatch.setattr(chat_mod, "_persist_memory_candidates", noop)
        monkeypatch.setattr(chat_mod, "chat_inject_background", noop)
        monkeypatch.setattr(chat_mod, "_check_frustration", lambda *a, **k: None)

        # the brain: the REAL dispatch and the REAL Flue seam (verify-on-challenge, recall, offer and identity blocks) run;
        # only the transport underneath is a stub, so "what would the sidecar have been sent" is a measured fact for both lanes
        rig = self
        real_stream = zoe_flue_client.run_flue_brain_streaming

        async def counted_stream(message, session_id, user_id, **kwargs):
            rig.seam_calls += 1
            rig.seam_voice_mode = bool(kwargs.get("voice_mode"))
            async for d in real_stream(message, session_id, user_id, **kwargs):
                yield d

        monkeypatch.setattr(zoe_flue_client, "run_flue_brain_streaming", counted_stream)

        async def wire(session_id, payload, **_k):
            body = json.loads(payload.decode())
            rig.brain_calls.append({"message": body.get("body", body.get("message", "")), "session_id": session_id})
            if rig.serve_on_brain:
                await rig.serve_notes()
            yield rig.brain_reply

        monkeypatch.setattr(zoe_flue_client, "_run_turn_aggregated_wire2", wire)

        async def fake_search(query, provider_budget_s=7.4):
            rig.searches.append(query)
            return {"status": "results", "domains": ["example.org"], "results": [
                {"domain": "example.org", "title": "result", "snippet": "A reference page about the question."}]}

        monkeypatch.setattr("verify_on_challenge._search", fake_search)

        import exact_words

        real_note = exact_words.note_turn

        def spy_note(uid, message, *a, **k):
            rig.exact_turns.append(message)
            return real_note(uid, message, *a, **k)

        monkeypatch.setattr(exact_words, "note_turn", spy_note)

        async def one_shot(*a, **k):
            rig.brain_calls.append({"message": a[0] if a else "", "oneshot": True})
            return BRAIN_SENTINEL

        monkeypatch.setattr(brain_dispatch, "brain_oneshot", one_shot)
        monkeypatch.setattr(chat_mod, "_brain_oneshot", one_shot)

        async def no_hybrid(*_a, **_k):
            return {"attempted": False}

        monkeypatch.setattr(chat_mod, "_run_chat_pi_hybrid_lane", no_hybrid)

        # the shared core: remember which tier answered, for BOTH lanes
        real_resolve = fast_tiers.resolve

        async def spy_resolve(text, user_id, session_id, **kwargs):
            res = await real_resolve(text, user_id, session_id, **kwargs)
            rig._last_tier = res
            return res

        monkeypatch.setattr(fast_tiers, "resolve", spy_resolve)

        # voice-private plumbing
        class _Audio:
            body = b"x"
            media_type = "audio/wav"

        async def synth(*_a, **_k):
            return _Audio()

        monkeypatch.setattr(vt, "synthesize", synth)
        monkeypatch.setattr(vt, "_resolve_recent_panel_session_user", noop)
        monkeypatch.setattr(vt, "_resolve_panel_default_user", noop)
        monkeypatch.setattr(vt, "_touch_panel_session", noop)
        monkeypatch.setattr(vt, "_run_voice_memory_passes", noop)
        monkeypatch.setattr(vt, "_voice_brain_kwargs", self._voice_brain_kwargs)

        async def voice_save(session_id, user_text, reply, user_id, speaker_verified=None):
            if user_text:
                db.add_message(session_id, "user", user_text, user_id)
            if reply:
                db.add_message(session_id, "assistant", reply, user_id)

        monkeypatch.setattr(vt, "_schedule_voice_chat_save", voice_save)
        vt._PENDING_CONFIRMATIONS.clear()
        vt._VOICE_SESSIONS.clear()
        return self

    @staticmethod
    async def _voice_brain_kwargs(session_id, user_id, text, router_decision):
        from routers.voice_tts import _VoicePacket

        return {}, _VoicePacket("lazy")

    async def serve_notes(self) -> None:
        """What the recall packet does live just before the model answers: record the user's notes it handed over, so the reply that
        follows is attributable to them ("why did you say that?"). The brain is a stub here, so the rig does the handing over."""
        import memory_provenance as mp
        from memory_service import get_memory_service

        rows = await get_memory_service().list_by_status(user_id=self.user, status="approved", limit=50)
        mp.note_served(self.user, [(r.id, r.text) for r in rows])

    # ── drivers ──────────────────────────────────────────────────────────────
    def _outcome(self, lane: str, text: str, reply: str, *, stage: str, before_brain: int, before_seam: int,
                 pending: bool = False) -> Outcome:
        seam = self.seam_calls > before_seam
        sent = len(self.brain_calls) > before_brain
        out = Outcome(lane=lane, text=text, reply=reply, stage=stage, brain_reached=seam, pending_confirmation=pending,
                      people=[dict(p) for p in self.db.people], feedback_rows=len(self.db.feedback))
        if seam:
            out.stage = "brain" if sent else "seam_reply"     # seam_reply: the seam answered without asking the model
            if sent:
                out.brain_message = self.brain_calls[-1].get("message", "")
        return out

    async def chat(self, text: str) -> Outcome:
        from routers import chat as chat_mod

        self._last_tier = None
        before, before_seam = len(self.brain_calls), self.seam_calls
        user = {"user_id": self.user, "role": "user", "username": self.user}
        lines = []
        async for line in chat_mod.chat_stream_generator(text, self.chat_session, user, channel="chat"):
            lines.append(line)
        reply = _sse_text(lines).replace(BRAIN_SENTINEL, "").strip()
        t = self._last_tier
        if t is not None and getattr(t, "reply", "") and reply == str(t.reply).strip():
            stage = f"tier:{getattr(t, 'tier', '') or 'tier1.5'}:{getattr(t, 'intent', '')}"
        else:
            stage = "chat:intent"
        return self._outcome("chat", text, reply, stage=stage, before_brain=before, before_seam=before_seam)

    async def voice(self, text: str) -> Outcome:
        from routers import voice_tts as vt

        self._last_tier = None
        before, before_seam = len(self.brain_calls), self.seam_calls
        payload = {"text": text, "panel_id": self.panel, "session_id": self.voice_session, "identified_user_id": self.user}
        caller = {"source": "session", "user_id": self.user, "role": "user", "panel_id": self.panel}
        res = await vt.voice_command(payload, caller, stream=False, db=self.db)
        res = res if isinstance(res, dict) else {}
        reply = str(res.get("reply") or "").replace(BRAIN_SENTINEL, "").strip()
        t = self._last_tier
        if t is not None and getattr(t, "reply", "") and reply == str(t.reply).strip():
            stage = f"tier:{getattr(t, 'tier', '') or 'tier1.5'}:{getattr(t, 'intent', '')}"
        else:
            stage = f"voice:{res.get('intent') or ('pending_confirmation' if res.get('pending_confirmation') else 'other')}"
        return self._outcome("voice", text, reply, stage=stage, before_brain=before, before_seam=before_seam,
                             pending=bool(res.get("pending_confirmation")))
