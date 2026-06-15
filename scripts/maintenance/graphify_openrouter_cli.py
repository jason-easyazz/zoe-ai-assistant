#!/usr/bin/env python3
"""Run Graphify with Zoe's OpenRouter backend registered at runtime."""
from __future__ import annotations

import os


def register_openrouter_backend() -> None:
    from graphify import llm
    # Verified against the installed graphify CLI: provider selection reads this registry.
    llm.BACKENDS["openrouter"] = {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": os.environ.get("GRAPHIFY_OPENROUTER_MODEL", "openai/gpt-4.1-mini"),
        "env_key": "OPENROUTER_API_KEY",
        "model_env_key": "GRAPHIFY_OPENROUTER_MODEL",
        "pricing": {"input": 0.0, "output": 0.0},
        "temperature": 0,
        "max_tokens": 16384,
    }


def main() -> int:
    register_openrouter_backend()
    from graphify.__main__ import main as graphify_main
    return graphify_main()


if __name__ == "__main__":
    raise SystemExit(main())
