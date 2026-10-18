"""The VAPID private key lives outside the checkout (architecture deep dive B9), and the move loses no key.

``routers/push.py`` reads ``ZOE_VAPID_KEY_PATH`` (default ``~/.zoe/secrets/vapid_keys.json``) first, then the
legacy in-tree ``data/vapid_keys.json``; it only GENERATES when neither exists, and then writes 0600 to the new
path. Regenerating while the legacy key exists would orphan every browser's push subscription - the negative
control below proves the legacy fallback is what prevents that.
"""
from __future__ import annotations

import json
import stat

import pytest

pytestmark = pytest.mark.ci_safe

import routers.push as push

KEYS = {"public_key": "PUB", "private_key": "PRIV"}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_VAPID_KEY_PATH", str(tmp_path / "secrets" / "vapid_keys.json"))
    monkeypatch.setattr(push, "LEGACY_VAPID_KEY_PATH", tmp_path / "checkout" / "data" / "vapid_keys.json")
    monkeypatch.setattr(push, "_vapid_keys", None)
    yield


def test_default_path_is_outside_the_checkout(monkeypatch):
    monkeypatch.delenv("ZOE_VAPID_KEY_PATH")
    p = push.vapid_key_path()
    assert p.parts[-3:] == (".zoe", "secrets", "vapid_keys.json")
    assert "services" not in p.parts


def test_new_path_wins_over_legacy(tmp_path):
    push.LEGACY_VAPID_KEY_PATH.parent.mkdir(parents=True)
    push.LEGACY_VAPID_KEY_PATH.write_text(json.dumps({"public_key": "OLD", "private_key": "OLD"}))
    push._write_private_json(push.vapid_key_path(), KEYS)
    assert push._get_vapid_keys() == KEYS


def test_legacy_key_is_still_read_and_never_regenerated(monkeypatch):
    push.LEGACY_VAPID_KEY_PATH.parent.mkdir(parents=True)
    push.LEGACY_VAPID_KEY_PATH.write_text(json.dumps(KEYS))
    assert push._get_vapid_keys() == KEYS
    assert not push.vapid_key_path().exists(), "reading the legacy key must not write a second copy"


def test_negative_control_without_the_legacy_fallback_a_new_key_would_be_minted(monkeypatch):
    push.LEGACY_VAPID_KEY_PATH.parent.mkdir(parents=True)
    push.LEGACY_VAPID_KEY_PATH.write_text(json.dumps(KEYS))
    monkeypatch.setattr(push, "_vapid_read_path", lambda: None)   # break the fallback
    pytest.importorskip("py_vapid")
    got = push._get_vapid_keys()
    assert got and got != KEYS, "with the fallback removed the legacy key is ignored - the bug this guards"


def test_an_existing_loose_parent_dir_is_tightened():
    d = push.vapid_key_path().parent
    d.mkdir(parents=True)
    d.chmod(0o775)
    push._write_private_json(push.vapid_key_path(), KEYS)
    assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_generated_key_is_written_private_at_the_new_path():
    pytest.importorskip("py_vapid")
    keys = push._get_vapid_keys()
    p = push.vapid_key_path()
    assert keys and json.loads(p.read_text()) == keys
    assert stat.S_IMODE(p.stat().st_mode) == 0o600 and stat.S_IMODE(p.parent.stat().st_mode) == 0o700
    assert not push.LEGACY_VAPID_KEY_PATH.exists()
