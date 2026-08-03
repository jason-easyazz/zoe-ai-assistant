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
import concurrent.futures
import contextlib
import json
import logging
import math
import os
import subprocess
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping

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
    # Claim the memory block for this seam (see _memory_block / _memory_packet_block).
    # With this set, extensions/memory.ts contributes NOTHING to the system prompt,
    # so the prompt stays byte-identical across a session's turns and llama.cpp can
    # reuse the cached KV prefix. Unset (standalone `pi`, bench/, zoe-core/test) the
    # extension keeps composing the block itself, exactly as it always did.
    env["ZOE_CORE_MEMORY_SEAM"] = "1"
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


# asyncio.create_subprocess_exec forks() SYNCHRONOUSLY on the calling (event
# loop) thread. zoe-data is a large multi-GB, multi-threaded process (Chroma,
# fastembed, thread pools); under heavy swap a fork can wedge post-fork/pre-exec
# on an atfork lock another thread held at fork time, freezing the whole event
# loop (the same deadlock class fixed for the hermes kanban CLI in commit
# 5e5ec34d — see services/zoe-data/AGENTS.md's "Background loops must not fork
# on the event loop thread" rule). Every cold brain turn and wake-word prewarm
# spawns this Pi RPC subprocess, so it must not fork on the loop thread either.
#
# Unlike the kanban CLI (run-to-completion, fixed via subprocess.run in a
# thread pool), this is a long-lived RPC process we stream to/from over stdin/
# stdout for the life of the worker — subprocess.run can't model that. Instead
# we do the blocking fork+exec (subprocess.Popen) in this dedicated thread pool,
# then hand the already-open pipe fds to asyncio's low-level pipe transports
# (connect_read_pipe/connect_write_pipe just wrap existing fds — no fork), so
# the rest of the worker keeps using the familiar async stdin/stdout interface.
_SPAWN_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="zoe-core-spawn"
)


class _AsyncPipeProcess:
    """Async-stream wrapper around a subprocess.Popen spawned off the event loop.

    Exposes the small subset of asyncio.subprocess.Process this module relies
    on: .stdin (StreamWriter), .stdout (StreamReader), .returncode, .terminate(),
    .kill(), .wait().
    """

    def __init__(
        self,
        popen: subprocess.Popen,
        stdin: asyncio.StreamWriter,
        stdout: asyncio.StreamReader,
    ) -> None:
        self._popen = popen
        self.stdin = stdin
        self.stdout = stdout

    @property
    def returncode(self) -> "int | None":
        return self._popen.poll()

    def terminate(self) -> None:
        with contextlib.suppress(ProcessLookupError):
            self._popen.terminate()

    def kill(self) -> None:
        with contextlib.suppress(ProcessLookupError):
            self._popen.kill()

    async def wait(self) -> int:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_SPAWN_POOL, self._popen.wait)


async def _spawn_pi_process(env: dict[str, str]) -> _AsyncPipeProcess:
    """Fork+exec the Pi RPC subprocess off the event loop thread (see above)."""
    loop = asyncio.get_running_loop()

    def _blocking_popen() -> subprocess.Popen:
        return subprocess.Popen(
            _rpc_command(),
            cwd=str(_CORE_DIR),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )

    popen = await loop.run_in_executor(_SPAWN_POOL, _blocking_popen)

    # No explicit loop= args: this coroutine runs on the loop these objects
    # will use, and the loop parameter was removed from asyncio's high-level
    # APIs in 3.10 — the constructors bind the running loop themselves.
    reader = asyncio.StreamReader()
    read_protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: read_protocol, popen.stdout)

    write_transport, write_protocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin, popen.stdin
    )
    writer = asyncio.StreamWriter(write_transport, write_protocol, reader, loop)

    return _AsyncPipeProcess(popen, writer, reader)


class _ZoeCoreWorker:
    """A persistent Pi-RPC brain process for one (user, session)."""

    def __init__(self, user_id: str, *, voice_mode: bool = False) -> None:
        self.user_id = user_id
        self.voice_mode = voice_mode
        self.env = _worker_env(user_id, voice_mode=voice_mode)
        self.proc: "_AsyncPipeProcess | None" = None
        self._lock = asyncio.Lock()
        self.last_used = time.monotonic()

    async def _ensure_started(self) -> None:
        if self.proc and self.proc.returncode is None:
            return
        self.proc = await _spawn_pi_process(self.env)

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

    async def stream(
        self,
        message: "str | Callable[[], Awaitable[str]]",
        *,
        timeout_s: float,
    ) -> AsyncIterator[str]:
        """Send one turn; yield assistant text deltas (suffixes) as they arrive.

        `message` may be a coroutine FACTORY instead of a string, in which case it
        is awaited INSIDE the per-session lock. That placement is the ordering
        contract, not a style choice: the memory recall fetch that composes the
        prompt is the only slow step before the turn, and awaiting it in the caller
        (outside this lock) let a later same-session request overtake an earlier one
        whose recall was stalled. Before the seam owned recall, the fetch happened
        inside the memory.ts extension — i.e. after the prompt was written, with
        this lock already held — so arrival order was preserved. Composing here
        restores exactly that.
        """
        async with self._lock:
            self.last_used = time.monotonic()
            if not isinstance(message, str):
                message = await message()
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
        # Tool calls that started but haven't reported a MATCHING tool_execution_end.
        # A slow tool (web search / deep research ~60s / CloakBrowser) produces a
        # long stdout gap with NO events; if we applied the idle timeout during
        # that gap we'd time out, return the pre-tool fragment as a "complete"
        # answer, and leave the worker mid-generation. So while a tool is
        # outstanding we use the full remaining deadline, never the idle window.
        outstanding_tool_ids: dict[str, int] = {}
        outstanding_tool_names: dict[str, int] = {}

        def _tools_outstanding() -> int:
            return sum(outstanding_tool_ids.values()) + sum(outstanding_tool_names.values())

        def _decrement_pending_tool(bucket: dict[str, int], key: str) -> None:
            remaining = bucket[key] - 1
            if remaining > 0:
                bucket[key] = remaining
            else:
                del bucket[key]

        def _remember_tool_start(tool_call: Mapping) -> None:
            tc_id = tool_call.get("id")
            tc_name = tool_call.get("name")
            if tc_id:
                key = str(tc_id)
                outstanding_tool_ids[key] = outstanding_tool_ids.get(key, 0) + 1
            elif tc_name:
                key = str(tc_name)
                outstanding_tool_names[key] = outstanding_tool_names.get(key, 0) + 1

        def _mark_tool_end(event: Mapping) -> bool:
            tc_id = event.get("toolCallId") or event.get("callId")
            if tc_id:
                key = str(tc_id)
                if key in outstanding_tool_ids:
                    _decrement_pending_tool(outstanding_tool_ids, key)
                    return True
                logger.debug("zoe-core: ignoring unmatched tool_execution_end id=%s", key)
                return False
            tc_name = event.get("name") or event.get("toolName")
            if tc_name:
                key = str(tc_name)
                if key in outstanding_tool_names:
                    _decrement_pending_tool(outstanding_tool_names, key)
                    return True
                logger.debug("zoe-core: ignoring unmatched tool_execution_end name=%s", key)
                return False
            logger.debug("zoe-core: ignoring id-less/name-less tool_execution_end")
            return False

        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError("zoe-core turn timed out")
            # Once we have an answer and a completed turn — and no tool call is in
            # flight — bound each read to the idle window: if agent_end never comes,
            # return rather than hang. With a tool outstanding, the gap is expected
            # work, so wait out the full remaining deadline instead.
            idle_eligible = streamed_any and saw_turn_end and _tools_outstanding() == 0
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
                        tc_id = tc.get("id")
                        tc_name = tc.get("name")
                        _remember_tool_start(tc)
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
                # Tool finished — re-arm the idle timeout only when this end frame
                # matches a tracked start. Stray/duplicate end frames are common
                # enough to be ignored; letting them clear the counter can truncate a
                # slow, still-running tool turn.
                if _mark_tool_end(event):
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


# ── The memory block rides HERE, not on the system prompt ────────────────────
#
# extensions/memory.ts used to compose the /api/memories/for-prompt packet onto
# the SYSTEM PROMPT every turn. The packet is keyed on the user's message, so it
# changed on essentially every turn — and llama.cpp reuses cached KV only for an
# EXACT common prefix (`--cache-reuse` is off for Gemma's shared-KV + SWA
# attention, where KV shifting is unsupported). The reusable prefix therefore
# ended at the last byte of SOUL.md and the whole conversation was re-prefilled
# every single turn. Folding it in here instead puts the volatile bytes in the
# TAIL, past everything cacheable, and leaves the system prompt as pure SOUL.md.
#
# Two ordering rules, both established by measurement against
# test_zoe_core_client.py::test_tool_action_dispatches (15 runs, 14/15 baseline):
#
#   * The directive rides WITH the packet and never without it. Keeping the
#     directive unconditional — the obvious way to make it static and therefore
#     cacheable — scored 6/15: told to lead with what it remembers when it
#     remembers nothing, the 4B brain chats instead of calling its tools. Dropping
#     the directive from that same build restored 15/15.
#   * `message` stays LAST. Pi's own tail slot (a `custom` message, appended
#     AFTER the user message) scored 9/15 and costs the request its recency
#     position for nothing the seam does not already provide.
#
# ONE FETCH SITE, not one block. `_worker_env` sets ZOE_CORE_MEMORY_SEAM=1, which
# makes memory.ts contribute nothing, so /api/memories/for-prompt is requested
# exactly once per turn — here. It is NOT mutually exclusive with a caller-supplied
# `db_memory_context`, and making it so was a REGRESSION (caught in review on
# #1615):
#
#   * The VOICE path always passes a nonempty db_memory_context
#     (`_voice_brain_memory` → `_voice_recall_packet`, routers/voice_tts.py), and
#     that packet is built from MemoryService.search / zoe_memory_compose —
#     it never calls memory_for_prompt. But `_fold_pending_contact_offers`
#     (routers/memories.py:725, flag `ZOE_PERSON_SUGGEST_ENABLED`) runs ONLY
#     inside memory_for_prompt. Suppressing the fetch therefore dropped every
#     pending "Would you like me to add <name> as a contact?" offer from voice —
#     the endpoint carries additions the voice recall block does not.
#   * It also inverted ZOE_CHAT_INJECT_DB_MEMORY, whose documented meaning
#     (routers/chat.py) is "ON restores the old DOUBLE injection".
#
# So both blocks are emitted independently, exactly as they were before this
# change: on chat db_memory_context is None by default and only the packet
# appears; on voice both do, which is what the replay corpus was gated against.

# Matches extensions/memory.ts's own slice, so both sides key recall identically.
_PACKET_MESSAGE_CHARS = 500
_DEFAULT_PACKET_TIMEOUT_MS = 2000.0


def _packet_timeout_s() -> float:
    """The memory budget in seconds, falling back on an unparseable setting.

    Read at call time and never at import: a module-level `float(os.environ[...])`
    raised ValueError during import on an operator typo, which took out the whole
    core brain lane — strictly worse than the fail-open behaviour the rest of this
    path is built on. A bad or non-positive value degrades to the default instead.
    """
    # The literal default is kept in the getenv call so tools/audit/flag_inventory.py
    # can extract it statically; _DEFAULT_PACKET_TIMEOUT_MS covers the invalid-value
    # path. The parametrized timeout test exercises both, so they cannot drift apart.
    raw = (os.environ.get("ZOE_CORE_MEMORY_TIMEOUT_MS", "2000") or "").strip()
    ms = _DEFAULT_PACKET_TIMEOUT_MS
    if raw:
        try:
            parsed = float(raw)
            # isfinite is load-bearing, not belt-and-braces: float("inf") and
            # float("1e10000") both parse cleanly and both pass `> 0`, and
            # asyncio.wait_for(timeout=inf) waits FOREVER — a typo would hang the
            # core lane on a memory fetch instead of degrading past it. (NaN fails
            # `> 0` already; isfinite covers it too.)
            if parsed > 0 and math.isfinite(parsed):
                ms = parsed
            else:
                logger.warning(
                    "ZOE_CORE_MEMORY_TIMEOUT_MS=%r is not a positive finite number; using %.0fms",
                    raw, _DEFAULT_PACKET_TIMEOUT_MS,
                )
        except ValueError:
            logger.warning(
                "ZOE_CORE_MEMORY_TIMEOUT_MS=%r is not a number; using %.0fms",
                raw, _DEFAULT_PACKET_TIMEOUT_MS,
            )
    return ms / 1000.0

# How the brain should USE the packet. Byte-for-byte the same string as
# MEMORY_USAGE_DIRECTIVE in services/zoe-core/extensions/memory.ts — the two run
# in different processes so the constant cannot be shared, and the copies are
# pinned equal by tests/test_zoe_core_memory_packet_placement.py. Change both or
# neither.
_MEMORY_USAGE_DIRECTIVE = (
    "Use what you know about the user (below) naturally, the way a close friend would. "
    "When they greet you or open a conversation, and you know of something timely — a "
    "date in the next day or two, or a worry they've been carrying — LEAD with it warmly "
    "(e.g. \"Morning! Don't forget the dentist at 3 today.\"), then ask how they are. "
    "If something is clearly relevant to what they just said, bring it up even unasked. "
    "Don't recite the whole list, don't force a fact when none fits, never mention citation ids."
)


# Introduces the user's own turn at the end of a composed prompt. Byte-for-byte
# the same string as UTTERANCE_MARKER in services/zoe-core/extensions/abilities.ts,
# which splits on it to scope progressive disclosure to the latest utterance; the
# two run in different processes so the constant cannot be shared, and the copies
# are pinned equal by tests/test_zoe_core_memory_packet_placement.py.
_UTTERANCE_MARKER = "[The user just said]"


# The memory block is DELIMITED so `extensions/memory.ts` can strip superseded
# copies out of Pi's retained conversation before each LLM call (see its `context`
# handler). Pi retains every user message it is sent, so an undelimited block
# accumulated one full memory snapshot per turn — burning the 32k window and
# leaving corrected facts, and resolved "add this contact?" offers, permanently
# readable in older turns. Same vocabulary as the flue lane's `_RECALL_BLOCK_OPEN`.
# Kept byte-for-byte in sync with MEMORY_BLOCK_OPEN/CLOSE in memory.ts (pinned by
# a test).
_MEMORY_BLOCK_OPEN = "[MEMORY CONTEXT]"
_MEMORY_BLOCK_CLOSE = "[END MEMORY CONTEXT]"


# ── Delimiter-collision guard ────────────────────────────────────────────────
#
# The three markers above are COMPOSITION-OWNED: this module emits them, and
# `memory.ts` / `abilities.ts` parse them back out of Pi's retained conversation.
# Everything ELSE folded into a composed message is user content —
# `routers/memories.py` splices stored `ref.text[:200]` straight into the packet,
# the caller's recall context is recalled user text, and the replayed history is
# literally what was said. A stored memory whose text is the line
# `[END MEMORY CONTEXT]` therefore closed the block early: the elision regex
# stopped at the user-controlled delimiter, and the REMAINDER of a superseded
# packet — stale facts, a resolved "would you like me to add Sam as a contact?"
# offer — survived the strip and stayed readable for the life of the session.
# That is the whole corrected-fact/resolved-offer guarantee, defeated by content.
#
# So every marker occurrence in content gets a U+200B ZERO WIDTH SPACE wedged in
# after its opening bracket: `[END MEMORY CONTEXT]` becomes
# `[<ZWSP>END MEMORY CONTEXT]`, which renders identically and reads identically to
# the brain, but is no longer equal to any delimiter any consumer looks for.
#
# Escape rather than reject: dropping a colliding memory would let one poisoned
# fact silence itself, which is a worse failure than showing it inertly.
#
# Two properties this relies on:
#   * It runs on CONTENT ONLY, before the delimiters are added, so composition
#     keeps sole ownership of them.
#   * It is idempotent (a wedged marker no longer matches) and byte-for-byte a
#     no-op on text containing no marker — so ordinary packets, and the
#     voice-replay corpus, are unchanged by construction.
_MARKER_BREAK = "\u200b"  # written as an ESCAPE on purpose: the char is invisible in source

# The labels `_compose_message` puts on each context block. They are STRUCTURE,
# not text: `abilities.ts` anchors on `_HISTORY_LABEL` to find the replayed turns
# it seeds disclosure from, so a stored memory containing that line could
# otherwise forge a history block and arm a domain the user never mentioned —
# the round-two bug by another route. `_HISTORY_LABEL` is kept byte-for-byte in
# sync with `HISTORY_MARKER` in services/zoe-core/extensions/abilities.ts (pinned
# by a test).
_PORTRAIT_LABEL = "[About you]"
_RECALL_LABEL = "[What you remember]"
_HISTORY_LABEL = "[Recent conversation]"

# Mirrored (block OPEN/CLOSE only) by `CONTROL_MARKERS` in memory.ts. The other
# four have no TS counterpart to guard: a standalone `pi` run has no composed
# message at all, so those markers exist only in what THIS module builds.
_CONTROL_MARKERS = (
    _MEMORY_BLOCK_OPEN,
    _MEMORY_BLOCK_CLOSE,
    _UTTERANCE_MARKER,
    _PORTRAIT_LABEL,
    _RECALL_LABEL,
    _HISTORY_LABEL,
)


def _neutralize_markers(text: str) -> str:
    """User content with every composition-owned marker rendered inert.

    See the note above. No-op — and returns the SAME object — when nothing
    collides, which is every real turn.
    """
    if not text:
        return text
    for marker in _CONTROL_MARKERS:
        if marker in text:
            text = text.replace(marker, f"{marker[:1]}{_MARKER_BREAK}{marker[1:]}")
    return text


def _memory_block(packet: str) -> str:
    """Directive + packet, delimited, or "" — the directive NEVER appears alone."""
    # Neutralize BEFORE delimiting: the packet is user content (stored memory
    # text), the delimiters are ours. See `_neutralize_markers`.
    packet = _neutralize_markers((packet or "").strip())
    if not packet:
        return ""
    return (
        f"{_MEMORY_BLOCK_OPEN}\n{_MEMORY_USAGE_DIRECTIVE}\n\n{packet}\n{_MEMORY_BLOCK_CLOSE}"
    )


async def _memory_packet_block(message: str, user_id: str) -> str:
    """The /api/memories/for-prompt packet for this turn, or "" — NEVER raises.

    Calls the composer function directly (routers.memories.memory_for_prompt)
    rather than an HTTP self-call: same event loop, no socket round-trip. `_=None`
    skips the FastAPI internal-token dependency, which guards the HTTP surface and
    not in-process callers; the endpoint itself fails closed for guest/unknown
    users. Same shape as zoe_flue_client._fetch_for_prompt_packet.

    Fail-open and time-boxed on the same budget the extension used: memory must
    never block or break a turn.
    """
    if not (user_id or "").strip():
        return ""  # fail closed: unknown user → never another user's memories
    try:
        # `limit` MUST be passed explicitly: called directly (not through FastAPI)
        # its default is the Query() descriptor object, not an int. This is the
        # same value the endpoint would have resolved for the extension's HTTP
        # call, so the packet is identical to the one memory.ts used to fetch.
        from routers.memories import _PROMPT_PACKET_MAX_FACTS, memory_for_prompt

        result = await asyncio.wait_for(
            memory_for_prompt(
                user_id=user_id,
                message=(message or "")[:_PACKET_MESSAGE_CHARS],
                limit=_PROMPT_PACKET_MAX_FACTS,
                _=None,
            ),
            timeout=_packet_timeout_s(),
        )
        return str((result or {}).get("packet") or "").strip()
    except Exception as exc:  # noqa: BLE001 - memory is best-effort, never a turn breaker
        logger.debug("zoe-core: memory packet unavailable (non-fatal): %s", exc)
        return ""


def _compose_message(
    message: str,
    *,
    history: list[dict] | None,
    db_memory_context: str | None,
    portrait: str | None,
    voice_mode: bool = False,
    memory_packet: str | None = None,
) -> str:
    """Prepend the per-turn context the brain needs ahead of the user's words.

    Soul + the static memory-usage directive come from the extensions; we fold in
    the memory packet (see above), the extras chat.py passes (recent history,
    portrait, precomputed memory context), and — for voice turns — a brevity
    directive so spoken replies stay short.

    `message` is always LAST. Nothing may be appended after it.

    When any context block is present the user's turn is introduced by
    `_UTTERANCE_MARKER`. That label is the boundary `extensions/abilities.ts`
    uses to scope progressive disclosure to the LATEST utterance — without it
    `event.prompt` is this whole composed string, so a domain keyword sitting in
    the replayed history or the memory packet re-armed that domain on every turn
    and the disclosure window could never decay. With no context blocks the
    composed message is the bare `message`, byte-for-byte as before.
    """
    parts: list[str] = []
    if voice_mode:
        parts.append(_VOICE_BREVITY)
    # Every block below folds USER CONTENT in behind a composition-owned label, so
    # each one is neutralized (see `_neutralize_markers`). The packet is where a
    # collision actually LEAKED — it sits between the block delimiters — but the
    # portrait, the recall context and the replayed history are user text too, and
    # a marker in any of them makes the strip over-elide a superseded turn. Sweep
    # the class rather than the one instance.
    #
    # `message` is deliberately NOT neutralized: it is LAST, so a marker the user
    # types can only make the strip drop MORE of their own superseded turn, never
    # leak one; and `latestUtterance` already splits on the LAST occurrence, so it
    # can only narrow their own text. Rewriting the user's literal words has a real
    # cost and buys no safety here.
    if portrait:
        parts.append(f"{_PORTRAIT_LABEL}\n{_neutralize_markers(portrait.strip())}")
    if db_memory_context:
        parts.append(
            f"{_RECALL_LABEL}\n{_neutralize_markers(db_memory_context.strip())}"
        )
    # INDEPENDENT of db_memory_context, never an elif — the voice path always
    # supplies one, and the for-prompt packet carries additions (pending-contact
    # offers) that the voice recall block does not. See the header.
    #
    # Not labelled like the block above: these are the exact bytes the memory
    # extension used to put on the system prompt, moved verbatim.
    block = _memory_block(memory_packet or "")
    if block:
        parts.append(block)
    if history:
        lines = []
        for turn in history[-12:]:
            role = turn.get("role") or turn.get("speaker") or "user"
            content = (turn.get("content") or turn.get("text") or "").strip()
            if content:
                lines.append(f"{role}: {_neutralize_markers(content)}")
        if lines:
            # `role: text` per line — the shape `abilities.ts` parses to seed
            # disclosure on a restarted worker. Roles stay unprefixed and
            # unescaped; only the CONTENT is neutralized (above).
            parts.append(f"{_HISTORY_LABEL}\n" + "\n".join(lines))
    if not parts:
        return message  # no context at all → the bare utterance, unchanged
    parts.append(f"{_UTTERANCE_MARKER}\n{message}")
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
    # Composition is DEFERRED into the worker's per-session lock (see
    # _ZoeCoreWorker.stream): awaiting the recall fetch out here let a later
    # same-session request overtake an earlier one whose recall was stalled.
    #
    # The fetch itself is unconditional: this is the ONLY /api/memories/for-prompt
    # call in the lane (memory.ts stands down via ZOE_CORE_MEMORY_SEAM), and it
    # must NOT be skipped when the caller supplied db_memory_context — the endpoint
    # folds in pending-contact offers that no other path produces. See the header.
    async def _compose() -> str:
        packet = await _memory_packet_block(message, user_id)
        return _compose_message(
            message, history=history, db_memory_context=db_memory_context,
            portrait=portrait, voice_mode=voice_mode, memory_packet=packet,
        )
    # Bound concurrent brain turns (see _MAX_CONCURRENCY), but only for the
    # duration of actual generation — NOT for however long the consumer takes to
    # process each delta. A naive `async with _brain_sem(): async for delta in
    # worker.stream(...): yield delta` holds the semaphore (and the worker's
    # per-session lock) across every yield, i.e. for the whole consumer-paced
    # generator lifetime: a voice consumer doing per-sentence Kokoro TTS, or a
    # Hermes escalation, keeps a slot pinned long after the brain itself is done,
    # starving the other _MAX_CONCURRENCY-1 slot(s) for unrelated turns.
    #
    # Fix: run generation in a background task that drains worker.stream() into
    # an unbounded queue as fast as the brain produces it (queue.put never blocks
    # on a slow consumer), holding the semaphore only inside that task. The outer
    # generator yields from the queue at whatever pace the caller consumes —
    # decoupled from the semaphore/lock hold, which now releases as soon as
    # generation (agent_end) actually completes.
    queue: "asyncio.Queue[object]" = asyncio.Queue()
    _DONE = object()
    errors: list[BaseException] = []

    async def _produce() -> None:
        try:
            async with _brain_sem():
                worker = await _worker_for(user_id, session_id, voice_mode=voice_mode)
                async for delta in worker.stream(_compose, timeout_s=_TIMEOUT_S):
                    await queue.put(delta)
        except BaseException as exc:  # noqa: BLE001 - re-raised to the consumer below
            errors.append(exc)
        finally:
            await queue.put(_DONE)

    producer = asyncio.ensure_future(_produce())
    try:
        while True:
            item = await queue.get()
            if item is _DONE:
                break
            yield item
        if errors:
            raise errors[0]
    finally:
        # Consumer stopped early (break/exception/aclose) — make sure the
        # producer (and the worker turn it holds) unwinds instead of leaking a
        # background generation.
        if not producer.done():
            producer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await producer


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
                # __TOOL__/__THINKING__ are activity sentinels for streaming UI
                # consumers (see chat.py's sentinel handling) — never real reply
                # text. The streaming path strips them before display/TTS; do the
                # same here so a non-streaming caller doesn't persist/speak raw
                # sentinel JSON.
                if delta.startswith("__TOOL__:") or delta.startswith("__THINKING__:"):
                    continue
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
