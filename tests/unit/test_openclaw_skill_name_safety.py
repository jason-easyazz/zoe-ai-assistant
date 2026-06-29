"""
test_openclaw_skill_name_safety.py — path-traversal hardening for the OpenClaw
skill-management API.

The skill endpoints accept a caller-supplied skill ``{name}`` and join it into a
filesystem path. Before the fix a crafted name (``..``, a path separator, an
absolute path, a leading dot, or a URL-encoded ``%2e%2e`` that route
normalisation decodes to ``..``) could resolve OUTSIDE the workspace skills
directory — turning ``remove_skill`` into a destructive ``rmtree`` on an
arbitrary directory, and the install/preview paths into out-of-base writes/reads.

These tests prove every name-derived filesystem op is sandboxed and that
legitimate skill names still operate exactly as before.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import openclaw_manager as om


# A representative sample of real allowlisted / workspace skill names that MUST
# keep working unchanged.
VALID_NAMES = [
    "briefing", "home-assistant", "taskflow-inbox-triage", "video-frames",
    "openai-whisper-api", "dynamic-widgets", "memory-consolidation",
    "a", "a.b", "a_b", "Skill-1.2.3",
]

# Names that must be rejected before any filesystem access. The %2e%2e cases are
# written here already decoded — FastAPI/route normalisation hands the handler
# the decoded value, so "%2e%2e/x" reaches us as "../x".
TRAVERSAL_NAMES = [
    "..",
    "../secret",
    "../../etc/passwd",
    "foo/../../bar",
    "a/b",
    "a\\b",
    "/etc/passwd",
    "/abs/path",
    ".hidden",
    ".",
    "",
    "foo\x00bar",
    "name with space",
    "weird;name",
]


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point the skills base at a temp dir with one real skill and an out-of-base
    'secret' sentinel that traversal must never touch."""
    base = tmp_path / "workspace" / "skills"
    base.mkdir(parents=True)

    good = base / "briefing"
    good.mkdir()
    (good / "SKILL.md").write_text("# Briefing\nDaily briefing skill\n")

    # Sibling of the skills base — a successful traversal would delete/read this.
    secret = tmp_path / "workspace" / "secret"
    secret.mkdir()
    (secret / "important.txt").write_text("do not touch")

    monkeypatch.setattr(om, "_OPENCLAW_DIR", tmp_path)
    monkeypatch.setattr(om, "_SKILLS_BASE", base)
    return {"base": base, "good": good, "secret": secret, "root": tmp_path}


# ── name validator ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", VALID_NAMES)
def test_valid_names_accepted(name):
    assert om._validate_skill_name(name) == name


@pytest.mark.parametrize("name", TRAVERSAL_NAMES)
def test_unsafe_names_rejected(name):
    with pytest.raises(ValueError):
        om._validate_skill_name(name)


def test_safe_skill_dir_stays_in_base(sandbox):
    resolved = om._safe_skill_dir("briefing")
    assert resolved == sandbox["base"].resolve() / "briefing"
    assert sandbox["base"].resolve() in resolved.parents


@pytest.mark.parametrize("name", TRAVERSAL_NAMES)
def test_safe_skill_dir_rejects_traversal(sandbox, name):
    with pytest.raises(ValueError):
        om._safe_skill_dir(name)


# ── remove_skill (the destructive path) ───────────────────────────────────────

async def test_remove_skill_normal_operates(sandbox):
    result = await om.remove_skill("briefing")
    assert result == {"status": "removed", "name": "briefing"}
    assert not sandbox["good"].exists()


@pytest.mark.parametrize("name", ["../secret", "../../etc", "/etc", "..", ".hidden"])
async def test_remove_skill_traversal_rejected(sandbox, name):
    with pytest.raises(ValueError):
        await om.remove_skill(name)
    # The out-of-base sentinel and the legit skill are both untouched.
    assert sandbox["secret"].exists()
    assert (sandbox["secret"] / "important.txt").exists()
    assert sandbox["good"].exists()


async def test_remove_skill_cannot_rmtree_sibling(sandbox):
    """Direct proof: a traversal aimed at the sibling 'secret' dir never deletes it."""
    traversal = f"../{sandbox['secret'].name}"
    with pytest.raises(ValueError):
        await om.remove_skill(traversal)
    assert sandbox["secret"].exists()


# ── install_skill (bundled path: rmtree dest + copytree) ──────────────────────

async def test_install_bundled_traversal_rejected(sandbox, monkeypatch):
    # Even if everything downstream would succeed, a bad name must be rejected
    # before any rmtree/copytree against an out-of-base dest.
    async def _fake_run(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("CLI should not run for an invalid name")

    monkeypatch.setattr(om, "_run_openclaw", _fake_run)
    with pytest.raises(ValueError):
        await om.install_skill("../secret", source="openclaw-bundled")
    assert sandbox["secret"].exists()


# ── preview_skill (read path) ─────────────────────────────────────────────────

async def test_preview_skill_normal_reads(sandbox):
    result = await om.preview_skill("briefing")
    assert result["source"] == "workspace"
    assert "Briefing" in (result["content"] or "")


@pytest.mark.parametrize("name", ["../secret", "../../etc/passwd", ".hidden"])
async def test_preview_skill_traversal_rejected(sandbox, name):
    with pytest.raises(ValueError):
        await om.preview_skill(name)


# ── router maps ValueError → HTTP 400 ─────────────────────────────────────────


def _load_router_module():
    """Load routers/openclaw.py directly from file.

    The ``routers`` package __init__ eagerly imports every router (incl. people),
    which the unit-test sys.path can't satisfy. The openclaw router uses only
    absolute imports, so we load it standalone and skip the package __init__.
    """
    import importlib.util

    here = Path(__file__).resolve().parents[2]
    path = here / "services" / "zoe-data" / "routers" / "openclaw.py"
    spec = importlib.util.spec_from_file_location("openclaw_router_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_router_rejects_traversal_with_400(sandbox):
    r = _load_router_module()
    from fastapi import HTTPException

    for endpoint in (
        r.remove_skill_endpoint,
        r.update_skill_endpoint,
        r.preview_skill_endpoint,
    ):
        with pytest.raises(HTTPException) as ei:
            await endpoint(name="../secret", _user={"is_admin": True})
        assert ei.value.status_code == 400
    assert sandbox["secret"].exists()


async def test_router_install_traversal_with_400(sandbox):
    r = _load_router_module()
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        await r.install_skill_endpoint(
            name="../secret", source="openclaw-bundled", _user={"is_admin": True}
        )
    assert ei.value.status_code == 400
    assert sandbox["secret"].exists()
