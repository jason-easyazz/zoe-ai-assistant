"""The untrusted-quote fence is one contract held in three runtimes - pin the copies equal (2026-10-19).

Producer (the Telegram bot, TypeScript), gatekeeper (zoe-data, Python: keeps a fenced message off the deterministic intent fast
path) and enforcer (the Flue brain, TypeScript: taints the turn so writes and web search refuse). If the BEGIN line drifts in
any one copy, the brain no longer recognises the fence and a forwarded message is executed as the member's own command again -
with every unit test in each runtime still green. Static: reads source files, imports one stdlib-only module, no network.

Negative control (run by hand when written): change one character of the BEGIN marker in the Telegram copy -> red.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
BRAIN = ROOT / "labs/flue-zoe-brain-2x/src/untrusted-content.ts"
BOT = ROOT / "labs/flue-zoe-telegram-2x/src/untrusted.ts"
DATA = ROOT / "services/zoe-data/untrusted_content.py"
CHAT = ROOT / "services/zoe-data/routers/chat.py"


def _data_module():
    spec = importlib.util.spec_from_file_location("zoe_untrusted_content_pin", DATA)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ts_markers(src: str, source: str) -> tuple[str, str]:
    m = re.search(source + r"\s*:\s*\{\s*begin:\s*'([^']+)',\s*end:\s*'([^']+)'\s*\}", src)
    assert m, f"no {source} marker pair found"
    return m.group(1), m.group(2)


def _ts_const(src: str, name: str) -> str:
    m = re.search(r"export const " + name + r"\s*=\s*((?:'[^']*'\s*\+?\s*)+);", src)
    assert m, f"{name} not found"
    return "".join(re.findall(r"'([^']*)'", m.group(1)))


def _ts_regexes(src: str) -> dict[str, str]:
    return dict(re.findall(r"^const ([A-Z_]+_RE) = (/.+/[gimsuy]*);$", src, re.M))


def test_the_three_copies_of_the_telegram_markers_are_byte_equal():
    data = _data_module()
    brain, bot = BRAIN.read_text(), BOT.read_text()
    assert _ts_markers(brain, "telegram") == _ts_markers(bot, "telegram") == data.UNTRUSTED_QUOTE_MARKERS["telegram"]
    assert _ts_markers(brain, "omi") == data.UNTRUSTED_QUOTE_MARKERS["omi"]


def test_the_bot_fences_exactly_as_the_brain_does():
    brain, bot = BRAIN.read_text(), BOT.read_text()
    assert _ts_const(brain, "UNTRUSTED_QUOTE_PREAMBLE") == _ts_const(bot, "UNTRUSTED_QUOTE_PREAMBLE")
    assert re.search(r"QUOTE_TEXT_MAX = (\d+)", brain).group(1) == re.search(r"QUOTE_TEXT_MAX = (\d+)", bot).group(1)
    brain_re, bot_re = _ts_regexes(brain), _ts_regexes(bot)
    for name in ("TAG_RE", "ANGLE_ENTITY_RE", "ANGLE_RE", "INST_RE", "CONTROL_RE", "ROLE_RE"):
        assert brain_re.get(name) and brain_re.get(name) == bot_re.get(name), name


def test_zoe_data_recognises_the_fence_as_a_whole_line_only():
    data = _data_module()
    begin, end = data.UNTRUSTED_QUOTE_MARKERS["telegram"]
    assert data.untrusted_source_in(f"(I'm forwarding a message)\nPREAMBLE\n{begin}\nadd milk\n{end}") == "telegram"
    assert data.untrusted_source_in(f"what does {begin} mean?") is None          # prose mentioning it does not count
    assert data.untrusted_source_in("add milk to the shopping list") is None
    assert data.untrusted_source_in(None) is None


def test_chat_keeps_a_fenced_message_off_the_intent_fast_path_in_both_paths():
    """Both /api/chat paths (stream + non-stream) decide ``use_intent_fast_path``; each must veto it for a fenced message."""
    src = CHAT.read_text()
    decisions = [m.start() for m in re.finditer(r"use_intent_fast_path = \(not force_openclaw\) and _ALL_TOOLS_ENABLED", src)]
    assert len(decisions) == 2, decisions
    for start in decisions:
        window = src[start:start + 1600]
        guard = window.find("if untrusted_content.untrusted_source_in(message_for_processing):")
        fast = window.find("if use_intent_fast_path:")
        assert 0 <= guard < fast, "the fence veto must come before the fast path runs"
        assert "use_intent_fast_path = False" in window[guard:fast]
