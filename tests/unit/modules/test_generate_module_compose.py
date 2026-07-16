"""
Regression tests for tools/generate_module_compose.py path-traversal hardening.

Covers fix (d): a bad module name is rejected by the compose generator and can
never be used to resolve a compose path outside modules/.
"""
import pytest

from conftest import load_compose_generator

pytestmark = pytest.mark.ci_safe


@pytest.fixture()
def gen(tmp_path):
    mod = load_compose_generator()
    g = mod.ComposeGenerator(project_root=tmp_path)
    # Create a legitimate module so the happy path has something to load.
    mdir = tmp_path / "modules" / "zoe-music"
    mdir.mkdir(parents=True)
    (mdir / "docker-compose.module.yml").write_text(
        "services:\n  zoe-music:\n    image: x\n"
    )
    return g


def test_valid_module_loads(gen):
    result = gen.load_module_compose("zoe-music")
    assert result is not None
    assert "services" in result


@pytest.mark.parametrize("bad", [
    "../../etc",
    "..",
    "../secrets",
    "foo/bar",
    "/etc/passwd",
    "zoe-music/../../../etc",
    "a b",            # space
    "UPPER",          # uppercase not allowed by slug
    "with.dot",       # dot not allowed
    "",               # empty
    "x" * 65,         # too long
])
def test_bad_module_name_rejected(gen, bad):
    assert gen.load_module_compose(bad) is None


def test_traversal_cannot_read_outside_modules(gen, tmp_path):
    # Plant a compose file OUTSIDE modules/ and confirm a traversal name can't
    # reach it.
    outside = tmp_path / "docker-compose.module.yml"
    outside.write_text("services:\n  evil:\n    image: pwn\n")
    assert gen.load_module_compose("..") is None
    assert gen.load_module_compose("../") is None


def test_generate_skips_bad_names(gen, monkeypatch):
    # An attacker-controlled config can't crash generation or escape modules/.
    monkeypatch.setattr(gen, "get_enabled_modules", lambda: ["../../etc", "zoe-music"])
    combined = gen.generate()
    assert "zoe-music" in combined["services"]
    # The malicious entry contributed nothing.
    assert len(combined["services"]) == 1
