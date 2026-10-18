"""typed_env — the one canonical env parse (Wave-4 packet, PR W4-T1).

Pure stdlib, no service imports beyond the module under test. Locks in:
truthy/falsy table, unrecognized-value → default (+ one warning, not spam),
invalid int/float → default, and call-time re-read after monkeypatch.setenv
(the anti-import-snapshot guarantee that keeps migrations mechanical).
"""
import logging

import pytest

pytestmark = pytest.mark.ci_safe  # pure stdlib parsers

import typed_env
from typed_env import env_bool, env_float, env_int, env_list, env_str

K = "TYPED_ENV_TEST_KEY"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(K, raising=False)
    # warn-once registry is process-global — isolate per test
    monkeypatch.setattr(typed_env, "_warned", set())


# ── env_bool: the canonical truth table ──────────────────────────────────────

@pytest.mark.parametrize("raw", ["1", "true", "yes", "on", "TRUE", " Yes ", "ON"])
def test_bool_truthy(monkeypatch, raw):
    monkeypatch.setenv(K, raw)
    assert env_bool(K, default=False) is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "FALSE", " No ", "OFF"])
def test_bool_falsy(monkeypatch, raw):
    monkeypatch.setenv(K, raw)
    assert env_bool(K, default=True) is False


@pytest.mark.parametrize("default", [True, False])
def test_bool_absent_uses_default(monkeypatch, default):
    assert env_bool(K, default=default) is default


@pytest.mark.parametrize("default", [True, False])
def test_bool_present_but_empty_is_false(monkeypatch, default):
    """KEY= (present but empty) means explicitly CLEARED — False regardless of
    the default. The live .env uses this state, and every legacy bool parse
    (truthy-set membership) yields False for "" — if empty returned the
    default, migrating a default-true flag would silently flip it on."""
    monkeypatch.setenv(K, "")
    assert env_bool(K, default=default) is False
    monkeypatch.setenv(K, "   ")
    assert env_bool(K, default=default) is False


def test_bool_unrecognized_falls_back_with_one_warning(monkeypatch, caplog):
    monkeypatch.setenv(K, "banana")
    caplog.set_level(logging.WARNING, logger=typed_env.__name__)
    assert env_bool(K, default=True) is True
    assert env_bool(K, default=True) is True  # second read: no second warning
    warnings = [r for r in caplog.records if "not a valid bool" in r.message]
    assert len(warnings) == 1


# ── env_int / env_float ──────────────────────────────────────────────────────

def test_int_parses_and_defaults(monkeypatch, caplog):
    monkeypatch.setenv(K, " 42 ")
    assert env_int(K, default=7) == 42
    monkeypatch.setenv(K, "not-a-number")
    caplog.set_level(logging.WARNING, logger=typed_env.__name__)
    assert env_int(K, default=7) == 7
    assert any("not a valid int" in r.message for r in caplog.records)
    monkeypatch.delenv(K)
    assert env_int(K, default=7) == 7


def test_float_parses_and_defaults(monkeypatch, caplog):
    monkeypatch.setenv(K, "2.5")
    assert env_float(K, default=1.0) == 2.5
    monkeypatch.setenv(K, "nope")
    caplog.set_level(logging.WARNING, logger=typed_env.__name__)
    assert env_float(K, default=1.5) == 1.5
    assert any("not a valid float" in r.message for r in caplog.records)
    monkeypatch.delenv(K)
    assert env_float(K, default=0.25) == 0.25


# ── env_str / env_list ───────────────────────────────────────────────────────

def test_str_strips_and_defaults(monkeypatch):
    monkeypatch.setenv(K, "  hello  ")
    assert env_str(K) == "hello"
    monkeypatch.delenv(K)
    assert env_str(K, default="fallback") == "fallback"
    # A `.env` line like `KEY=` (present but empty/whitespace) means "not set" —
    # consistent with every other accessor; never a surprise empty string.
    monkeypatch.setenv(K, "")
    assert env_str(K, default="fallback") == "fallback"
    monkeypatch.setenv(K, "   ")
    assert env_str(K, default="fallback") == "fallback"


def test_list_splits_strips_drops_empties(monkeypatch):
    monkeypatch.setenv(K, " a, b ,,c , ")
    assert env_list(K) == ("a", "b", "c")
    monkeypatch.setenv(K, "x|y", )
    assert env_list(K, sep="|") == ("x", "y")
    monkeypatch.delenv(K)
    assert env_list(K, default=("d",)) == ("d",)


# ── the load-bearing guarantee: CALL-time reads ──────────────────────────────

def test_call_time_reread_after_setenv(monkeypatch):
    """Accessors must see env changes made AFTER import — tests monkeypatch
    env, and runtime_env bootstraps after module import. An import-time
    snapshot would silently break both."""
    assert env_bool(K, default=False) is False
    monkeypatch.setenv(K, "1")
    assert env_bool(K, default=False) is True
    monkeypatch.setenv(K, "0")
    assert env_bool(K, default=True) is False
    monkeypatch.setenv(K, "9")
    assert env_int(K, default=0) == 9
    monkeypatch.setenv(K, "10")
    assert env_int(K, default=0) == 10


# ── the documented divergence this module exists to end ─────────────────────

def test_one_truth_table_ends_the_use_core_brain_divergence(monkeypatch):
    """chat.py parsed "1"/"yes" as False (== "true") while brain_dispatch parsed
    them as True (not-in-falsy). Under env_bool there is exactly one answer."""
    for raw in ("1", "yes", "true", "on"):
        monkeypatch.setenv(K, raw)
        assert env_bool(K, default=False) is True, raw


# ── flag(): the registry-driven read ─────────────────────────────────────────

import flag_registry  # noqa: E402  (pure data module, stdlib only)


@pytest.fixture
def _fake_flags(monkeypatch):
    """Register throwaway flags so the tests never depend on a real flag's live default."""
    monkeypatch.setattr(flag_registry, "FLAGS", dict(flag_registry.FLAGS))
    flag_registry._f("TEST_FLAG_BOOL", "bool", True)
    flag_registry._f("TEST_FLAG_INT", "int", 7)
    flag_registry._f("TEST_FLAG_FLOAT", "float", 1.5)
    flag_registry._f("TEST_FLAG_LIST", "list", ("a", "b"))
    flag_registry._f("TEST_FLAG_STR", "str", "dflt")
    flag_registry._f("TEST_FLAG_MODE", "str", "shadow", allowed=("off", "shadow", "enforce"))
    for n in ("BOOL", "INT", "FLOAT", "LIST", "STR", "MODE"):
        monkeypatch.delenv(f"TEST_FLAG_{n}", raising=False)


def test_flag_returns_the_declared_default_when_unset(_fake_flags):
    assert typed_env.flag("TEST_FLAG_BOOL") is True
    assert typed_env.flag("TEST_FLAG_INT") == 7
    assert typed_env.flag("TEST_FLAG_FLOAT") == 1.5
    assert typed_env.flag("TEST_FLAG_LIST") == ("a", "b")
    assert typed_env.flag("TEST_FLAG_STR") == "dflt"
    assert typed_env.flag("TEST_FLAG_MODE") == "shadow"


def test_flag_reads_at_call_time_with_the_canonical_bool_rule(_fake_flags, monkeypatch):
    monkeypatch.setenv("TEST_FLAG_BOOL", "off")
    assert typed_env.flag("TEST_FLAG_BOOL") is False
    monkeypatch.setenv("TEST_FLAG_BOOL", "YES")
    assert typed_env.flag("TEST_FLAG_BOOL") is True
    monkeypatch.setenv("TEST_FLAG_BOOL", "")  # present-but-empty is off, as env_bool
    assert typed_env.flag("TEST_FLAG_BOOL") is False
    monkeypatch.setenv("TEST_FLAG_INT", "12")
    assert typed_env.flag("TEST_FLAG_INT") == 12
    monkeypatch.setenv("TEST_FLAG_INT", "nope")  # unparseable -> the declared default
    assert typed_env.flag("TEST_FLAG_INT") == 7


def test_flag_mode_values_are_validated_against_allowed(_fake_flags, monkeypatch, caplog):
    monkeypatch.setenv("TEST_FLAG_MODE", " ENFORCE ")
    assert typed_env.flag("TEST_FLAG_MODE") == "enforce"
    monkeypatch.setenv("TEST_FLAG_MODE", "enfroce")  # typo: default + a warning, never a silent flip
    with caplog.at_level(logging.WARNING, logger="typed_env"):
        assert typed_env.flag("TEST_FLAG_MODE") == "shadow"
    assert any("enfroce" in r.getMessage() for r in caplog.records)


def test_flag_refuses_an_unregistered_name(_fake_flags):
    with pytest.raises(KeyError, match="not in the flag registry"):
        typed_env.flag("TEST_FLAG_NOT_DECLARED")
