#!/usr/bin/env python3
"""
verify-bonsai-tools.py
Runs 3 tool-call prompts against a running Bonsai llama-server to verify
tool calling works correctly before promoting to production.

Usage: python3 ~/bin/verify-bonsai-tools.py [--port 11435]
Exit code: 0 = all passed, 1 = one or more failed
"""
import argparse
import json
import sys
import urllib.request
import urllib.error

TOOLS_LIST = [
    {
        "type": "function",
        "function": {
            "name": "add_to_list",
            "description": "Add an item to a named list (shopping, todo, etc.)",
            "parameters": {
                "type": "object",
                "properties": {
                    "list_name": {"type": "string"},
                    "item": {"type": "string"},
                },
                "required": ["list_name", "item"],
            },
        },
    }
]

TOOLS_HA = [
    {
        "type": "function",
        "function": {
            "name": "control_device",
            "description": "Control a smart home device",
            "parameters": {
                "type": "object",
                "properties": {
                    "device": {"type": "string"},
                    "action": {"type": "string", "enum": ["on", "off", "toggle", "dim"]},
                },
                "required": ["device", "action"],
            },
        },
    }
]

TOOLS_MULTI = [
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": "Add an event to the calendar",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "date": {"type": "string"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_reminder",
            "description": "Set a reminder",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "time": {"type": "string"},
                },
                "required": ["text"],
            },
        },
    },
]


def post(port, body, timeout=60):
    url = f"http://127.0.0.1:{port}/v1/chat/completions"
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def check_health(port):
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode()).get("status") == "ok"
    except Exception:
        return False


def run_tests(port):
    print(f"\n{'='*55}")
    print(f"  BONSAI 8B TOOL-CALL VERIFICATION  (port {port})")
    print(f"{'='*55}")

    if not check_health(port):
        print(f"ERROR: server on :{port} not healthy. Is Bonsai running?")
        print(f"  Start with: nohup bash ~/bin/start-llama-bonsai-8b.sh >> ~/logs/llama-server-bonsai.log 2>&1 &")
        sys.exit(1)

    tests = [
        {
            "name": "Single tool — shopping list",
            "body": {
                "model": "zoe-local",
                "messages": [{"role": "user", "content": "Add oat milk to the shopping list."}],
                "tools": TOOLS_LIST,
                "tool_choice": "auto",
                "max_tokens": 256,
                "temperature": 0.1,
            },
            "check": lambda r: (
                r["choices"][0].get("finish_reason") == "tool_calls"
                and r["choices"][0]["message"].get("tool_calls")
                and r["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "add_to_list"
                and "milk" in json.loads(r["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]).get("item", "").lower()
            ),
        },
        {
            "name": "Single tool — HA light control",
            "body": {
                "model": "zoe-local",
                "messages": [{"role": "user", "content": "Turn on the kitchen lights."}],
                "tools": TOOLS_HA,
                "tool_choice": "auto",
                "max_tokens": 256,
                "temperature": 0.1,
            },
            "check": lambda r: (
                r["choices"][0].get("finish_reason") == "tool_calls"
                and r["choices"][0]["message"].get("tool_calls")
                and r["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "control_device"
                and json.loads(r["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]).get("action") == "on"
            ),
        },
        {
            "name": "Multi-tool — calendar + reminder",
            "body": {
                "model": "zoe-local",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Add a dentist appointment to my calendar for Friday, "
                            "and also set a reminder to call the clinic tomorrow morning."
                        ),
                    }
                ],
                "tools": TOOLS_MULTI,
                "tool_choice": "auto",
                "parallel_tool_calls": True,
                "max_tokens": 512,
                "temperature": 0.1,
            },
            "check": lambda r: (
                len(r["choices"][0]["message"].get("tool_calls", [])) >= 2
                and {c["function"]["name"] for c in r["choices"][0]["message"]["tool_calls"]}
                == {"create_calendar_event", "create_reminder"}
            ),
        },
    ]

    passed = 0
    for i, t in enumerate(tests, 1):
        print(f"\n[{i}/3] {t['name']}")
        try:
            resp = post(port, t["body"])
            ok = t["check"](resp)
            calls = resp["choices"][0]["message"].get("tool_calls", [])
            call_summary = ", ".join(
                f"{c['function']['name']}({c['function']['arguments'][:60]}...)"
                if len(c['function']['arguments']) > 60
                else f"{c['function']['name']}({c['function']['arguments']})"
                for c in calls
            ) if calls else "(no tool calls)"
            if ok:
                print(f"  PASS: {call_summary}")
                passed += 1
            else:
                finish = resp["choices"][0].get("finish_reason")
                content = resp["choices"][0]["message"].get("content", "")[:120]
                print(f"  FAIL: finish_reason={finish}")
                print(f"        calls={call_summary}")
                if content:
                    print(f"        content={content!r}")
        except Exception as e:
            print(f"  ERROR: {e}")

    print(f"\n{'─'*55}")
    print(f"  RESULT: {passed}/3 passed")
    print(f"{'─'*55}")

    if passed == 3:
        print("\n  ALL TOOL CALLS PASSED. Ready to consider promoting to production.")
        print("  Run the full benchmark first: bash ~/bin/run-bonsai-benchmark.sh")
        return True
    elif passed >= 2:
        print("\n  PARTIAL: Single-tool calls work. Multi-tool needs investigation.")
        print("  Check that --chat-template-kwargs '{\"enable_thinking\":false}' is active.")
        return False
    else:
        print("\n  FAILED. Check that enable_thinking=false is set in the start script.")
        print("  Verify: curl -s http://127.0.0.1:11435/props | python3 -m json.tool")
        return False


def main():
    parser = argparse.ArgumentParser(description="Verify Bonsai 8B tool calling on port 11435")
    parser.add_argument("--port", type=int, default=11435)
    args = parser.parse_args()
    ok = run_tests(args.port)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
