"""zoe-core brain client — runs Pi (full-agent mode) as Zoe's brain.

This is the production cutover target that replaces the hand-rolled
``zoe_agent.py`` brain. It runs the installed ``pi`` CLI in ``--mode rpc`` with
the four zoe-core extensions (provider + soul + memory + abilities) on the local
Gemma backend, exposing the same streaming interface ``chat.py`` already expects
from ``run_zoe_agent_streaming`` / ``run_zoe_agent``.

Design notes
------------
* **One warm Pi process per (user, session).** The zoe-core extensions resolve
  the acting user from ``ZOE_CORE_USER_ID`` in ``process.env`` (fail-closed when
  absent), and an RPC process's env is fixed at spawn — so a *shared* process
  could only ever serve one user safely. We therefore key a persistent worker by
  (user_id, session_id): each conversation gets its own warm brain with its own
  identity baked in. This also gives natural per-conversation continuity and
  keeps the multi-user guarantee from PR #692 intact.
* **Warm + LRU-bounded.** Workers persist across turns (no ~1-2s subprocess boot
  per message). On constrained hardware we cap the live worker count and evict
  the least-recently-used process when over the cap.
* **Reuses the proven RPC plumbing** from ``pi_intent_classifier`` (event/text
  parsing) rather than duplicating it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, AsyncIterator, Mapping

from pi_intent_classifier import (
    _assistant_text_from_rpc_event,
    _pi_subprocess_env,
    _rpc_event_matches_request,
    _rpc_response_matches_request,
)

logger = logging.getLogger(__name__)

_CORE_DIR = Path(__file__).resolve().parent.parent / "zoe-core"
_EXT_DIR = _CORE_DIR / "extensions"
_SOUL_PATH = _CORE_DIR / "SOUL.md"
_EXTENSIONS = [
    _EXT_DIR / "provider-local-gemma.ts",
    _EXT_DIR / "soul.ts",
    _EXT_DIR / "memory.ts",
    _EXT_DIR / "abilities.ts",
]

_PI_COMMAND = os.environ.get("ZOE_CORE_PI_COMMAND", "pi")
_PROVIDER = os.environ.get("ZOE_CORE_PROVIDER", "local-gemma")
_MODEL = os.environ.get("ZOE_CORE_MODEL_ID", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf")
_TIMEOUT_S = float(os.environ.get("ZOE_CORE_TIMEOUT_S", "180"))
# Generation-length cap for VOICE turns only. The provider extension
# (provider-local-gemma.ts) registers the model's maxTokens from
# ZOE_CORE_MODEL_MAXTOKENS at spawn (default 2048) — there is NO per-request
# override in the Pi RPC `prompt` message, so the only safe lever is the worker's
# spawn env. Voice replies are 1-2 spoken sentences (see _VOICE_BREVITY); a chatty
# turn that runs to the full 2048-token budget adds a long generation tail and
# delays first audio for nothing. We bound voice generations to this cap (~512
# tokens ≈ far more than 2 sentences, so it never clips a real spoken answer) and
# leave non-voice (chat) turns at the provider default. 0/negative disables the cap.
_VOICE_MODEL_MAXTOKENS = int(os.environ.get("ZOE_CORE_VOICE_MODEL_MAXTOKENS", "512"))
# Safety valve: once an answer has streamed and a turn has ended, if no further
# event arrives within this idle window we assume the loop is done even if
# agent_end was never emitted (Pi crash / lost event / older build) — bounding
# the worst case to ~idle seconds instead of the full _TIMEOUT_S hang.
_IDLE_TIMEOUT_S = float(os.environ.get("ZOE_CORE_IDLE_TIMEOUT_S", "20"))
_MAX_WORKERS = int(os.environ.get("ZOE_CORE_MAX_WORKERS", "4"))
# Cap on concurrent brain turns. llama-server runs a single generation slot
# (--parallel 1), so letting many turns run at once only thrashes: N Pi
# subprocesses spawn + N prompt-prefills contend for one GPU slot on a
# memory-pressured Jetson, and some turns come back empty. Bounding concurrency
# to a small number lets each turn complete reliably (it queues instead of
# failing). Single-request latency is unaffected (the semaphore is uncontended).
_MAX_CONCURRENCY = max(1, int(os.environ.get("ZOE_CORE_MAX_CONCURRENCY", "2")))
_BRAIN_SEM: "asyncio.Semaphore | None" = None


def _brain_sem() -> "asyncio.Semaphore":
    """Lazily create the concurrency semaphore bound to the running loop."""
    global _BRAIN_SEM
    if _BRAIN_SEM is None:
        _BRAIN_SEM = asyncio.Semaphore(_MAX_CONCURRENCY)
    return _BRAIN_SEM


def _rpc_command() -> list[str]:
    cmd = [_PI_COMMAND, "--mode", "rpc", "--provider", _PROVIDER, "--model", _MODEL]
    for ext in _EXTENSIONS:
        cmd += ["-e", str(ext)]
    cmd += [
        "--no-extensions", "--no-skills", "--no-prompt-templates",
        "--no-themes", "--no-context-files", "--thinking", "off",
    ]
    return cmd


def _data_url() -> str:
    """zoe-data base URL the brain calls back for tools/delegation.

    Read lazily (NOT at import): bootstrap_runtime_env() populates os.environ in
    the lifespan startup, which runs AFTER this module is imported — a
    module-level constant would miss a .env-provided ZOE_CORE_DATA_URL and fall
    back to the wrong port. Default is loopback :8011 (the live zoe-data port),
    not the legacy :8000.
    """
    return (
        os.environ.get("ZOE_CORE_DATA_URL")
        or os.environ.get("ZOE_DATA_URL")
        or "http://127.0.0.1:8011"
    )


def _worker_env(user_id: str, *, voice_mode: bool = False) -> dict[str, str]:
    """Env for a worker. ZOE_CORE_USER_ID is baked per worker (fail-closed:
    a guest/empty user means the memory extension fetches nothing).

    Voice workers (voice_mode=True) bake a tighter generation cap
    (ZOE_CORE_MODEL_MAXTOKENS = _VOICE_MODEL_MAXTOKENS) so a spoken turn can't run
    away to the full default budget. Because this cap is fixed at spawn and a Pi
    process is reused across a session's turns, voice and non-voice workers are
    keyed separately (see _worker_for) so the cap never leaks onto chat turns.
    """
    env = _pi_subprocess_env(os.environ)
    env["ZOE_DATA_URL"] = _data_url()
    env["ZOE_CORE_SOUL_PATH"] = str(_SOUL_PATH)
    # Only known users get an identity; unknown -> empty (memory fails closed).
    env["ZOE_CORE_USER_ID"] = (user_id or "").strip()
    token = os.environ.get("ZOE_INTERNAL_TOKEN", "")
    if token:
        env["ZOE_INTERNAL_TOKEN"] = token
    env.setdefault("ZOE_CORE_ALLOW_WRITES", "true")
    if voice_mode and _VOICE_MODEL_MAXTOKENS > 0:
        env["ZOE_CORE_MODEL_MAXTOKENS"] = str(_VOICE_MODEL_MAXTOKENS)
    return env


def _toolcall_block_from_amev(amev: Mapping) -> "Mapping | None":
    """Pull the toolCall block out of a toolcall_start amev frame.

    Schema (verified live): amev.partial.content[contentIndex] == {type:"toolCall",
    id, name, arguments, ...}. We index by contentIndex when present; otherwise we
    scan partial.content for the first toolCall block. Returns None (skip the
    sentinel) on any missing/odd shape rather than raising.
    """
    partial = amev.get("partial")
    if not isinstance(partial, Mapping):
        return None
    content = partial.get("content")
    if not isinstance(content, (list, tuple)):
        return None
    idx = amev.get("contentIndex")
    if isinstance(idx, int) and 0 <= idx < len(content):
        block = content[idx]
        if isinstance(block, Mapping) and block.get("type") == "toolCall":
            return block
    for block in content:
        if isinstance(block, Mapping) and block.get("type") == "toolCall":
            return block
    return None


def _tool_args_sentinels(event: Mapping) -> "list[str]":
    """Build __TOOL__ phase=args sentinels from a message_end event.

    Schema: event.message.content == [{type:"toolCall", id, name, arguments:{...}}].
    Emits one sentinel per toolCall block carrying the FULL arguments. Defensive:
    skips blocks missing id/name, never raises.
    """
    out: list[str] = []
    message = event.get("message")
    if not isinstance(message, Mapping):
        return out
    content = message.get("content")
    if not isinstance(content, (list, tuple)):
        return out
    for block in content:
        if not isinstance(block, Mapping) or block.get("type") != "toolCall":
            continue
        tc_id = block.get("id")
        tc_name = block.get("name")
        if not tc_id or not tc_name:
            continue
        out.append(
            "__TOOL__:" + json.dumps({
                "phase": "args",
                "id": str(tc_id),
                "name": str(tc_name),
                "args": block.get("arguments") or {},
            })
        )
    return out


def _tool_result_sentinel(event: Mapping) -> "str | None":
    """Build a __TOOL__ phase=result sentinel from a tool_execution_end event.

    The result field's exact shape was not pinned in the captured schema, so we
    probe the likely carriers in order — top-level ``result``/``output``, then a
    nested ``result.content``/``result.text`` — and stringify whatever we find.
    The tool-call id is read from ``id``/``toolCallId``/``callId`` if present.
    Returns None when nothing useful is carried (so we don't emit an empty card).
    """
    tc_id = event.get("id") or event.get("toolCallId") or event.get("callId")
    result: Any = None
    for key in ("result", "output", "content"):
        if event.get(key) is not None:
            result = event[key]
            break
    if isinstance(result, Mapping):
        # Unwrap a nested {content|text|output: ...} result envelope.
        for key in ("content", "text", "output", "result"):
            if result.get(key) is not None:
                result = result[key]
                break
    if result is None and tc_id is None:
        return None
    payload: dict[str, Any] = {"phase": "result"}
    if tc_id is not None:
        payload["id"] = str(tc_id)
    if result is not None:
        payload["result"] = result if isinstance(result, str) else _stringify(result)
    return "__TOOL__:" + json.dumps(payload)


def _stringify(value: Any) -> str:
    """Best-effort compact string for a tool result of unknown shape."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


class _ZoeCoreWorker:
    """A persistent Pi-RPC brain process for one (user, session)."""

    def __init__(self, user_id: str, *, voice_mode: bool = False) -> None:
        self.user_id = user_id
        self.voice_mode = voice_mode
        self.env = _worker_env(user_id, voice_mode=voice_mode)
        self.proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self.last_used = time.monotonic()

    async def _ensure_started(self) -> None:
        if self.proc and self.proc.returncode is None:
            return
        self.proc = await asyncio.create_subprocess_exec(
            *_rpc_command(),
            cwd=str(_CORE_DIR),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=self.env,
        )

    async def reset(self) -> None:
        async with self._lock:
            await self._reset_locked()

    async def terminate_now(self) -> None:
        await self._reset_locked()

    async def _reset_locked(self) -> None:
        proc = self.proc
        self.proc = None
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

    async def stream(self, message: str, *, timeout_s: float) -> AsyncIterator[str]:
        """Send one turn; yield assistant text deltas (suffixes) as they arrive."""
        async with self._lock:
            self.last_used = time.monotonic()
            try:
                await self._ensure_started()
                assert self.proc and self.proc.stdin and self.proc.stdout
                request_id = f"zoe-core-{uuid.uuid4().hex}"
                payload = json.dumps(
                    {"id": request_id, "type": "prompt", "message": message},
                    separators=(",", ":"),
                )
                self.proc.stdin.write((payload + "\n").encode())
                await self.proc.stdin.drain()
                async for delta in self._read_turn(request_id, timeout_s):
                    yield delta
            except BaseException:
                await self._reset_locked()
                raise

    async def _read_turn(self, request_id: str, timeout_s: float) -> AsyncIterator[str]:
        assert self.proc and self.proc.stdout
        emitted = ""           # text already streamed for the CURRENT message
        streamed_any = False   # whether we've yielded anything this whole turn
        saw_turn_end = False   # at least one turn has completed
        prompt_accepted = False
        # Count of tool calls that started but haven't reported tool_execution_end.
        # A slow tool (web search / deep research ~60s / CloakBrowser) produces a
        # long stdout gap with NO events; if we applied the idle timeout during
        # that gap we'd time out, return the pre-tool fragment as a "complete"
        # answer, and leave the worker mid-generation. So while a tool is
        # outstanding we use the full remaining deadline, never the idle window.
        tools_outstanding = 0
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError("zoe-core turn timed out")
            # Once we have an answer and a completed turn — and no tool call is in
            # flight — bound each read to the idle window: if agent_end never comes,
            # return rather than hang. With a tool outstanding, the gap is expected
            # work, so wait out the full remaining deadline instead.
            idle_eligible = streamed_any and saw_turn_end and tools_outstanding == 0
            read_timeout = min(remaining, _IDLE_TIMEOUT_S) if idle_eligible else remaining
            try:
                line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=read_timeout)
            except asyncio.TimeoutError:
                if idle_eligible:
                    return  # answer delivered + a turn ended; agent_end presumed lost
                # Deadline hit with a tool still outstanding (or before any answer):
                # never pass off a truncated turn as complete. Raise so stream()
                # resets the worker (no orphaned mid-generation process) and the
                # caller can surface/persist the failure instead of a fragment.
                raise
            if not line:
                raise RuntimeError("zoe-core Pi RPC process closed")
            try:
                event = json.loads(line.decode(errors="replace"))
            except json.JSONDecodeError:
                continue
            if _rpc_response_matches_request(event, request_id):
                if not event.get("success"):
                    raise RuntimeError(str(event.get("error") or "zoe-core prompt failed"))
                prompt_accepted = True
                continue
            if not prompt_accepted or not _rpc_event_matches_request(event, request_id):
                continue
            etype = event.get("type")
            # A new assistant message resets the per-message delta tracker. The
            # agent loop can span multiple messages/turns (e.g. a tool-call turn
            # followed by the answer turn); deltas are cumulative WITHIN a message.
            if etype == "message_start":
                emitted = ""
            # Stream incremental text deltas as Pi generates them, instead of
            # waiting for text_end/agent_end. Pi emits message_update events with
            # assistantMessageEvent={type:"text_delta", delta:"…"} per chunk; the
            # old reader only caught the COMPLETE text, so the first token reached
            # the user only after the whole reply was generated (~2s TTFT → ~0.5s).
            amev = event.get("assistantMessageEvent")
            # Restrict to message_update so a terminal event (agent_end/turn_end)
            # that also happens to carry assistantMessageEvent still reaches the
            # terminal handling below instead of being skipped by the `continue`
            # (which would hang the turn for the full idle timeout).
            if etype == "message_update" and isinstance(amev, Mapping):
                amev_type = amev.get("type")
                if amev_type == "text_delta":
                    delta = str(amev.get("delta") or "")
                    if delta:
                        emitted += delta
                        streamed_any = True
                        yield delta
                # Surface "what Zoe is doing" as sentinel markers so chat.py can map
                # them to AG-UI tool/step events. These are NOT spoken text — they
                # ride alongside the text stream and are parsed (and stripped) by the
                # sentinel handlers in chat.py. Be defensive: a malformed frame skips
                # its sentinel rather than crashing the turn.
                elif amev_type == "thinking":
                    thinking = str(amev.get("thinking") or "")
                    if thinking:
                        yield "__THINKING__:" + thinking
                elif amev_type == "toolcall_start":
                    tc = _toolcall_block_from_amev(amev)
                    if tc is not None:
                        # A tool is now in flight — suppress the idle timeout until
                        # its matching tool_execution_end arrives (slow tools are
                        # silent). Counted only AFTER the frame validates: a
                        # malformed toolcall_start has no matching
                        # tool_execution_end, so counting it would leave the turn
                        # permanently non-idle-eligible (phantom pending tool).
                        tools_outstanding += 1
                        tc_id = tc.get("id")
                        tc_name = tc.get("name")
                        if tc_id and tc_name:
                            yield "__TOOL__:" + json.dumps(
                                {"phase": "start", "id": str(tc_id), "name": str(tc_name)}
                            )
                # Every message_update (text_start/delta/end, thinking, toolcall) is
                # fully handled here — never fall through to the message-field path,
                # which re-emits the first chunk (the "YouYou" double-emit).
                continue
            # ── Tool activity surfacing (top-level event types, NOT under amev) ──
            # message_end carries the COMPLETE tool-call args; tool_execution_end
            # carries the result. Both are mapped to __TOOL__ sentinels for chat.py.
            if etype == "message_end":
                for sentinel in _tool_args_sentinels(event):
                    yield sentinel
            elif etype == "tool_execution_end":
                # Tool finished — re-arm the idle timeout (clamp at 0 in case a
                # start event was missed) now that events should resume promptly.
                tools_outstanding = max(0, tools_outstanding - 1)
                sentinel = _tool_result_sentinel(event)
                if sentinel is not None:
                    yield sentinel
            # Only fall back to the whole-message field when NOTHING has streamed
            # for this turn yet. Once text_delta chunks have streamed, a terminal
            # message/text_end/agent_end event re-delivers the COMPLETE message;
            # emitting it again double-speaks the reply (whole-paragraph + "YouYou"
            # first-token doubling). Deltas are the source of truth.
            if not streamed_any:
                text = _assistant_text_from_rpc_event(event)
                if text:
                    yield text
                    emitted = text
                    streamed_any = True
            if etype == "turn_end":
                saw_turn_end = True
            # Only the END OF THE WHOLE AGENT LOOP terminates the turn. A bare
            # turn_end fires after each tool-call turn too — returning there would
            # cut off before the model synthesizes its answer from the tool result.
            if etype == "agent_end":
                if not streamed_any:
                    final = _assistant_text_from_rpc_event(event)
                    if final:
                        yield final
                return


_WORKERS: "OrderedDict[tuple[str, str, bool], _ZoeCoreWorker]" = OrderedDict()
_WORKERS_LOCK = asyncio.Lock()


async def _worker_for(
    user_id: str, session_id: str, *, voice_mode: bool = False
) -> _ZoeCoreWorker:
    # Key on voice_mode too: the generation cap is baked into the worker's spawn
    # env, so a voice turn and a chat turn for the same (user, session) must NOT
    # share a process — otherwise the voice cap would leak onto chat replies.
    key = ((user_id or "").strip(), session_id or "default", bool(voice_mode))
    async with _WORKERS_LOCK:
        worker = _WORKERS.get(key)
        if worker is None:
            worker = _ZoeCoreWorker(key[0], voice_mode=key[2])
            _WORKERS[key] = worker
        _WORKERS.move_to_end(key)
        # Evict least-recently-used over the cap, but NEVER evict a worker that's
        # mid-turn (its lock is held) — resetting an in-flight stream is what made
        # concurrent turns come back empty. Skip busy/just-fetched workers; if that
        # leaves us briefly over the cap, that's fine (they're reclaimed next call).
        evicted: list[_ZoeCoreWorker] = []
        for k in list(_WORKERS.keys()):
            if len(_WORKERS) <= _MAX_WORKERS:
                break
            w = _WORKERS[k]
            if w is worker or w._lock.locked():
                continue
            del _WORKERS[k]
            evicted.append(w)
    for old in evicted:
        try:
            await old.reset()
        except Exception:  # noqa: BLE001 - best-effort reap
            logger.warning("zoe-core: failed to reap evicted worker", exc_info=True)
    return worker


async def prewarm(user_id: str, session_id: str, *, voice_mode: bool = False) -> bool:
    """Spawn the (user, session) worker's Pi subprocess ahead of the turn.

    Called on wake-word so the first real turn of a session doesn't pay the
    ~2-3s subprocess boot — the worker is the same one the turn will use, so this
    just moves the inevitable spawn earlier (into the wake → end-of-speech window).
    Best-effort and never raises. Returns True if a live subprocess is ready.

    Pass voice_mode=True from the voice/wake path so the prewarmed worker matches
    the (voice-capped) worker the voice turn will actually use — otherwise prewarm
    would warm the chat worker and the voice turn would still pay the cold boot.
    """
    try:
        worker = await _worker_for(user_id, session_id, voice_mode=voice_mode)
        # Take the worker lock so we don't race a real turn's _ensure_started; if a
        # turn already holds it the worker is started anyway and this is a fast no-op.
        async with worker._lock:
            await worker._ensure_started()
            worker.last_used = time.monotonic()
        return bool(worker.proc and worker.proc.returncode is None)
    except Exception as exc:  # noqa: BLE001 - prewarm is best-effort, never break wake
        logger.debug("zoe-core prewarm failed (non-fatal): %s", exc)
        return False


# Per-turn brevity directive for voice. The Pi brain is a separate subprocess that
# never sees `voice_mode` directly, and SOUL has no voice rule — so without this the
# panel path got NO brevity signal and over-answered. Injected here so EVERY voice
# caller (panel, LiveKit, chat voice-mode) is covered in one place.
_VOICE_BREVITY = (
    "[VOICE MODE] This is spoken aloud, so keep it warm but SHORT — reply the way "
    "you'd actually say it out loud, in 1-2 complete sentences. Lead with the "
    "answer; skip preamble ('Sure!', 'Of course!') and recaps. Finish your thought, "
    "then stop — brief, never clipped. No markdown, lists, headers, or code; numbers "
    "in spoken form (e.g. 'twenty-four degrees'). For more than 3 items, give the "
    "first 3 and offer to continue. Think and use tools fully as normal — this only "
    "shapes how much you SAY, never what you do. Only give a longer answer if the "
    "user explicitly asks for detail."
)


def _compose_message(
    message: str,
    *,
    history: list[dict] | None,
    db_memory_context: str | None,
    portrait: str | None,
    voice_mode: bool = False,
) -> str:
    """Prepend any caller-supplied context the extensions don't already inject.

    Soul + per-turn memory packet come from the extensions; we only fold in the
    extras chat.py passes (recent history, portrait, precomputed memory context),
    and — for voice turns — a brevity directive so spoken replies stay short.
    """
    parts: list[str] = []
    if voice_mode:
        parts.append(_VOICE_BREVITY)
    if portrait:
        parts.append(f"[About you]\n{portrait.strip()}")
    if db_memory_context:
        parts.append(f"[What you remember]\n{db_memory_context.strip()}")
    if history:
        lines = []
        for turn in history[-12:]:
            role = turn.get("role") or turn.get("speaker") or "user"
            content = (turn.get("content") or turn.get("text") or "").strip()
            if content:
                lines.append(f"{role}: {content}")
        if lines:
            parts.append("[Recent conversation]\n" + "\n".join(lines))
    parts.append(message)
    return "\n\n".join(parts)


async def run_zoe_core_streaming(
    message: str,
    session_id: str,
    user_id: str = "",
    *,
    history: list[dict] | None = None,
    db_memory_context: str | None = None,
    portrait: str | None = None,
    on_tool_start=None,
    on_tool_end=None,
    on_heartbeat=None,
    voice_mode: bool = False,
) -> AsyncIterator[str]:
    """Streaming brain turn through zoe-core. Drop-in for run_zoe_agent_streaming.

    Yields assistant text deltas. On any failure, raises so the caller's existing
    fallback handling applies (we never silently swallow a brain failure).
    """
    composed = _compose_message(
        message, history=history, db_memory_context=db_memory_context,
        portrait=portrait, voice_mode=voice_mode,
    )
    # Bound concurrent brain turns (see _MAX_CONCURRENCY). Acquire BEFORE creating
    # the worker so subprocess spawns are bounded too, not just generations.
    async with _brain_sem():
        worker = await _worker_for(user_id, session_id, voice_mode=voice_mode)
        async for delta in worker.stream(composed, timeout_s=_TIMEOUT_S):
            yield delta


async def _reset_worker_for(
    user_id: str, session_id: str, *, voice_mode: bool = False
) -> None:
    """Reset the worker for a key so the next turn re-spawns its subprocess fresh.

    Used by the retry path: when a turn comes back empty under load, the worker's
    subprocess may be in a bad state — `reset()` terminates it. The worker object
    INTENTIONALLY stays registered in `_WORKERS` (it is restartable by design): the
    retry's `_worker_for` returns the same object and `_ensure_started` re-spawns
    the subprocess because `proc is None`. This preserves the (user, session) →
    worker identity and LRU position across the reset. voice_mode must match the
    turn's worker key so the right (capped vs. default) worker is reset.
    """
    key = ((user_id or "").strip(), session_id or "default", bool(voice_mode))
    async with _WORKERS_LOCK:
        worker = _WORKERS.get(key)
    if worker is not None:
        try:
            await worker.reset()
        except Exception:  # noqa: BLE001 - best-effort
            logger.debug("zoe-core: worker reset failed for %s", key, exc_info=True)


async def run_zoe_core(
    message: str,
    session_id: str,
    user_id: str = "",
    *,
    history: list[dict] | None = None,
    db_memory_context: str | None = None,
    portrait: str | None = None,
    max_tokens_override: int = 0,  # accepted for run_zoe_agent compatibility; honored in Phase 4
    voice_mode: bool = False,
) -> str:
    """Non-streaming brain turn — collects the stream into one string.

    Retries once on a transient empty/failed turn: under load a worker's
    subprocess can thrash and return no text. We re-spawn the session's worker and
    try a second time, so a momentarily-overloaded brain doesn't surface a blank
    answer. A genuinely-empty answer on both attempts returns ""; a failure on both
    attempts re-raises so the caller's fallback applies.
    """
    last_exc: "Exception | None" = None
    for attempt in (1, 2):
        chunks: list[str] = []
        try:
            async for delta in run_zoe_core_streaming(
                message, session_id, user_id,
                history=history, db_memory_context=db_memory_context,
                portrait=portrait, voice_mode=voice_mode,
            ):
                chunks.append(delta)
            last_exc = None
        except Exception as exc:  # noqa: BLE001 - retry transient brain failures once
            last_exc = exc
            logger.warning("zoe-core turn failed (attempt %d/2): %s", attempt, exc)
        result = "".join(chunks).strip()
        if result:
            return result
        # Empty or failed turn. On the first attempt, reset the worker and retry.
        if attempt == 1:
            await _reset_worker_for(user_id, session_id, voice_mode=voice_mode)
    if last_exc is not None:
        raise last_exc
    return ""


async def shutdown_workers(*, reset_timeout_s: float = 2.0) -> None:
    """Reap all warm brain processes (call on service shutdown)."""
    async with _WORKERS_LOCK:
        workers = list(_WORKERS.values())
        _WORKERS.clear()
    for worker in workers:
        try:
            await asyncio.wait_for(worker.reset(), timeout=reset_timeout_s)
        except asyncio.TimeoutError:
            logger.warning("zoe-core: worker reset timed out on shutdown; forcing terminate")
            try:
                await worker.terminate_now()
            except Exception:  # noqa: BLE001
                logger.warning("zoe-core: failed to force-terminate worker on shutdown", exc_info=True)
        except Exception:  # noqa: BLE001
            logger.warning("zoe-core: failed to reap worker on shutdown", exc_info=True)
