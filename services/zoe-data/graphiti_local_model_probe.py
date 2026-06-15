"""Explicit local-model structured-output probe for Graphiti readiness."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

import httpx

from graphiti_runtime_probe import GraphitiRuntimeConfig, GraphitiRuntimeConfigError


class GraphitiLocalModelProbeError(ValueError):
    """Raised when local structured-output probe configuration is invalid."""


@dataclass(frozen=True)
class GraphitiLocalModelProbeConfig:
    run: bool = False
    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_model: str = "gemma-4-E2B-it-Q4_K_M.gguf"
    offline_only: bool = True
    timeout_seconds: float = 8.0
    max_tokens: int = 400

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        run: bool | None = None,
    ) -> "GraphitiLocalModelProbeConfig":
        values = env or os.environ
        runtime = GraphitiRuntimeConfig.from_env(values)
        config = cls(
            run=_env_bool(values.get("GRAPHITI_LOCAL_MODEL_PROBE_RUN"), default=False) if run is None else run,
            llm_base_url=runtime.llm_base_url,
            llm_model=runtime.llm_model,
            offline_only=runtime.offline_only,
            timeout_seconds=_env_float(values.get("GRAPHITI_LOCAL_MODEL_PROBE_TIMEOUT_SECONDS"), default=8.0),
            max_tokens=_env_int(values.get("GRAPHITI_LOCAL_MODEL_PROBE_MAX_TOKENS"), default=400),
        )
        config.validate()
        return config

    @classmethod
    def snapshot_from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        run: bool | None = None,
    ) -> dict[str, Any]:
        values = env or os.environ
        runtime = GraphitiRuntimeConfig.snapshot_from_env(values)
        return {
            "run": _env_bool_snapshot(values.get("GRAPHITI_LOCAL_MODEL_PROBE_RUN"), default=False) if run is None else run,
            "llm_base_url": runtime.get("llm_base_url"),
            "llm_model": runtime.get("llm_model"),
            "offline_only": runtime.get("offline_only"),
            "timeout_seconds": _env_float_snapshot(values.get("GRAPHITI_LOCAL_MODEL_PROBE_TIMEOUT_SECONDS"), default=8.0),
            "max_tokens": _env_int_snapshot(values.get("GRAPHITI_LOCAL_MODEL_PROBE_MAX_TOKENS"), default=400),
        }

    def validate(self) -> None:
        if self.timeout_seconds <= 0:
            raise GraphitiLocalModelProbeError("GRAPHITI_LOCAL_MODEL_PROBE_TIMEOUT_SECONDS must be positive")
        if self.max_tokens <= 0:
            raise GraphitiLocalModelProbeError("GRAPHITI_LOCAL_MODEL_PROBE_MAX_TOKENS must be positive")
        path = urlparse(self.llm_base_url).path.rstrip("/")
        if path and not path.endswith("/v1") and not path.endswith("/chat/completions"):
            raise GraphitiLocalModelProbeError(
                "GRAPHITI_LLM_BASE_URL must be an OpenAI-compatible root, /v1 base, or /chat/completions URL"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run": self.run,
            "llm_base_url": self.llm_base_url,
            "llm_model": self.llm_model,
            "offline_only": self.offline_only,
            "timeout_seconds": self.timeout_seconds,
            "max_tokens": self.max_tokens,
        }


@dataclass(frozen=True)
class GraphitiLocalModelProbeResult:
    ok: bool
    acceptable: bool
    status: str
    config: dict[str, Any]
    latency_ms: float
    request: dict[str, Any]
    parsed: dict[str, Any] | None = None
    raw_text: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "acceptable": self.acceptable,
            "status": self.status,
            "config": self.config,
            "latency_ms": self.latency_ms,
            "request": self.request,
            "parsed": self.parsed,
            "raw_text": self.raw_text,
            "reason": self.reason,
        }


async def probe_graphiti_local_model_contract(
    *,
    env: Mapping[str, str] | None = None,
    run: bool | None = None,
) -> GraphitiLocalModelProbeResult:
    """Probe local structured-output viability for Graphiti without writes."""

    started = time.perf_counter()
    try:
        config = GraphitiLocalModelProbeConfig.from_env(env, run=run)
    except (GraphitiRuntimeConfigError, GraphitiLocalModelProbeError, ValueError) as exc:
        return GraphitiLocalModelProbeResult(
            ok=False,
            acceptable=False,
            status="misconfigured",
            config=GraphitiLocalModelProbeConfig.snapshot_from_env(env, run=run),
            latency_ms=_elapsed_ms(started),
            request=_contract_request_snapshot(),
            reason=str(exc),
        )

    if not config.run:
        return GraphitiLocalModelProbeResult(
            ok=False,
            acceptable=True,
            status="disabled",
            config=config.to_dict(),
            latency_ms=_elapsed_ms(started),
            request=_contract_request_snapshot(),
            reason="explicit --run or GRAPHITI_LOCAL_MODEL_PROBE_RUN=true is required",
        )

    try:
        raw_text = await _call_local_chat_completion(config)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return GraphitiLocalModelProbeResult(
            ok=False,
            acceptable=False,
            status="llm_unavailable",
            config=config.to_dict(),
            latency_ms=_elapsed_ms(started),
            request=_contract_request_snapshot(),
            reason=str(exc),
        )

    parsed = _parse_json_object(raw_text)
    if parsed is None:
        return GraphitiLocalModelProbeResult(
            ok=False,
            acceptable=False,
            status="invalid_json",
            config=config.to_dict(),
            latency_ms=_elapsed_ms(started),
            request=_contract_request_snapshot(),
            raw_text=raw_text,
            reason="local model did not return a parseable JSON object",
        )

    validation_error = _validate_contract_payload(parsed)
    if validation_error:
        return GraphitiLocalModelProbeResult(
            ok=False,
            acceptable=False,
            status="contract_mismatch",
            config=config.to_dict(),
            latency_ms=_elapsed_ms(started),
            request=_contract_request_snapshot(),
            parsed=parsed,
            raw_text=raw_text,
            reason=validation_error,
        )

    return GraphitiLocalModelProbeResult(
        ok=True,
        acceptable=True,
        status="structured_output_ready",
        config=config.to_dict(),
        latency_ms=_elapsed_ms(started),
        request=_contract_request_snapshot(),
        parsed=parsed,
        raw_text=raw_text,
    )


def _contract_request_snapshot() -> dict[str, Any]:
    return {
        "task": "graphiti_local_structured_output_contract",
        "required_top_level_keys": ["entities", "relationships", "evidence_refs"],
        "expected_entity_ids": ["weather_card", "voice_queue_guard"],
        "expected_relationship_types": ["FAILED_ON", "FIXED_BY", "MEASURED_BY"],
        "evidence_refs": ["trace:weather-queue:001", "pytest:test_voice_transcribe"],
    }


async def _call_local_chat_completion(config: GraphitiLocalModelProbeConfig) -> str:
    url = _chat_completions_url(config.llm_base_url)
    payload = {
        "model": config.llm_model,
        "messages": [
            {
                "role": "system",
                "content": "Return only one valid JSON object. Do not include markdown, prose, comments, or trailing text.",
            },
            {
                "role": "user",
                "content": (
                    "Extract Graphiti-style memory facts from this evidence. Required JSON keys: "
                    "entities, relationships, evidence_refs. Text: Zoe weather_card FAILED_ON "
                    "mobile_dashboard_render. The fix voice_queue_guard FIXED_BY duplicate_weather_response "
                    "and was MEASURED_BY pytest:test_voice_transcribe. Evidence refs: "
                    "trace:weather-queue:001 and pytest:test_voice_transcribe."
                ),
            },
        ],
        "temperature": 0,
        "max_tokens": config.max_tokens,
    }
    async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
    return _extract_chat_text(data)


def _chat_completions_url(base_url: str) -> str:
    stripped = base_url.rstrip("/")
    parsed = urlparse(stripped)
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        return stripped
    base = stripped + "/"
    if path.endswith("/v1"):
        return urljoin(base, "chat/completions")
    if path in {"", "/"}:
        return urljoin(base, "v1/chat/completions")
    raise GraphitiLocalModelProbeError(
        "GRAPHITI_LLM_BASE_URL must be an OpenAI-compatible root, /v1 base, or /chat/completions URL"
    )


def _extract_chat_text(payload: Any) -> str:
    if not isinstance(payload, MappingABC):
        raise ValueError("chat completion response was not an object")
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, MappingABC):
            message = first.get("message")
            if isinstance(message, MappingABC) and message.get("content") is not None:
                return str(message.get("content"))
            if first.get("text") is not None:
                return str(first.get("text"))
    raise ValueError("chat completion response did not contain assistant text")


def _parse_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    candidates = [stripped, *_balanced_json_candidates(stripped)]
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _balanced_json_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    depth = 0
    start: int | None = None
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start : index + 1])
                start = None
    return candidates


def _validate_contract_payload(payload: Mapping[str, Any]) -> str | None:
    entities = payload.get("entities")
    relationships = payload.get("relationships")
    evidence_refs = payload.get("evidence_refs")
    if not isinstance(entities, list) or not entities:
        return "entities must be a non-empty list"
    if not isinstance(relationships, list) or not relationships:
        return "relationships must be a non-empty list"
    if not isinstance(evidence_refs, list) or not evidence_refs:
        return "evidence_refs must be a non-empty list"

    normalized_entities = {_entity_id(entity) for entity in entities}
    missing_entities = {"weather_card", "voice_queue_guard"} - normalized_entities
    if missing_entities:
        return "missing expected entity id(s): " + ", ".join(sorted(missing_entities))

    rel_types = {_relationship_type(rel) for rel in relationships if isinstance(rel, MappingABC)}
    missing_rel_types = {"FAILED_ON", "FIXED_BY", "MEASURED_BY"} - rel_types
    if missing_rel_types:
        return "missing expected relationship type(s): " + ", ".join(sorted(missing_rel_types))

    expected_evidence = {"trace:weather-queue:001", "pytest:test_voice_transcribe"}
    observed_evidence = {str(ref) for ref in evidence_refs}
    if not expected_evidence.issubset(observed_evidence):
        return "missing expected evidence ref(s): " + ", ".join(sorted(expected_evidence - observed_evidence))
    return None


def _entity_id(entity: Any) -> str:
    if isinstance(entity, MappingABC):
        raw = entity.get("id") or entity.get("name") or entity.get("entity_id")
    else:
        raw = entity
    return _normalize_identifier(raw)


def _relationship_type(relationship: Mapping[str, Any]) -> str:
    raw = relationship.get("type") or relationship.get("relationship") or relationship.get("predicate")
    return str(raw or "").strip().upper()


def _normalize_identifier(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _env_bool(value: str | None, *, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Unrecognized boolean env var value: {value!r}")


def _env_bool_snapshot(value: str | None, *, default: bool) -> bool | str:
    try:
        return _env_bool(value, default=default)
    except ValueError:
        return str(value)


def _env_float(value: str | None, *, default: float) -> float:
    if value is None or str(value).strip() == "":
        return default
    return float(str(value).strip())


def _env_float_snapshot(value: str | None, *, default: float) -> float | str:
    try:
        return _env_float(value, default=default)
    except ValueError:
        return str(value)


def _env_int(value: str | None, *, default: int) -> int:
    if value is None or str(value).strip() == "":
        return default
    return int(str(value).strip())


def _env_int_snapshot(value: str | None, *, default: int) -> int | str:
    try:
        return _env_int(value, default=default)
    except ValueError:
        return str(value)


def probe_graphiti_local_model_contract_sync(
    *,
    env: Mapping[str, str] | None = None,
    run: bool | None = None,
) -> dict[str, Any]:
    """CLI-only sync wrapper; async callers should await the probe."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(probe_graphiti_local_model_contract(env=env, run=run)).to_dict()

    raise RuntimeError("probe_graphiti_local_model_contract_sync cannot be called from a running event loop")


__all__ = [
    "GraphitiLocalModelProbeConfig",
    "GraphitiLocalModelProbeError",
    "GraphitiLocalModelProbeResult",
    "probe_graphiti_local_model_contract",
    "probe_graphiti_local_model_contract_sync",
]
