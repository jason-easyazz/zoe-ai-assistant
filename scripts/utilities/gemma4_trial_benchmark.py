#!/usr/bin/env python3
"""Run a small reproducible llama.cpp trial and capture Jetson resource data."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROMPTS = (
    "Reply with exactly: ZOE_OK",
    "In two short sentences, explain why the sky appears blue.",
    "Jason has 17 batteries and uses 5. How many remain? Give only the number.",
)


def request_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def command_output(*command: str) -> str:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()


def resources() -> dict[str, Any]:
    return {
        "free": command_output("free", "-h"),
        "processes": command_output(
            "ps", "-eo", "pid,rss,vsz,cmd", "--sort=-rss"
        ).splitlines()[:12],
        "tegrastats": command_output(
            "timeout", "2", "tegrastats", "--interval", "1000"
        ),
    }


def file_data(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def run_case(
    base_url: str,
    payload: dict[str, Any],
    label: str,
    timeout: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = request_json(
            f"{base_url}/v1/chat/completions",
            payload,
            timeout,
        )
        elapsed = time.perf_counter() - started
        choice = response["choices"][0]["message"]
        return {
            "label": label,
            "ok": True,
            "seconds": round(elapsed, 3),
            "content": choice.get("content", ""),
            "tool_calls": choice.get("tool_calls", []),
            "usage": response.get("usage", {}),
        }
    except (OSError, KeyError, IndexError, urllib.error.HTTPError) as exc:
        return {
            "label": label,
            "ok": False,
            "seconds": round(time.perf_counter() - started, 3),
            "error": str(exc),
        }


def benchmark(
    base_url: str,
    timeout: float,
    image: Path | None,
    audio: Path | None,
) -> dict[str, Any]:
    before = resources()
    results: list[dict[str, Any]] = []
    for prompt in PROMPTS:
        result = run_case(
            base_url,
            {
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 96,
                "stream": False,
            },
            f"text: {prompt}",
            timeout,
        )
        result["prompt"] = prompt
        results.append(result)

    results.append(
        run_case(
            base_url,
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "Look up the temperature in Perth.",
                    }
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "description": "Get weather for a city.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "city": {"type": "string"},
                                },
                                "required": ["city"],
                            },
                        },
                    }
                ],
                "tool_choice": "required",
                "temperature": 0,
                "max_tokens": 96,
                "stream": False,
            },
            "tool_call",
            timeout,
        )
    )

    if image:
        results.append(
            run_case(
                base_url,
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "Describe the main object and its dominant "
                                        "color in one sentence."
                                    ),
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": (
                                            "data:image/png;base64,"
                                            f"{file_data(image)}"
                                        )
                                    },
                                },
                            ],
                        }
                    ],
                    "temperature": 0,
                    "max_tokens": 96,
                    "stream": False,
                },
                "image",
                timeout,
            )
        )

    if audio:
        results.append(
            run_case(
                base_url,
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "Transcribe this audio, then answer its "
                                        "spoken request briefly."
                                    ),
                                },
                                {
                                    "type": "input_audio",
                                    "input_audio": {
                                        "data": file_data(audio),
                                        "format": "wav",
                                    },
                                },
                            ],
                        }
                    ],
                    "temperature": 0,
                    "max_tokens": 128,
                    "stream": False,
                },
                "native_audio",
                timeout,
            )
        )

    try:
        with urllib.request.urlopen(f"{base_url}/props", timeout=10) as response:
            props = json.load(response)
    except (OSError, json.JSONDecodeError) as exc:
        props = {"error": str(exc)}

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "props": {
            "model_path": props.get("model_path"),
            "build_info": props.get("build_info"),
            "modalities": props.get("modalities"),
            "n_ctx": (props.get("default_generation_settings") or {}).get("n_ctx"),
        },
        "before": before,
        "results": results,
        "after": resources(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--audio", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            os.environ.get(
                "ZOE_BENCHMARK_OUTPUT",
                f"/home/zoe/benchmarks/gemma4-{int(time.time())}.json",
            )
        ),
    )
    args = parser.parse_args()

    report = benchmark(
        args.base_url.rstrip("/"),
        args.timeout,
        args.image,
        args.audio,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0 if all(item["ok"] for item in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
