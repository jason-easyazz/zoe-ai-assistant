"""The household-data canary for this PUBLIC repository (``tools/audit/privacy_canary.py``, 2026-10-19).

Two layers:
  1. THE MECHANISM, always (CI included): a throwaway key and invented canaries over a synthetic tree - a planted name,
     email, coordinate and phone number are each caught; a clean tree and look-alikes are not; the committed digest file
     carries only opaque hex digests (no value it guards, in any form).
  2. THE REAL SCAN, wherever the key is available (``ZOE_PRIVACY_CANARY_KEY`` - the GitHub Actions secret in CI - or
     ``~/.zoe/privacy-canary/key`` on the box): no tracked file may contain a household canary. Without the key it skips
     with the reason; the key is an owner-provisioned secret, and a CI run without it is reported as a skip.

Negative control (run by hand when written): the pre-scrub copies of ``scripts/setup/create_users.sql``,
``homeassistant/configuration.yaml`` and ``services/zoe-data/tests/test_ws_roles.py`` from origin/main hit 22 times
(emails, names, both coordinates) under the real key.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools/audit/privacy_canary.py"


def _tool():
    spec = importlib.util.spec_from_file_location("privacy_canary_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pc = _tool()
KEY = b"throwaway-test-key-not-a-secret"
INVENTED = ["word:zebulonia", "email:quill.person@example.net", "coord:-12.3456", "tel:0499 000 111"]


def _digests():
    return {pc.digest(KEY, c) for c in pc.parse_canaries("\n".join(INVENTED))}


def _scan(tmp_path, files: dict[str, str]):
    for name, body in files.items():
        (tmp_path / name).write_text(body)
    return pc.scan(sorted(tmp_path.iterdir()), KEY, _digests(), root=tmp_path)


@pytest.mark.parametrize("body, kind", [
    ("INSERT INTO users VALUES ('Zebulonia', 'admin');", "word"),
    ("contact: Quill.Person@Example.NET", "email"),
    ("latitude: -12.34567", "coord"),            # truncated to 3 decimals before digesting
    ("call +61 499 000 111 after 5", "tel"),     # any formatting, same last 9 digits
])
def test_a_planted_canary_is_caught_and_reported_without_its_value(tmp_path, body, kind):
    hits = _scan(tmp_path, {"leak.txt": f"line one\n{body}\n"})
    assert hits == [("leak.txt", 2, kind)]
    assert all("zebulonia" not in str(h).lower() and "quill" not in str(h).lower() for h in hits)


def test_a_clean_tree_and_look_alikes_pass(tmp_path):
    hits = _scan(tmp_path, {
        "ok.md": "zebulon and zebulonian are different words; -12.35 has too few decimals; 0499 000 112 differs",
        "bin.dat": "\0zebulonia",            # binary files are skipped
    })
    assert hits == []


def test_parse_canaries_rejects_garbage_and_ignores_comments():
    assert pc.parse_canaries("# note\n\nword:Zebulonia  # trailing\n") == ["word:zebulonia"]
    with pytest.raises(ValueError):
        pc.parse_canaries("zebulonia")          # no kind
    with pytest.raises(ValueError):
        pc.parse_canaries("coord:north")


def test_the_committed_digest_file_is_opaque():
    data = json.loads((ROOT / "tools/audit/privacy_canary_digests.json").read_text())
    digests = data["digests"]
    assert len(digests) >= 10
    assert all(re.fullmatch(r"[0-9a-f]{64}", d) for d in digests)
    raw = (ROOT / "tools/audit/privacy_canary_digests.json").read_text()
    assert set(data) == {"version", "scheme", "digests"}, "no labels, kinds or hints next to the digests"
    assert "@" not in raw.replace("HMAC-SHA256", "")


def test_no_tracked_file_carries_a_household_canary():
    key = pc.load_key()
    if key is None:
        pytest.skip("privacy canary key not available (owner-provisioned: ZOE_PRIVACY_CANARY_KEY secret or "
                    "~/.zoe/privacy-canary/key) - the real scan did not run")
    hits = pc.scan(pc.tracked_files(ROOT), key, pc.load_digests())
    assert not hits, "household canary in tracked file(s) - replace with obvious placeholders:\n  " + "\n  ".join(
        f"{p}:{n} ({k})" for p, n, k in hits)
