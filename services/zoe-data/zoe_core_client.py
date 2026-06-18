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
from typing import AsyncIterator, Mapping

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
_MODEL = os.environ.get("ZOE_CORE_MODEL_ID", "gemma-4-E2B-it-Q4_K_M.gguf")
_TIMEOUT_S = float(os.environ.get("ZOE_CORE_TIMEOUT_S", "180"))
# Safety valve: once an answer has streamed and a turn has ended, if no further
# event arrives within this idle window we assume the loop is done even if
# agent_end was never emitted (Pi crash / lost event / older build) — bounding
# the worst case to ~idle seconds instead of the full _TIMEOUT_S hang.
_IDLE_TIMEOUT_S = float(os.environ.get("ZOE_CORE_IDLE_TIMEOUT_S", "20"))
_MAX_WORKERS = int(os.environ.get("ZOE_CORE_MAX_WORKERS", "4"))


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


def _worker_env(user_id: str) -> dict[str, str]:
    """Env for a worker. ZOE_CORE_USER_ID is baked per worker (fail-closed:
    a guest/empty user means the memory extension fetches nothing)."""
    env = _pi_subprocess_env(os.environ)
    env["ZOE_DATA_URL"] = _data_url()
    env["ZOE_CORE_SOUL_PATH"] = str(_SOUL_PATH)
    # Only known users get an identity; unknown -> empty (memory fails closed).
    env["ZOE_CORE_USER_ID"] = (user_id or "").strip()
    token = os.environ.get("ZOE_INTERNAL_TOKEN", "")
    if token:
        env["ZOE_INTERNAL_TOKEN"] = token
    env.setdefault("ZOE_CORE_ALLOW_WRITES", "true")
    return env


class _ZoeCoreWorker:
    """A persistent Pi-RPC brain process for one (user, session)."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self.env = _worker_env(user_id)
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
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError("zoe-core turn timed out")
            # Once we have an answer and a completed turn, bound each read to the
            # idle window: if agent_end never comes, return rather than hang.
            read_timeout = min(remaining, _IDLE_TIMEOUT_S) if (streamed_any and saw_turn_end) else remaining
            try:
                line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=read_timeout)
            except asyncio.TimeoutError:
                if streamed_any and saw_turn_end:
                    return  # answer delivered + a turn ended; agent_end presumed lost
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
            text = _assistant_text_from_rpc_event(event)
            if text and text.startswith(emitted) and len(text) > len(emitted):
                yield text[len(emitted):]
                emitted = text
                streamed_any = True
            elif text and not text.startswith(emitted):
                # Message boundary / non-monotonic update — emit the fresh text.
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


_WORKERS: "OrderedDict[tuple[str, str], _ZoeCoreWorker]" = OrderedDict()
_WORKERS_LOCK = asyncio.Lock()


async def _worker_for(user_id: str, session_id: str) -> _ZoeCoreWorker:
    key = ((user_id or "").strip(), session_id or "default")
    async with _WORKERS_LOCK:
        worker = _WORKERS.get(key)
        if worker is None:
            worker = _ZoeCoreWorker(key[0])
            _WORKERS[key] = worker
        _WORKERS.move_to_end(key)
        # Evict least-recently-used over the cap.
        evicted: list[_ZoeCoreWorker] = []
        while len(_WORKERS) > _MAX_WORKERS:
            _, old = _WORKERS.popitem(last=False)
            evicted.append(old)
    for old in evicted:
        try:
            await old.reset()
        except Exception:  # noqa: BLE001 - best-effort reap
            logger.warning("zoe-core: failed to reap evicted worker", exc_info=True)
    return worker


def _compose_message(
    message: str,
    *,
    history: list[dict] | None,
    db_memory_context: str | None,
    portrait: str | None,
) -> str:
    """Prepend any caller-supplied context the extensions don't already inject.

    Soul + per-turn memory packet come from the extensions; we only fold in the
    extras chat.py passes (recent history, portrait, precomputed memory context).
    """
    parts: list[str] = []
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
        message, history=history, db_memory_context=db_memory_context, portrait=portrait
    )
    worker = await _worker_for(user_id, session_id)
    async for delta in worker.stream(composed, timeout_s=_TIMEOUT_S):
        yield delta


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
    """Non-streaming brain turn — collects the stream into one string."""
    chunks: list[str] = []
    async for delta in run_zoe_core_streaming(
        message, session_id, user_id,
        history=history, db_memory_context=db_memory_context,
        portrait=portrait, voice_mode=voice_mode,
    ):
        chunks.append(delta)
    return "".join(chunks).strip()


async def shutdown_workers() -> None:
    """Reap all warm brain processes (call on service shutdown)."""
    async with _WORKERS_LOCK:
        workers = list(_WORKERS.values())
        _WORKERS.clear()
    for worker in workers:
        try:
            await worker.reset()
        except Exception:  # noqa: BLE001
            logger.warning("zoe-core: failed to reap worker on shutdown", exc_info=True)
