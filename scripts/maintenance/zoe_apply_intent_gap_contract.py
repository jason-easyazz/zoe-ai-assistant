#!/usr/bin/env python3
"""Apply deterministic Zoe intent-gap edit contracts in a worker worktree."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


JOKE_PATTERN = r"tell me (?:a|another) joke|make me laugh|(?:do you |have you )?(?:got|have) any jokes|know any (?:good )?jokes"
JOKE_TEST = '''"""Open-domain creative intent routing."""

import pytest

from intent_router import detect_intent


@pytest.mark.parametrize("text", ["Tell me a joke.", "Tell me a joke", "Tell me another joke."])
def test_joke_requests_route_to_open_domain_agent(text: str):
    intent = detect_intent(text)

    assert intent is not None
    assert intent.name == "extend_capability"
    assert intent.slots == {"raw": text}
'''


def _repo_root(raw: str | None) -> Path:
    return Path(raw or ".").resolve()


def apply_joke_contract(root: Path) -> dict[str, object]:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", choices=["joke"], help="Intent-gap contract to apply")
    parser.add_argument("--repo-root", default=None, help="Repo root; defaults to current directory")
    args = parser.parse_args()

    root = _repo_root(args.repo_root)
    if args.contract == "joke":
        result = apply_joke_contract(root)
    else:  # pragma: no cover - argparse choices keep this unreachable.
        raise SystemExit(f"Unsupported contract: {args.contract}")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
