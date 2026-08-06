"""
Regression tests for tools/generate_module_compose.py.

Covers fix (d): a bad module name is rejected by the compose generator and can
never be used to resolve a compose path outside modules/.

Also pins the generator's known representational GAP against the real
modules/omnigent compose — see test_generator_cannot_represent_omnigent.
"""
import pytest
import yaml

from conftest import REPO_ROOT, load_compose_generator

pytestmark = pytest.mark.ci_safe


@pytest.fixture()
def gen(tmp_path):
    mod = load_compose_generator()
    g = mod.ComposeGenerator(project_root=tmp_path)
    # Create a legitimate module so the happy path has something to load.
    # Synthetic fixture name on purpose: this test must not depend on which
    # modules actually exist in the repo.
    mdir = tmp_path / "modules" / "zoe-example"
    mdir.mkdir(parents=True)
    (mdir / "docker-compose.module.yml").write_text(
        "services:\n  zoe-example:\n    image: x\n"
    )
    return g


def test_valid_module_loads(gen):
    result = gen.load_module_compose("zoe-example")
    assert result is not None
    assert "services" in result


@pytest.mark.parametrize("bad", [
    "../../etc",
    "..",
    "../secrets",
    "foo/bar",
    "/etc/passwd",
    "zoe-example/../../../etc",
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
    monkeypatch.setattr(gen, "get_enabled_modules", lambda: ["../../etc", "zoe-example"])
    combined = gen.generate()
    assert "zoe-example" in combined["services"]
    # The malicious entry contributed nothing.
    assert len(combined["services"]) == 1


# ---------------------------------------------------------------------------
# The generator-vs-omnigent gap.
#
# modules/omnigent is the only real module, and it is deliberately NOT in
# config/modules.yaml enabled_modules: it is deployed from its own compose file
# (docker compose -p omnigent -f modules/omnigent/docker-compose.module.yml).
#
# The reason is structural: generate() hardcodes zoe-network as the ONLY
# top-level network and merely warns about a module's others, so it cannot
# emit omnigent's zoe-codeintel network. Feeding omnigent through the
# generator produces an INVALID project — measured 2026-08-06:
#   service "omnigent" refers to undefined network zoe-codeintel:
#   invalid compose project
# (`docker compose config` rc=15; adding the network definition makes the
# identical file validate, confirming that is the only gap).
#
# #1659 documented this gap in prose (docs/guides/MODULE_SYSTEM.md → "Existing
# tooling — what it is, and what it is not"; modules/AGENTS.md → Local
# Contracts). These tests are the ENFORCEMENT of that prose: a doc sentence
# drifts silently, an assertion does not.
#
# Intentionally docker-free so it stays ci_safe. If someone teaches the
# generator to emit additional top-level networks, this test goes RED — that is
# the signal to update both docs and then delete or invert this test.
# ---------------------------------------------------------------------------

OMNIGENT_EXTRA_NETWORK = "zoe-codeintel"


def _real_generator_for_omnigent(monkeypatch):
    mod = load_compose_generator()
    g = mod.ComposeGenerator(project_root=REPO_ROOT)
    monkeypatch.setattr(g, "get_enabled_modules", lambda: ["omnigent"])
    return g


def test_omnigent_is_not_enabled_in_modules_yaml():
    """omnigent must stay out of the generator's input (it can't represent it)."""
    config = yaml.safe_load((REPO_ROOT / "config" / "modules.yaml").read_text())
    assert "omnigent" not in (config.get("enabled_modules") or [])


def test_generator_cannot_represent_omnigent(monkeypatch):
    """The generated project references a network it never defines."""
    module_compose = yaml.safe_load(
        (REPO_ROOT / "modules" / "omnigent" / "docker-compose.module.yml").read_text()
    )
    # Precondition: omnigent really does need the second network.
    assert OMNIGENT_EXTRA_NETWORK in module_compose["networks"]
    assert OMNIGENT_EXTRA_NETWORK in module_compose["services"]["omnigent"]["networks"]

    combined = _real_generator_for_omnigent(monkeypatch).generate()

    # The service still demands the network...
    assert OMNIGENT_EXTRA_NETWORK in combined["services"]["omnigent"]["networks"]
    # ...but the generator never defines it at top level. That mismatch is what
    # makes the project invalid, and why omnigent uses its own compose file.
    assert OMNIGENT_EXTRA_NETWORK not in combined["networks"], (
        "generate_module_compose.py now emits extra top-level networks. The "
        "omnigent exception documented in docs/guides/MODULE_SYSTEM.md and "
        "modules/AGENTS.md may no longer apply — update both, then retire this test."
    )
