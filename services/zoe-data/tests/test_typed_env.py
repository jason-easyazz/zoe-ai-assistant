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
