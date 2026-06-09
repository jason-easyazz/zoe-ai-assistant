#!/usr/bin/env python3
"""Validate Zoe memory stays offline-only by default.

This audit is intentionally narrow: Zoe may use cloud providers for other
operator tooling, but durable Zoe memory must not depend on public/cloud LLM
providers or blind auto-retain defaults.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ZOE_DATA = PROJECT_ROOT / "services" / "zoe-data"

sys.path.insert(0, str(ZOE_DATA))

from hindsight_memory import HindsightConfig, HindsightOfflineConfigError  # noqa: E402

RED = "\033[91m"
GREEN = "\033[92m"
BLUE = "\033[94m"
RESET = "\033[0m"

ACTIVE_MEMORY_GLOBS = (
    "services/zoe-data/*memory*.py",
    "services/zoe-data/*hindsight*.py",
    "services/zoe-data/zoe_memory*.py",
    "services/zoe-data/memory_*.py",
    "services/zoe-data/routers/memories.py",
    "docs/adr/ADR-hindsight-bakeoff.md",
    "docs/adr/ADR-zoe-memory-layer.md",
    "docs/strategy/zoe-evolution-harness-plan.md",
    "docs/strategy/zoe-evolution-harness-status.md",
    "docs/architecture/zoe-harness-current-inventory.md",
)

_CLOUD_PROVIDER_PATTERN = (
    r"HINDSIGHT_API_LLM_PROVIDER\s*[:=]\s*[\"']?"
    r"(?:anthropic|gemini|groq|openrouter|bedrock|vertexai|ollama-cloud)[\"']?"
)

_CLOUD_EMBEDDINGS_PROVIDER_PATTERN = (
    r"HINDSIGHT_API_EMBEDDINGS_PROVIDER\s*[:=]\s*[\"']?"
    r"(?:cohere|gemini|google|litellm|openai|openrouter|vertexai|zeroentropy)[\"']?"
)

DENIED_MEMORY_PATTERNS = (
    re.compile(r"HINDSIGHT_OFFLINE_ONLY\s*[:=]\s*(?:false|0|no|off)", re.I),
    re.compile(r"HINDSIGHT_AUTO_RETAIN\s*[:=]\s*(?:true|1|yes|on)", re.I),
    re.compile(_CLOUD_PROVIDER_PATTERN, re.I),
    re.compile(_CLOUD_EMBEDDINGS_PROVIDER_PATTERN, re.I),
    re.compile(r"(?:OPENAI|ANTHROPIC|GEMINI|GROQ|OPENROUTER)_API_KEY", re.I),
)

ALLOWED_CLOUD_KEY_CONTEXT = (
    "provider == \"openai\" and self.llm_base_url",
    "embeddings_provider == \"openai\" and self.embeddings_base_url",
    "provider {provider!r} is not allowed",
    "embeddings_provider {embeddings_provider!r} is not allowed",
)


def _fail(message: str) -> int:
    print(f"{RED}ERROR:{RESET} {message}")
    return 1


def _assert_hindsight_policy() -> list[str]:
    issues: list[str] = []

    default = HindsightConfig.from_env({})
    if default.enabled:
        issues.append("Hindsight must be disabled by default")
    if not default.offline_only:
        issues.append("Hindsight offline_only must default to true")
    if default.auto_retain:
        issues.append("Hindsight auto_retain must default to false")
    if default.base_url != "http://127.0.0.1:8888":
        issues.append("Hindsight default base_url must stay localhost")
    if default.embeddings_provider != "local":
        issues.append("Hindsight embeddings_provider must default to local")

    rejected_cases = [
        {
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "openai",
        },
        {
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "anthropic",
        },
        {
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "https://memory.example.com",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
        },
        {
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
            "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "openai",
        },
        {
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
            "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "tei",
        },
    ]
    for env in rejected_cases:
        try:
            HindsightConfig.from_env(env)
        except HindsightOfflineConfigError:
            continue
        issues.append(f"Hindsight accepted unsafe memory env: {env}")

    accepted_cases = [
        {
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
        },
        {
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "openai",
            "HINDSIGHT_API_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
        },
        {
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
            "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "onnx",
        },
        {
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
            "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "tei",
            "HINDSIGHT_API_EMBEDDINGS_TEI_URL": "http://127.0.0.1:8080",
        },
        {
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
            "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "openai",
            "HINDSIGHT_API_EMBEDDINGS_OPENAI_BASE_URL": "http://127.0.0.1:11434/v1",
        },
    ]
    for env in accepted_cases:
        try:
            HindsightConfig.from_env(env)
        except HindsightOfflineConfigError as exc:
            issues.append(f"Hindsight rejected safe local memory env {env}: {exc}")

    return issues


def _active_memory_files() -> list[Path]:
    files: set[Path] = set()
    for pattern in ACTIVE_MEMORY_GLOBS:
        files.update(PROJECT_ROOT.glob(pattern))
    return sorted(path for path in files if path.is_file())


def _is_allowed_line(line: str) -> bool:
    return any(marker in line for marker in ALLOWED_CLOUD_KEY_CONTEXT)


def _scan_active_memory_files() -> list[str]:
    issues: list[str] = []
    for path in _active_memory_files():
        rel = path.relative_to(PROJECT_ROOT)
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if _is_allowed_line(line):
                continue
            for pattern in DENIED_MEMORY_PATTERNS:
                if pattern.search(line):
                    issues.append(f"{rel}:{lineno}: offline memory risk: {line.strip()}")
    return issues


def main() -> int:
    print(f"{BLUE}Validating offline-only Zoe memory policy...{RESET}")
    issues = []
    issues.extend(_assert_hindsight_policy())
    issues.extend(_scan_active_memory_files())

    if issues:
        for issue in issues:
            print(f"{RED}x{RESET} {issue}")
        return _fail(f"offline memory validation found {len(issues)} issue(s)")

    print(f"{GREEN}ok{RESET} Hindsight defaults are disabled, offline-only, localhost, local embeddings, and no auto-retain")
    print(
        f"{GREEN}ok{RESET} Public/cloud memory LLM and embedding providers are rejected "
        "unless OpenAI-compatible/TEI points to local/private base URL"
    )
    print(f"{GREEN}ok{RESET} Active memory files contain no committed cloud-memory defaults or API-key dependencies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
