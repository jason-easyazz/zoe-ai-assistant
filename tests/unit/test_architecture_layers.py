"""Layering rules for services/zoe-data, pinned by AST (pure source reading: imports no product module).

Four rules, each "true today with an explicit, exact-count allowlist of today's exceptions", so a NEW exception
fails while an existing one is documented:

1. UPWARD IMPORTS: a module outside ``routers/`` does not import from ``routers/``, and nothing but ``main.py`` imports
   ``main``. Routers are the top of the stack; a service importing one drags the web layer (and its import-time
   side effects) into the domain code and makes cycles likely.
2. WEB FRAMEWORK: ``fastapi`` / ``starlette`` are imported only in ``routers/``, ``middleware/``, ``main.py`` and the
   named modules below (mostly auth/guard helpers that raise ``HTTPException``).
3. OUTBOUND HTTP: ``httpx`` / ``requests`` / ``aiohttp`` / ``urllib3`` / ``urllib.request`` are imported only in
   the named modules, so a new network egress point is a deliberate, reviewed addition.
4. SETTINGS: direct ``os.environ`` / ``os.getenv`` reads whose key is NOT a literal ``ZOE_*`` name (a literal ``ZOE_*``
   key is already ratcheted by ``tests/unit/test_flag_registry.py``; this covers the rest and computed keys).
   New settings go through ``services/zoe-data/typed_env.py``.

Allowlists are module -> number of matching statements/sites, EXACT: a new exception fails, and so does
removing one without lowering the number (so the list cannot rot into slack). When a rule is red, the failure names
the module and line and says what to do.

Rejected on measurement (kept out on purpose): "nothing outside the door modules imports ``os_shadow``" (16 modules
import it, mostly as the sanctioned door hook; the real invariant, no import from ``labs/``, is pinned elsewhere) and
a raw-socket rule (``socket`` is imported by a handful of unrelated infra modules).

NEGATIVE CONTROLS (run 2026-11-28; also on the real tree by deleting one allowlist entry, and by raising one: each fails with the offending module:line, then passes when restored):
the ``test_control_*`` tests feed the SAME checker functions a synthetic tree that plants (1) a service importing
``routers.x`` and a non-main module importing ``main``, (2) a service importing ``fastapi``, (3) a module importing
``httpx``, (4) a module reading ``os.environ["DB_URL"]``; plus a tree that stays inside the rules and passes.
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

_ROOT = Path(__file__).resolve().parents[2] / "services" / "zoe-data"
_SKIP_DIRS = {"tests", "alembic", "__pycache__"}
_HTTP_ROOTS = {"httpx", "requests", "aiohttp", "urllib3"}
_FW_ROOTS = {"fastapi", "starlette"}

# --- allowlists: module (relative to services/zoe-data) -> exact number of matching statements/sites ---------------

UPWARD_IMPORTS = {
    "auth.py": 1,
    "biometric_scope.py": 1,
    "contacts_conversation.py": 1,
    "contacts_store.py": 1,
    "conversation_opener.py": 1,
    "correction_marker.py": 1,
    "first_boot_probe.py": 1,
    "intent_router.py": 5,
    "intercom.py": 1,
    "mcp_server.py": 4,
    "memory_digest.py": 1,
    "omi_recordings.py": 1,
    "omi_voices.py": 1,
    "panel_kiosk_credential.py": 1,
    "panel_review_cards.py": 2,
    "person_extractor.py": 2,
    "proactive/engine.py": 1,
    "relay_ingress.py": 3,
    "reminder_delivery.py": 1,
    "routers/system.py": 2,
    "skybridge_service.py": 6,
    "traffic_class.py": 1,
    "voice_enrollment.py": 1,
    "zoe_core_client.py": 1,
    "zoe_flue_client.py": 3,
}

FRAMEWORK_IMPORTS = {
    "alarm_voice.py": 1,
    "auth.py": 1,
    "biometric_scope.py": 1,
    "guest_policy.py": 1,
    "intent_router.py": 1,
    "mcp_server.py": 1,
    "memories_voice.py": 1,
    "music_jukebox.py": 1,
    "panel_principal.py": 1,
    "panel_review_cards.py": 1,
    "pending_suggestions.py": 2,
    "push.py": 1,
    "relay_ingress.py": 6,
    "reminder_service.py": 1,
}

HTTP_IMPORTS = {
    "agent_safety.py": 1,
    "agent_sync.py": 1,
    "auth.py": 1,
    "auth_handoff.py": 3,
    "brain_prefix_keeper.py": 1,
    "brain_scheduler.py": 2,
    "contact_backfill.py": 1,
    "conversation_notes.py": 1,
    "greploop_guard.py": 1,
    "greptile_client.py": 1,
    "hindsight_memory.py": 1,
    "home_runnables.py": 1,
    "intent_classifier_llm.py": 1,
    "intent_router.py": 4,
    "latent_intent_detector.py": 1,
    "log_throttle.py": 1,
    "ma_ondemand.py": 1,
    "main.py": 1,
    "mcp_server.py": 1,
    "memory_digest.py": 1,
    "memory_retire.py": 1,
    "multica_autopilot_sync.py": 1,
    "multica_client.py": 1,
    "multica_webhook_emitter.py": 1,
    "music_service.py": 1,
    "night_mind.py": 2,
    "nlu_extractor.py": 1,
    "omi_day_review.py": 2,
    "omi_repass_clean.py": 1,
    "omnigent_issue_executor.py": 1,
    "person_extractor_llm.py": 1,
    "presence/ha_presence.py": 1,
    "proactive/composer.py": 1,
    "router_two_stage.py": 1,
    "routers/auth.py": 1,
    "routers/chat.py": 1,
    "routers/ha_control.py": 1,
    "routers/music.py": 1,
    "routers/panel_auth.py": 1,
    "routers/panel_config.py": 1,
    "routers/system.py": 1,
    "routers/voice_tts.py": 1,
    "routers/weather.py": 1,
    "smart_home_service.py": 1,
    "startup_warmups.py": 1,
    "structural_reader.py": 1,
    "structural_verifier.py": 1,
    "system_stats.py": 1,
    "system_updates.py": 1,
    "telegram_notify.py": 1,
    "tts_waterfall.py": 1,
    "ui_compose.py": 1,
    "user_portrait.py": 1,
    "web_search_provider.py": 1,
    "ytmusic_signin.py": 1,
    "zoe_agent.py": 2,
    "zoe_flue_client.py": 6,
    "zoe_ui_components.py": 1,
}

# non-ZOE_ / computed-key environment reads (typed_env.py itself is exempt)
ENV_READS = {
    "agent_sync.py": 1,
    "async_subprocess.py": 1,
    "auth_handoff.py": 1,
    "contact_backfill.py": 1,
    "conversation_notes.py": 1,
    "database.py": 1,
    "db_pool.py": 1,
    "exact_words.py": 1,
    "executors/executor_queue_backend.py": 3,
    "executors/kanban_adapter.py": 1,
    "expert_dispatch.py": 1,
    "forget_session.py": 1,
    "gemma_endpoint.py": 1,
    "greploop_guard.py": 1,
    "greptile_client.py": 3,
    "intent_classifier_llm.py": 1,
    "intent_router.py": 1,
    "kanban_phase_budget.py": 3,
    "kv_miss_alert.py": 4,
    "latent_intent_detector.py": 1,
    "live_store_guard.py": 2,
    "logging_setup.py": 1,
    "ma_ondemand.py": 1,
    "main.py": 1,
    "mcp_server.py": 2,
    "memory_authority.py": 2,
    "memory_digest.py": 6,
    "memory_idle_consolidation.py": 1,
    "memory_residue.py": 1,
    "memory_retire.py": 1,
    "memory_service.py": 2,
    "multi_hop_recall.py": 1,
    "multica_autopilot_sync.py": 3,
    "multica_board_runner.py": 3,
    "multica_client.py": 4,
    "multica_webhook_emitter.py": 2,
    "music_oauth.py": 3,
    "music_service.py": 2,
    "night_mind.py": 8,
    "omi_day_review.py": 3,
    "panel_kiosk_credential.py": 1,
    "person_extractor_llm.py": 1,
    "pi_intent_lab.py": 5,
    "proactive/scheduler.py": 1,
    "restraint.py": 1,
    "router_retrain_ledger.py": 1,
    "routers/chat.py": 2,
    "routers/music.py": 2,
    "routers/pi_intent_lab.py": 2,
    "routers/skybridge.py": 3,
    "routers/system.py": 3,
    "routers/voice_livekit.py": 4,
    "routers/voice_tts.py": 1,
    "routers/weather.py": 1,
    "runtime_env.py": 1,
    "self_model.py": 3,
    "skybridge_service.py": 1,
    "structural_reader.py": 1,
    "structural_verifier.py": 1,
    "ui_compose.py": 1,
    "user_portrait.py": 4,
    "voice_settings.py": 1,
    "voice_turn.py": 1,
    "warm_speaker.py": 1,
    "write_claim_guard.py": 1,
    "ytmusic_signin.py": 2,
    "zoe_agent.py": 8,
    "zoe_flue_client.py": 2,
}


# --- the checkers (pure functions of a source tree; the negative controls reuse them) ---------------------------------

def _py_files(root: Path):
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root)
        if _SKIP_DIRS & set(rel.parts[:-1]):
            continue
        yield rel.as_posix(), p


def _parse(p: Path) -> ast.AST:
    return ast.parse(p.read_text(encoding="utf-8"))


def _import_roots(node: ast.AST) -> list[str]:
    """Absolute dotted module names a single import statement pulls in (``from routers import x`` -> routers.x)."""
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        return [node.module] + [f"{node.module}.{a.name}" for a in node.names]
    return []


def _import_sites(root: Path, matcher):
    """-> list of (module, lineno, dotted-name): one entry per import STATEMENT that matches (first matching name)."""
    out = []
    for rel, p in _py_files(root):
        for n in ast.walk(_parse(p)):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                for name in _import_roots(n):
                    if matcher(rel, name):
                        out.append((rel, n.lineno, name))
                        break
    return out


def find_upward_imports(root: Path):
    def m(rel, name):
        top = name.split(".")[0]
        if top == "routers":
            return not rel.startswith("routers/") and rel != "main.py"  # main.py mounts the routers
        return top == "main" and rel != "main.py"
    return _import_sites(root, m)


def find_framework_imports(root: Path):
    def m(rel, name):
        return name.split(".")[0] in _FW_ROOTS and not rel.startswith(("routers/", "middleware/")) and rel != "main.py"
    return _import_sites(root, m)


def find_http_imports(root: Path):
    def m(rel, name):
        return name.split(".")[0] in _HTTP_ROOTS or name == "urllib.request" or name.startswith("urllib.request.")
    return _import_sites(root, m)


def find_env_reads(root: Path):
    """Direct os.environ/os.getenv reads whose key is not a literal ZOE_* string -> list of (module, lineno, why)."""
    out = []
    for rel, p in _py_files(root):
        if rel == "typed_env.py":  # the sanctioned reader itself
            continue
        for n in ast.walk(_parse(p)):
            key = None
            hit = False
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                f = n.func
                if f.attr == "getenv" and isinstance(f.value, ast.Name) and f.value.id == "os":
                    hit, key = True, (n.args[0] if n.args else None)
                elif (f.attr in {"get", "pop", "setdefault"} and isinstance(f.value, ast.Attribute)
                      and f.value.attr == "environ" and isinstance(f.value.value, ast.Name) and f.value.value.id == "os"):
                    hit, key = True, (n.args[0] if n.args else None)
            elif (isinstance(n, ast.Subscript) and isinstance(n.value, ast.Attribute) and n.value.attr == "environ"
                  and isinstance(n.value.value, ast.Name) and n.value.value.id == "os"):
                hit, key = True, n.slice
            if not hit:
                continue
            if isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value.startswith("ZOE_"):
                continue
            out.append((rel, n.lineno, ast.unparse(key) if key is not None else "?"))
    return out


def judge(sites, allowed: dict[str, int], what: str, fix: str) -> list[str]:
    """Compare found sites against the exact-count allowlist; return human-readable problems (empty = clean)."""
    found = Counter(s[0] for s in sites)
    problems = []
    for mod, n in sorted(found.items()):
        if n > allowed.get(mod, 0):
            lines = ", ".join(f"{s[0]}:{s[1]} ({s[2]})" for s in sites if s[0] == mod)
            problems.append(f"{what}: {mod} has {n}, allowlist says {allowed.get(mod, 0)} -> {lines}. {fix}")
    for mod, n in sorted(allowed.items()):
        if found.get(mod, 0) < n:
            problems.append(
                f"{what}: allowlist says {mod}={n} but only {found.get(mod, 0)} found. "
                "Good news: lower or delete that allowlist entry so the exception cannot grow back."
            )
    return problems


_FIX_UP = ("A service module must not import routers/ or main: move the shared helper into a service module "
           "(routers import services, never the reverse).")
_FIX_FW = ("Keep fastapi/starlette in routers/, middleware/ or main.py; raise a plain exception in the service and "
           "let the router map it to HTTP.")
_FIX_HTTP = ("Reuse an existing adapter module that already owns the outbound call (or add one deliberately and "
             "add it to HTTP_IMPORTS in this test).")
_FIX_ENV = ("Read settings through services/zoe-data/typed_env.py (env_str/env_int/env_bool); "
            "if a direct read is truly needed, add it to ENV_READS here.")


# --- the real tree ------------------------------------------------------------------------------------------------

def test_services_do_not_import_upward_from_routers_or_main():
    assert not judge(find_upward_imports(_ROOT), UPWARD_IMPORTS, "upward import", _FIX_UP)


def test_web_framework_is_imported_only_at_the_edge():
    assert not judge(find_framework_imports(_ROOT), FRAMEWORK_IMPORTS, "fastapi/starlette import", _FIX_FW)


def test_outbound_http_clients_are_imported_only_in_named_modules():
    assert not judge(find_http_imports(_ROOT), HTTP_IMPORTS, "http client import", _FIX_HTTP)


def test_settings_are_read_through_typed_env_not_directly():
    assert not judge(find_env_reads(_ROOT), ENV_READS, "direct env read", _FIX_ENV)


# --- negative controls: the same checkers on a synthetic tree ------------------------------------------------------

def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, src in files.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(src, encoding="utf-8")
    return tmp_path


def test_control_clean_tree_passes(tmp_path):
    root = _tree(tmp_path, {
        "svc.py": "import os\nfrom typed_env import env_str\nx = os.environ.get('ZOE_FLAG')\n",
        "routers/a.py": "from fastapi import APIRouter\nimport httpx\nfrom routers import b\n",
        "main.py": "from fastapi import FastAPI\nfrom routers import a\n",
    })
    assert not find_upward_imports(root) and not find_framework_imports(root) and not find_env_reads(root)
    assert [s[0] for s in find_http_imports(root)] == ["routers/a.py"]


def test_control_upward_import_is_detected(tmp_path):
    root = _tree(tmp_path, {
        "svc.py": "from routers.people import x\n",
        "svc2.py": "from routers import people\n",
        "svc3.py": "import main\n",
    })
    sites = find_upward_imports(root)
    assert {s[0] for s in sites} == {"svc.py", "svc2.py", "svc3.py"}
    problems = judge(sites, {}, "upward import", _FIX_UP)
    assert len(problems) == 3 and "svc.py:1" in problems[0] and "move the shared helper" in problems[0]


def test_control_framework_import_is_detected(tmp_path):
    root = _tree(tmp_path, {"svc.py": "from starlette.responses import JSONResponse\n", "middleware/m.py": "import fastapi\n"})
    assert [s[:2] for s in find_framework_imports(root)] == [("svc.py", 1)]


def test_control_http_client_import_is_detected(tmp_path):
    root = _tree(tmp_path, {"svc.py": "import httpx\nimport urllib.request\n", "ok.py": "import urllib.parse\n"})
    assert [(s[0], s[1]) for s in find_http_imports(root)] == [("svc.py", 1), ("svc.py", 2)]


def test_control_direct_env_read_is_detected_but_zoe_literal_is_not(tmp_path):
    root = _tree(tmp_path, {"svc.py": "import os\na = os.environ['DB_URL']\nb = os.getenv('X')\nc = os.environ.get(k)\n"
                                       "d = os.getenv('ZOE_OK')\n"})
    assert [s[1] for s in find_env_reads(root)] == [2, 3, 4]


def test_control_allowlist_is_exact_both_ways(tmp_path):
    root = _tree(tmp_path, {"svc.py": "import httpx\n"})
    sites = find_http_imports(root)
    assert judge(sites, {"svc.py": 1}, "w", "f") == []
    assert judge(sites, {}, "w", "f")  # a new exception fails
    assert judge(sites, {"svc.py": 2}, "w", "f")  # a stale/slack entry fails too
