"""The retired OpenClaw credential writers stay deleted (auth audit 2026-09-27).

Two flows wrote credentials for a runtime that no longer runs:

  * "Connect ChatGPT" — a chat intent that ran OpenAI's device-code flow and
    wrote the tokens to ~/.openclaw/agents/main/agent/auth-profiles.json (and
    ~/.hermes/auth.json), with no role check on the path;
  * POST /api/openclaw/telegram/setup — an admin-typed bot token written into
    ~/.openclaw/openclaw.json (the BotFather wizard in chat).

Nothing consumed either. These pins go red if any piece is reintroduced:
the intent, the stream handler, the file writers, or a UI entry point.
Source-level on purpose: importing routers.chat pulls the full service stack,
which the ci_safe lane does not install.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep green; opts into validate.yml's `-m ci_safe` lane

import ast
from pathlib import Path

import intent_router

ZOE_DATA = Path(__file__).resolve().parents[1]
DIST = ZOE_DATA.parent / "zoe-ui" / "dist"


@pytest.mark.parametrize("utterance", [
    "connect chatgpt",
    "can you connect my openai account",
    "set up codex",
    "link gpt",
])
def test_connect_chatgpt_intent_is_gone(utterance):
    intent = intent_router.detect_intent(utterance, log_miss=False)
    assert intent is None or intent.name != "connect_chatgpt"


def test_intent_router_has_no_chatgpt_connect_symbols():
    src = (ZOE_DATA / "intent_router.py").read_text()
    assert "connect_chatgpt" not in src
    assert not hasattr(intent_router, "_CONNECT_CHATGPT_RE")


def _top_level_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def test_chat_router_no_longer_writes_openclaw_or_hermes_credentials():
    chat = ZOE_DATA / "routers" / "chat.py"
    names = _top_level_names(chat)
    for gone in ("_chatgpt_connect_flow", "_write_hermes_codex_token", "_restart_hermes",
                 "_CODEX_AUTH_PROFILES_PATH", "_CODEX_CLIENT_ID", "_HERMES_AUTH_PATH"):
        assert gone not in names, gone
    src = chat.read_text()
    assert "auth-profiles.json" not in src
    assert "connect_chatgpt" not in src


def test_openclaw_router_no_longer_takes_a_bot_token():
    names = _top_level_names(ZOE_DATA / "routers" / "openclaw.py")
    for gone in ("telegram_setup", "telegram_status", "_save_telegram_token", "_validate_bot_token"):
        assert gone not in names, gone


def test_brain_no_longer_offers_the_telegram_wizard_tool():
    src = (ZOE_DATA / "zoe_agent.py").read_text()
    assert "setup_telegram" not in src
    assert "telegram_setup" not in src


@pytest.mark.parametrize("page", ["chat.html", "touch/chat.html"])
def test_ui_entry_points_are_gone(page):
    html = (DIST / page).read_text()
    assert "/api/openclaw/telegram/setup" not in html
    assert "renderTelegramSetup" not in html
    assert "zoe.chatgpt_connect" not in html
    assert "handleChatGPTConnect" not in html
