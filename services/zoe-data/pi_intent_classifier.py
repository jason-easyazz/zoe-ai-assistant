"""Pi/Gemma ambiguous-intent governor for Zoe.

This module is intentionally narrow: deterministic intents stay in
``intent_router``. Only short missed or ambiguous utterances may be sent to Pi,
and Pi must run through the local/offline runtime policy before its result can
be trusted as an executable Zoe intent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import glob
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from pi_runtime_probe import probe_pi_runtime
from zoe_pi_promotion import LOW_RISK_PI_INTENT_GROUPS, intent_group_for_intent

logger = logging.getLogger(__name__)

PI_INTENT_EXECUTE_THRESHOLD = 0.78
PI_INTENT_HINT_THRESHOLD = 0.55
PI_INTENT_DEFAULT_TIMEOUT_S = 4.0
PI_INTENT_MAX_WORDS = 32

_ALLOWED_EXECUTABLE_INTENTS = {
    "time_query",
    "date_query",
    "weather",
    "calendar_show",
    "calendar_create",
    "reminder_list",
    "reminder_create",
    "list_show",
    "list_add",
    "list_remove",
    "note_create",
    "note_search",
    "people_search",
    "greeting",
    "daily_briefing",
    "music_play",
    "music_control",
    "music_volume",
    "timer_create",
    "calculate",
    "user_issue_report",
    "extend_capability",
}

_TASK_LANES = {
    "fast_tool": {
        "time_query",
        "date_query",
        "weather",
        "calendar_show",
        "calendar_create",
        "reminder_list",
        "reminder_create",
        "list_show",
        "list_add",
        "list_remove",
        "note_create",
        "note_search",
        "people_search",
        "daily_briefing",
        "music_play",
        "music_control",
        "music_volume",
        "timer_create",
        "calculate",
    },
    "governed_agent": {"user_issue_report", "extend_capability"},
    "chat": set(),
}

_SECRET_ENV_MARKERS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY")
_PI_CLASSIFIER_BASE_FLAGS = (
    "--no-tools",
    "--no-extensions",
    "--no-skills",
    "--no-prompt-templates",
    "--no-themes",
    "--no-context-files",
    "--thinking",
    "off",
)
_PI_CLASSIFIER_SYSTEM_PROMPT = (
    "You are Zoe's offline intent classifier. Classify only; do not answer the user. "
    "Return exactly one compact JSON object with keys intent, slots, confidence, task_lane, reason. "
    "Use intent null and task_lane chat for casual chat, advice, research, or uncertain requests. "
    "Use governed_agent only for self-extension, complaints, or missing capability requests. "
    "Never create memory-write intents from ambiguous phrasing."
)


@dataclass(frozen=True)
class PiIntentClassifierConfig:
    enabled: bool = False
    provider: str = "ollama"
    model: str = "gemma-4-E2B-it-Q4_K_M.gguf"
    command: str = "pi"
    timeout_seconds: float = PI_INTENT_DEFAULT_TIMEOUT_S
    cwd: str = "/home/zoe/assistant"
    max_words: int = PI_INTENT_MAX_WORDS
    offline_only: bool = True
    no_approve: bool = True
    transport: str = "print"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "PiIntentClassifierConfig":
        values = env if env is not None else os.environ
        return cls(
            enabled=_env_bool(values.get("ZOE_PI_INTENT_ENABLED"), default=False),
            provider=(values.get("ZOE_PI_INTENT_PROVIDER") or "ollama").strip() or "ollama",
            model=(values.get("ZOE_PI_INTENT_MODEL") or values.get("ZOE_PI_MODEL") or "gemma-4-E2B-it-Q4_K_M.gguf").strip()
            or "gemma-4-E2B-it-Q4_K_M.gguf",
            command=(values.get("ZOE_PI_COMMAND") or "pi").strip() or "pi",
            timeout_seconds=float(values.get("ZOE_PI_INTENT_TIMEOUT_SECONDS") or PI_INTENT_DEFAULT_TIMEOUT_S),
            cwd=(values.get("ZOE_PI_CWD") or "/home/zoe/assistant").strip() or "/home/zoe/assistant",
            max_words=int(values.get("ZOE_PI_INTENT_MAX_WORDS") or PI_INTENT_MAX_WORDS),
            offline_only=_env_bool(values.get("ZOE_PI_OFFLINE_ONLY"), default=True),
            no_approve=_env_bool(values.get("ZOE_PI_INTENT_NO_APPROVE"), default=True),
            transport=(values.get("ZOE_PI_INTENT_TRANSPORT") or "print").strip().lower() or "print",
        )

    def validate(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("ZOE_PI_INTENT_TIMEOUT_SECONDS must be positive")
        if self.max_words <= 0:
            raise ValueError("ZOE_PI_INTENT_MAX_WORDS must be positive")
        if self.offline_only and self.provider.lower() not in {"ollama", "local", "llama", "llamacpp"}:
            raise ValueError("Pi intent classification requires a local/offline provider")
        if self.transport not in {"print", "rpc"}:
            raise ValueError("ZOE_PI_INTENT_TRANSPORT must be print or rpc")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "model": self.model,
            "command": self.command,
            "timeout_seconds": self.timeout_seconds,
            "cwd": self.cwd,
            "max_words": self.max_words,
            "offline_only": self.offline_only,
            "no_approve": self.no_approve,
            "transport": self.transport,
        }


@dataclass(frozen=True)
class PiIntentClassification:
    intent: str | None
    slots: Mapping[str, Any]
    confidence: float
    task_lane: str
    source: str
    latency_ms: float
    raw: str = ""
    reason: str | None = None

    @property
    def executable(self) -> bool:
        return bool(self.intent) and self.intent in _ALLOWED_EXECUTABLE_INTENTS and self.confidence >= PI_INTENT_EXECUTE_THRESHOLD

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "slots": dict(self.slots),
            "confidence": self.confidence,
            "task_lane": self.task_lane,
            "source": self.source,
            "latency_ms": self.latency_ms,
            "executable": self.executable,
            "reason": self.reason,
        }


def pi_intent_status(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    config = PiIntentClassifierConfig.from_env(env)
    status = {"config": _safe_config_dict(config), "promotion": pi_intent_promotion_status(env)}
    if not config.enabled:
        status.update({"ok": False, "status": "disabled", "reason": "ZOE_PI_INTENT_ENABLED is false"})
        return status
    runtime_env = _runtime_probe_env(env, config)
    probe = probe_pi_runtime(runtime_env).to_dict()
    status["probe"] = probe
    status.update(
        {
            "ok": bool(probe.get("ok")),
            "status": "available" if probe.get("ok") else probe.get("status", "unavailable"),
            "reason": probe.get("reason"),
        }
    )
    return status


def pi_intent_promotion_status(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    values = env if env is not None else os.environ
    requested_groups, active_groups, ignored_groups = _pi_intent_promotion_groups(
        values.get("ZOE_PI_INTENT_PROMOTED_GROUPS") or ""
    )
    auto_promote_requested = _env_bool(values.get("ZOE_PI_INTENT_AUTO_PROMOTE"), default=False)
    return {
        "auto_promote_requested": auto_promote_requested,
        "auto_promote_status": "evidence_only" if not auto_promote_requested else "requires_explicit_apply_path",
        "auto_promote_reason": (
            "ZOE_PI_INTENT_AUTO_PROMOTE is false; reports remain recommendations only"
            if not auto_promote_requested
            else "auto-promote is requested but this runtime only exposes guarded promotion actions"
        ),
        "requested_groups": list(requested_groups),
        "active_groups": list(active_groups),
        "ignored_groups": list(ignored_groups),
        "allowlisted_groups": sorted(LOW_RISK_PI_INTENT_GROUPS),
    }


def pi_intent_is_promoted(intent: str | None, env: Mapping[str, str] | None = None) -> bool:
    group = intent_group_for_intent(intent)
    if not group:
        return False
    return group in set(pi_intent_promotion_status(env)["active_groups"])


async def classify_with_pi_intent_governor(
    text: str,
    *,
    context_turns: str = "",
    env: Mapping[str, str] | None = None,
    config: PiIntentClassifierConfig | None = None,
) -> PiIntentClassification | None:
    active_config = config or PiIntentClassifierConfig.from_env(env)
    active_config.validate()
    if not active_config.enabled:
        return None
    if len((text or "").split()) > active_config.max_words:
        return None

    runtime_env = _runtime_probe_env(env, active_config)
    probe = probe_pi_runtime(runtime_env)
    if not probe.ok:
        logger.debug("Pi intent governor unavailable: %s", probe.to_dict())
        return None

    prompt = _classification_prompt(text, context_turns=context_turns)
    if active_config.transport == "rpc":
        return await _classify_with_pi_rpc(active_config, runtime_env, prompt)
    return await _classify_with_pi_print(active_config, runtime_env, prompt)


async def _classify_with_pi_print(
    active_config: PiIntentClassifierConfig, runtime_env: Mapping[str, str], prompt: str
) -> PiIntentClassification | None:
    cmd = _pi_command(active_config, prompt)
    run_env = _pi_subprocess_env(runtime_env)
    start = time.perf_counter()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=active_config.cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=run_env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=active_config.timeout_seconds)
    except asyncio.TimeoutError:
        if "proc" in locals():
            proc.kill()
            await proc.communicate()
        logger.debug("Pi intent governor timed out after %.2fs", active_config.timeout_seconds)
        return None
    except Exception as exc:
        logger.debug("Pi intent governor failed to start: %s", exc)
        return None

    latency_ms = (time.perf_counter() - start) * 1000
    if proc.returncode != 0:
        logger.debug("Pi intent governor failed rc=%s stderr=%s", proc.returncode, stderr.decode(errors="replace")[:500])
        return None
    return _parse_pi_classification(stdout.decode(errors="replace"), latency_ms=latency_ms)


async def _classify_with_pi_rpc(
    active_config: PiIntentClassifierConfig, runtime_env: Mapping[str, str], prompt: str
) -> PiIntentClassification | None:
    worker = _rpc_worker_for(active_config, runtime_env)
    start = time.perf_counter()
    try:
        raw = await worker.prompt(prompt, timeout_seconds=active_config.timeout_seconds)
    except asyncio.TimeoutError:
        logger.debug("Pi RPC intent governor timed out after %.2fs", active_config.timeout_seconds)
        return None
    except Exception as exc:
        logger.debug("Pi RPC intent governor failed: %s", exc)
        return None
    latency_ms = (time.perf_counter() - start) * 1000
    return _parse_pi_classification(raw, latency_ms=latency_ms)


def _classification_prompt(text: str, *, context_turns: str = "") -> str:
    lanes = {lane: sorted(intents) for lane, intents in _TASK_LANES.items() if intents}
    return (
        "Schema: {\"intent\": string|null, \"slots\": object, \"confidence\": number, "
        "\"task_lane\": \"fast_tool\"|\"governed_agent\"|\"chat\", \"reason\": string}.\n"
        f"Allowed intents: {sorted(_ALLOWED_EXECUTABLE_INTENTS)}\n"
        f"Task lanes: {json.dumps(lanes, sort_keys=True)}\n"
        "Hints: rain/umbrella/jacket=>weather; due/todo=>reminder_list; timer/alarm=>timer_create. "
        "Keep confidence below 0.78 unless obvious.\n"
        f"Recent context: {_sanitize_prompt_value(context_turns) or '(none)'}\n"
        f"User message: {_sanitize_prompt_value(text)}\n"
        "JSON:"
    )


def _pi_command(config: PiIntentClassifierConfig, prompt: str) -> list[str]:
    cmd = [
        config.command,
        "-p",
        "--no-session",
        *_PI_CLASSIFIER_BASE_FLAGS,
        "--system-prompt",
        _PI_CLASSIFIER_SYSTEM_PROMPT,
        "--provider",
        config.provider,
        "--model",
        config.model,
    ]
    if config.no_approve:
        cmd.append("--no-approve")
    cmd.append(prompt)
    return cmd


def _pi_rpc_command(config: PiIntentClassifierConfig) -> list[str]:
    cmd = [
        config.command,
        "--mode",
        "rpc",
        "--no-session",
        *_PI_CLASSIFIER_BASE_FLAGS,
        "--system-prompt",
        _PI_CLASSIFIER_SYSTEM_PROMPT,
        "--provider",
        config.provider,
        "--model",
        config.model,
    ]
    if config.no_approve:
        cmd.append("--no-approve")
    if config.offline_only:
        cmd.append("--offline")
    return cmd


@dataclass(frozen=True)
class _RpcWorkerKey:
    command: str
    provider: str
    model: str
    cwd: str
    no_approve: bool
    offline_only: bool


class _PiRpcIntentWorker:
    def __init__(self, config: PiIntentClassifierConfig, env: Mapping[str, str]) -> None:
        self.config = config
        self.env = _pi_subprocess_env(env)
        self.proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    async def prompt(self, prompt: str, *, timeout_seconds: float) -> str:
        async with self._lock:
            try:
                await self._ensure_started()
                assert self.proc is not None and self.proc.stdin is not None and self.proc.stdout is not None
                request_id = f"zoe-intent-{uuid.uuid4().hex}"
                payload = json.dumps({"id": request_id, "type": "prompt", "message": prompt}, separators=(",", ":"))
                self.proc.stdin.write((payload + "\n").encode())
                await self.proc.stdin.drain()
                return await asyncio.wait_for(self._read_turn(request_id), timeout=timeout_seconds)
            except BaseException:
                await self._reset_process_locked()
                raise

    async def reset(self) -> None:
        async with self._lock:
            await self._reset_process_locked()

    async def _reset_process_locked(self) -> None:
        proc = self.proc
        self.proc = None
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

    async def _ensure_started(self) -> None:
        if self.proc and self.proc.returncode is None:
            return
        self.proc = await asyncio.create_subprocess_exec(
            *_pi_rpc_command(self.config),
            cwd=self.config.cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=self.env,
        )

    async def _read_turn(self, request_id: str) -> str:
        assert self.proc is not None and self.proc.stdout is not None
        latest_text = ""
        prompt_accepted = False
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                raise RuntimeError("Pi RPC process closed")
            try:
                event = json.loads(line.decode(errors="replace"))
            except json.JSONDecodeError:
                continue
            if _rpc_response_matches_request(event, request_id):
                if not event.get("success"):
                    raise RuntimeError(str(event.get("error") or "Pi RPC prompt failed"))
                prompt_accepted = True
                continue
            if not prompt_accepted:
                continue
            if not _rpc_event_matches_request(event, request_id):
                continue
            text = _assistant_text_from_rpc_event(event)
            if text:
                latest_text = text
            if event.get("type") == "agent_end":
                return latest_text


_RPC_WORKERS: dict[_RpcWorkerKey, _PiRpcIntentWorker] = {}


def _rpc_worker_for(config: PiIntentClassifierConfig, env: Mapping[str, str]) -> _PiRpcIntentWorker:
    key = _RpcWorkerKey(config.command, config.provider, config.model, config.cwd, config.no_approve, config.offline_only)
    worker = _RPC_WORKERS.get(key)
    if worker is None:
        worker = _PiRpcIntentWorker(config, env)
        _RPC_WORKERS[key] = worker
    return worker


def _rpc_event_matches_request(event: Mapping[str, Any], request_id: str) -> bool:
    event_ids = [event.get("id"), event.get("request_id"), event.get("requestId")]
    turn = event.get("turn")
    if isinstance(turn, Mapping):
        event_ids.extend([turn.get("id"), turn.get("request_id"), turn.get("requestId")])
    present_ids = [str(value) for value in event_ids if value is not None]
    return not present_ids or request_id in present_ids


def _rpc_response_matches_request(event: Mapping[str, Any], request_id: str) -> bool:
    return (
        event.get("type") == "response"
        and event.get("command") == "prompt"
        and str(event.get("id") or "") == request_id
    )


def _assistant_text_from_rpc_event(event: Mapping[str, Any]) -> str:
    message = event.get("message")
    if isinstance(message, Mapping) and message.get("role") == "assistant":
        text = _text_from_message_content(message.get("content"))
        if text:
            return text
    if event.get("type") == "agent_end" and isinstance(event.get("messages"), list):
        for item in reversed(event["messages"]):
            if isinstance(item, Mapping) and item.get("role") == "assistant":
                text = _text_from_message_content(item.get("content"))
                if text:
                    return text
    assistant_event = event.get("assistantMessageEvent")
    if isinstance(assistant_event, Mapping) and assistant_event.get("type") == "text_end":
        return str(assistant_event.get("content") or "")
    return ""


def _text_from_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, Mapping) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return ""


def _parse_pi_classification(raw: str, *, latency_ms: float) -> PiIntentClassification | None:
    text = raw.strip().lstrip("` \n").rstrip("` \n")
    data = _extract_first_json_object(text)
    if data is None:
        return None
    intent = data.get("intent")
    if intent is not None:
        intent = str(intent).strip()
    if intent and intent not in _ALLOWED_EXECUTABLE_INTENTS:
        return None
    slots = data.get("slots") if isinstance(data.get("slots"), Mapping) else {}
    confidence = _bounded_float(data.get("confidence"), default=0.0)
    task_lane = str(data.get("task_lane") or _task_lane_for_intent(intent)).strip()
    if task_lane not in _TASK_LANES:
        task_lane = _task_lane_for_intent(intent)
    return PiIntentClassification(
        intent=intent or None,
        slots=slots,
        confidence=confidence,
        task_lane=task_lane,
        source="pi_gemma_intent_governor",
        latency_ms=latency_ms,
        raw=raw[:1000],
        reason=str(data.get("reason") or "")[:240] or None,
    )


def _extract_first_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    found: dict[str, Any] | None = None
    index = 0
    while index < len(text):
        brace = text.find("{", index)
        if brace == -1:
            break
        try:
            value, end = decoder.raw_decode(text[brace:])
        except json.JSONDecodeError:
            index = brace + 1
            continue
        if isinstance(value, dict):
            found = value
        index = brace + max(end, 1)
    return found


def _task_lane_for_intent(intent: str | None) -> str:
    if not intent:
        return "chat"
    for lane, intents in _TASK_LANES.items():
        if intent in intents:
            return lane
    return "chat"


def _runtime_probe_env(env: Mapping[str, str] | None, config: PiIntentClassifierConfig) -> dict[str, str]:
    values = dict(os.environ if env is None else env)
    values.update(
        {
            "ZOE_PI_ENABLED": "true" if config.enabled else "false",
            "ZOE_PI_OFFLINE_ONLY": "true" if config.offline_only else "false",
            "ZOE_PI_LOCAL_MODEL_REQUIRED": values.get("ZOE_PI_LOCAL_MODEL_REQUIRED", "true"),
            "ZOE_PI_COMMAND": config.command,
            "ZOE_PI_CWD": config.cwd,
            "ZOE_PI_TIMEOUT_SECONDS": str(config.timeout_seconds),
        }
    )
    values["ZOE_PI_ALLOW_EXECUTION"] = values.get("ZOE_PI_ALLOW_EXECUTION", "false")
    values["ZOE_PI_LOCAL_MODEL_CONFIGURED"] = values.get("ZOE_PI_LOCAL_MODEL_CONFIGURED", "false")
    values["PATH"] = _path_with_node(values.get("PATH", ""))
    return values


def _pi_subprocess_env(env: Mapping[str, str]) -> dict[str, str]:
    values = dict(env)
    if values.get("ZOE_PI_OFFLINE_ONLY", "true").lower() in {"1", "true", "yes", "on"}:
        for key in _SECRET_ENV_MARKERS:
            values.pop(key, None)
    values.setdefault("OLLAMA_HOST", "http://127.0.0.1:11434")
    return values


def _path_with_node(path: str) -> str:
    parts = [part for part in path.split(os.pathsep) if part]
    joined = os.pathsep.join(parts)
    if shutil.which("node", path=joined) and shutil.which("npm", path=joined):
        return joined
    node_bin = os.environ.get("ZOE_NVM_NODE_BIN") or _discover_nvm_node_bin()
    if node_bin and node_bin not in parts:
        parts.append(node_bin)
    return os.pathsep.join(parts)


def _discover_nvm_node_bin() -> str | None:
    candidates = [path for path in glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin")) if os.path.isdir(path)]
    if not candidates:
        return None

    def version_key(path: str) -> tuple[int, ...]:
        version = os.path.basename(os.path.dirname(path)).lstrip("v")
        try:
            return tuple(int(part) for part in version.split("."))
        except ValueError:
            return (0,)

    return sorted(candidates, key=version_key)[-1]


def _sanitize_prompt_value(value: str) -> str:
    return str(value or "").replace("{", "(").replace("}", ")")[:1000]


def _safe_config_dict(config: PiIntentClassifierConfig) -> dict[str, Any]:
    try:
        return config.to_dict()
    except Exception as exc:
        return {"error": str(exc)}


def _bounded_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return min(1.0, max(0.0, parsed))


@lru_cache(maxsize=16)
def _pi_intent_promotion_groups(value: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    requested_groups = tuple(_csv_env(value))
    active_groups = tuple(group for group in requested_groups if group in LOW_RISK_PI_INTENT_GROUPS)
    ignored_groups = tuple(group for group in requested_groups if group not in LOW_RISK_PI_INTENT_GROUPS)
    return requested_groups, active_groups, ignored_groups


def _csv_env(value: str | None) -> list[str]:
    if not value:
        return []
    return sorted({item.strip() for item in value.split(",") if item.strip()})


def _env_bool(value: str | None, *, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Unrecognized boolean env var value: {value!r}")


__all__ = [
    "PI_INTENT_EXECUTE_THRESHOLD",
    "PI_INTENT_HINT_THRESHOLD",
    "PiIntentClassification",
    "PiIntentClassifierConfig",
    "pi_intent_is_promoted",
    "pi_intent_promotion_status",
    "classify_with_pi_intent_governor",
    "pi_intent_status",
]
