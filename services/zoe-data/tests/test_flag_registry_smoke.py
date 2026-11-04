"""Every REGISTERED flag is readable through ``typed_env.flag`` and reads back its declared default.

The repo-level rules (unregistered read, contradicting default, ratchet) live in ``tests/unit/test_flag_registry.py``; this
is the runtime half: the registry's types and defaults are the ones ``typed_env`` can actually parse, so a hand-edited entry
(``'int'`` with a string default, a mode default outside ``allowed``) fails here instead of at the first live read.
"""
import os

import pytest

pytestmark = pytest.mark.ci_safe

import flag_registry
import typed_env


def test_every_registered_flag_reads_back_its_default_with_a_clean_env(monkeypatch):
    for name in flag_registry.FLAGS:
        monkeypatch.delenv(name, raising=False)
    bad = []
    for name, s in flag_registry.FLAGS.items():
        got = typed_env.flag(name)
        want = tuple(s.default) if s.type == "list" else s.default
        if got != want or type(got) is not type(want):
            bad.append((name, s.type, want, got))
    assert not bad, bad[:10]
    assert len(flag_registry.FLAGS) > 600  # the generated registry is loaded, not an empty stub


def test_a_mode_flag_accepts_each_allowed_value_and_rejects_a_typo(monkeypatch):
    modes = [s for s in flag_registry.FLAGS.values() if s.allowed]
    assert len(modes) >= 25
    for s in modes:
        for value in s.allowed:
            monkeypatch.setenv(s.name, value.upper())
            assert typed_env.flag(s.name) == value, (s.name, value)
        monkeypatch.setenv(s.name, "definitely-not-a-mode")
        assert typed_env.flag(s.name) == s.default, s.name
        monkeypatch.delenv(s.name, raising=False)
    assert not any(k.startswith("ZOE_") and os.environ.get(k) == "definitely-not-a-mode" for k in os.environ)
