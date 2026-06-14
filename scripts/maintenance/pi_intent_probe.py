#!/usr/bin/env python3
"""Measure Zoe's Pi/Gemma ambiguous-intent governor.

By default this script is read-only. Passing --write-local-model-config writes
~/.pi/agent/models.json for the local llama.cpp provider.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))

from pi_intent_classifier import classify_with_pi_intent_governor, pi_intent_status  # noqa: E402


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _write_local_model_config(*, model: str, base_url: str = "http://127.0.0.1:11434/v1") -> Path:
    path = Path.home() / ".pi" / "agent" / "models.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    payload = {
        "providers": {
            "ollama": {
                "baseUrl": base_url,
                "api": "openai-completions",
                "apiKey": "ollama",
                "compat": {"supportsDeveloperRole": False, "supportsReasoningEffort": False},
                "models": [
                    {
                        "id": model,
                        "name": f"Zoe Local {model}",
                        "input": ["text"],
                        "contextWindow": 131072,
                        "maxTokens": 2048,
                        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                    }
                ],
            }
        }
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    path.chmod(0o600)
    return path


async def _run_probe(text: str) -> dict:
    start = time.perf_counter()
    result = await classify_with_pi_intent_governor(text)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return {
        "text": text,
        "elapsed_ms": elapsed_ms,
        "classification": result.to_dict() if result else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Pi/Gemma ambiguous-intent classification")
    parser.add_argument("text", nargs="?", default="anything I should remember right now")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    parser.add_argument("--enable", action="store_true", help="Temporarily enable ZOE_PI_INTENT_ENABLED for this probe")
    parser.add_argument("--allow-execution", action="store_true", help="Temporarily set ZOE_PI_ALLOW_EXECUTION=true for this probe")
    parser.add_argument("--local-model-configured", action="store_true", help="Temporarily set ZOE_PI_LOCAL_MODEL_CONFIGURED=true for this probe")
    parser.add_argument("--timeout", type=float, default=None, help="Override ZOE_PI_INTENT_TIMEOUT_SECONDS")
    parser.add_argument("--model", default=None, help="Override ZOE_PI_INTENT_MODEL")
    parser.add_argument("--write-local-model-config", action="store_true", help="Write ~/.pi/agent/models.json for Zoe's local llama.cpp endpoint")
    args = parser.parse_args()

    _load_dotenv(ROOT / ".env")
    if args.enable:
        os.environ["ZOE_PI_INTENT_ENABLED"] = "true"
    if args.allow_execution:
        os.environ["ZOE_PI_ALLOW_EXECUTION"] = "true"
    if args.local_model_configured:
        os.environ["ZOE_PI_LOCAL_MODEL_CONFIGURED"] = "true"
    if args.timeout is not None:
        os.environ["ZOE_PI_INTENT_TIMEOUT_SECONDS"] = str(args.timeout)
    model = args.model or os.environ.get("ZOE_PI_INTENT_MODEL") or "gemma-4-E2B-it-Q4_K_M.gguf"
    if args.model:
        os.environ["ZOE_PI_INTENT_MODEL"] = args.model
    written_config = None
    if args.write_local_model_config:
        written_config = str(_write_local_model_config(model=model))

    payload = {
        "status": pi_intent_status(),
        "probe": asyncio.run(_run_probe(args.text)),
        "written_config": written_config,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        status = payload["status"]
        probe = payload["probe"]
        print(f"Pi intent status: {status.get('status')} ({status.get('reason') or 'ok'})")
        print(f"Elapsed: {probe['elapsed_ms']:.1f} ms")
        print(f"Classification: {probe['classification']}")
        if payload.get("written_config"):
            print(f"Wrote Pi model config: {payload['written_config']}")
    return 0 if payload["status"].get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
