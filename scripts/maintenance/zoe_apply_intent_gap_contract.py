#!/usr/bin/env python3
"""Apply deterministic Zoe intent-gap edit contracts in a worker worktree."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


JOKE_PATTERN = r"tell me (?:a|another) joke|make me laugh|(?:do you |have you )?(?:got|have) any jokes|know any (?:good )?jokes"
SAY_EXACTLY_PATTERN = r"say exactly[: ]+(?:.+)"
SAY_EXACTLY_TOKEN = r"say exactly[: ]+"
JOKE_TEST = '''"""Open-domain creative intent routing."""

import pytest

from intent_router import detect_intent


@pytest.mark.parametrize("text", ["Tell me a joke.", "Tell me a joke", "Tell me another joke.", "make me laugh", "do you have any jokes?", "have you got any jokes?", "know any good jokes?"])
def test_joke_requests_route_to_open_domain_agent(text: str):
    intent = detect_intent(text)

    assert intent is not None
    assert intent.name == "extend_capability"
    assert intent.slots == {"raw": text}
'''

SAY_EXACTLY_TEST = '''"""Exact repeat intent routing."""

from intent_router import detect_intent


def test_say_exactly_routes_to_open_domain_agent():
    text = "Say exactly: Zoe chat integration ok"
    intent = detect_intent(text)

    assert intent is not None
    assert intent.name == "extend_capability"
    assert intent.slots == {"raw": text}
'''


def _repo_root(raw: str | None) -> Path:
    return Path(raw or ".").resolve()


def _live_repo_root() -> Path:
    return Path(os.environ.get("ZOE_LIVE_REPO_ROOT", "/home/zoe/assistant")).resolve()


def _guard_not_live_root(root: Path, *, allow_live_root: bool = False) -> None:
    live_root = _live_repo_root()
    if allow_live_root or root.resolve() != live_root:
        return
    raise SystemExit(
        f"Refusing to mutate live checkout at {live_root}; run from the task worktree "
        "or pass --allow-live-root for an intentional operator repair"
    )


def apply_joke_contract(root: Path, *, allow_live_root: bool = False) -> dict[str, object]:
    _guard_not_live_root(root, allow_live_root=allow_live_root)
    router_path = root / "services/zoe-data/intent_router.py"
    test_path = root / "services/zoe-data/tests/test_intent_open_domain.py"
    if not router_path.exists():
        raise SystemExit(
            f"intent_router.py not found at {router_path}; pass --repo-root pointing at the repo root"
        )
    router = router_path.read_text(encoding="utf-8")
    changed: list[str] = []

    if JOKE_PATTERN not in router:
        needle = (
            '    r"can you explain|set up (?:a )?new automation|what is happening in)",\n'
        )
        replacement = (
            '    r"can you explain|set up (?:a )?new automation|what is happening in|"\n'
            f'    r"{JOKE_PATTERN})",\n'
        )
        if needle not in router:
            raise SystemExit("Could not find _AGENT_CHAT_RE insertion line in intent_router.py")
        router_path.write_text(router.replace(needle, replacement, 1), encoding="utf-8")
        changed.append(str(router_path.relative_to(root)))

    existing_test = test_path.read_text(encoding="utf-8") if test_path.exists() else ""
    if "test_joke_requests_route_to_open_domain_agent" not in existing_test:
        test_path.parent.mkdir(parents=True, exist_ok=True)
        if existing_test.strip():
            test_path.write_text(
                f"{existing_test.rstrip()}\n\n\n{JOKE_TEST}",
                encoding="utf-8",
            )
        else:
            test_path.write_text(JOKE_TEST, encoding="utf-8")
        changed.append(str(test_path.relative_to(root)))

    return {"contract": "joke-open-domain", "changed": changed, "idempotent": not changed}


def apply_say_exactly_contract(root: Path, *, allow_live_root: bool = False) -> dict[str, object]:
    _guard_not_live_root(root, allow_live_root=allow_live_root)
    router_path = root / "services/zoe-data/intent_router.py"
    test_path = root / "services/zoe-data/tests/test_intent_open_domain.py"
    if not router_path.exists():
        raise SystemExit(
            f"intent_router.py not found at {router_path}; pass --repo-root pointing at the repo root"
        )
    router = router_path.read_text(encoding="utf-8")
    changed: list[str] = []

    if SAY_EXACTLY_TOKEN not in router:
        needle = f'    r"{JOKE_PATTERN})",\n'
        replacement = f'    r"{JOKE_PATTERN}|"\n    r"{SAY_EXACTLY_PATTERN})",\n'
        if needle not in router:
            needle = '    r"can you explain|set up (?:a )?new automation|what is happening in)",\n'
            replacement = (
                '    r"can you explain|set up (?:a )?new automation|what is happening in|"\n'
                f'    r"{SAY_EXACTLY_PATTERN})",\n'
            )
        if needle not in router:
            raise SystemExit("Could not find _AGENT_CHAT_RE insertion line in intent_router.py")
        router_path.write_text(router.replace(needle, replacement, 1), encoding="utf-8")
        changed.append(str(router_path.relative_to(root)))

    existing_test = test_path.read_text(encoding="utf-8") if test_path.exists() else ""
    if "test_say_exactly_routes_to_open_domain_agent" not in existing_test:
        test_path.parent.mkdir(parents=True, exist_ok=True)
        if existing_test.strip():
            test_path.write_text(
                f"{existing_test.rstrip()}\n\n\n{SAY_EXACTLY_TEST}",
                encoding="utf-8",
            )
        else:
            test_path.write_text(SAY_EXACTLY_TEST, encoding="utf-8")
        changed.append(str(test_path.relative_to(root)))

    return {"contract": "say-exactly-open-domain", "changed": changed, "idempotent": not changed}


def _run_checked(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, check=True, text=True, capture_output=True)


def run_focused_checks(
    root: Path,
    result: dict[str, object],
    *,
    kanban_task: str | None = None,
    hermes_bin: str | None = None,
) -> dict[str, object]:
    checks = [
        ["python3", "-m", "py_compile", "services/zoe-data/intent_router.py"],
        [
            "python3",
            "-m",
            "pytest",
            "-q",
            "services/zoe-data/tests/test_intent_open_domain.py",
        ],
    ]
    for cmd in checks:
        _run_checked(cmd, cwd=root)

    clean = subprocess.run(["git", "diff", "--quiet"], cwd=root).returncode == 0
    focused = {
        "checks": ["py_compile:intent_router", "pytest:test_intent_open_domain"],
        "git_diff_clean": clean,
        "terminal_action": None,
    }
    if result.get("idempotent") is True and clean and kanban_task:
        reason = (
            "BLOCKER=ALREADY_COVERED: intent gap helper was idempotent and "
            "focused tests passed; no PR required"
        )
        _run_checked(
            [hermes_bin or "/home/zoe/.local/bin/hermes", "kanban", "block", kanban_task, reason],
            cwd=root,
        )
        focused["terminal_action"] = "kanban_block:ALREADY_COVERED"
    return focused


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", choices=["joke", "say_exactly"], help="Intent-gap contract to apply")
    parser.add_argument("--repo-root", default=None, help="Repo root; defaults to current directory")
    parser.add_argument(
        "--run-focused-checks",
        action="store_true",
        help="Run the focused compile/test checks after applying the contract",
    )
    parser.add_argument("--kanban-task", default=None, help="Kanban task id for ALREADY_COVERED terminal block")
    parser.add_argument("--hermes-bin", default=None, help="Hermes executable; defaults to /home/zoe/.local/bin/hermes")
    parser.add_argument(
        "--allow-live-root",
        action="store_true",
        help="Allow intentional operator mutation of the live /home/zoe/assistant checkout",
    )
    args = parser.parse_args()

    root = _repo_root(args.repo_root)
    if args.contract == "joke":
        result = apply_joke_contract(root, allow_live_root=args.allow_live_root)
    elif args.contract == "say_exactly":
        result = apply_say_exactly_contract(root, allow_live_root=args.allow_live_root)
    else:  # pragma: no cover - argparse choices keep this unreachable.
        raise SystemExit(f"Unsupported contract: {args.contract}")
    if args.run_focused_checks:
        result["focused_checks"] = run_focused_checks(
            root,
            result,
            kanban_task=args.kanban_task,
            hermes_bin=args.hermes_bin,
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
