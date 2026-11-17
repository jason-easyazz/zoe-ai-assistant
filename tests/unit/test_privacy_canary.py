"""The household-data canary for this PUBLIC repository (``tools/audit/privacy_canary.py``, 2026-10-19).

Two layers:
  1. THE MECHANISM, always (CI included): a throwaway key and invented canaries over a synthetic tree - a planted name,
     email, coordinate and phone number are each caught; a clean tree and look-alikes are not; the committed digest file
     carries only opaque hex digests (no value it guards, in any form).
  2. THE REAL SCAN, wherever the key is available (``ZOE_PRIVACY_CANARY_KEY`` - the GitHub Actions secret in CI - or
     ``~/.zoe/privacy-canary/key`` on the box): no tracked file may contain a household canary. Without the key,
     ``privacy_canary.missing_key_policy`` decides (audit row G11): a CI job of this repository that declares the variable
     but got it empty FAILS (the secret is not provisioned), as does any run with ``ZOE_PRIVACY_CANARY_REQUIRED=1``; a fork PR,
     Dependabot, a local run, or a job that does not declare it skips with a warning naming why.

Negative control (run by hand when written): the pre-scrub copies of ``scripts/setup/create_users.sql``,
``homeassistant/configuration.yaml`` and ``services/zoe-data/tests/test_ws_roles.py`` from origin/main hit 22 times
(emails, names, both coordinates) under the real key.
"""
from __future__ import annotations

import importlib.util
import json
import re
import warnings
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
        verdict, why = pc.missing_key_policy()
        if verdict == "fail":
            pytest.fail(why)
        warnings.warn(f"PRIVACY CANARY NOT RUN: {why}")      # loud in the summary, not only in -rs output
        pytest.skip(why)
    hits = pc.scan(pc.tracked_files(ROOT), key, pc.load_digests())
    assert not hits, "household canary in tracked file(s) - replace with obvious placeholders:\n  " + "\n  ".join(
        f"{p}:{n} ({k})" for p, n, k in hits)


# -- the missing-key policy (audit row G11): loud, and a FAIL where the scan must run --------------------------------------
def _event(tmp_path, *, fork: bool, full_name: str = "owner/repo"):
    p = tmp_path / "event.json"
    p.write_text(json.dumps({"pull_request": {"head": {"repo": {"fork": fork, "full_name": full_name}}}}))
    return str(p)


def _actions(**extra):
    return {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "owner/repo", "GITHUB_ACTOR": "someone", **extra}


def test_missing_key_fails_on_this_repos_own_ci_runs(tmp_path):
    """Negative control for the old behaviour (an unconditional skip): a declared-but-empty secret on a push or an
    own-branch PR is a FAIL, and so is an event payload that cannot be read."""
    assert pc.missing_key_policy(_actions(ZOE_PRIVACY_CANARY_KEY="", GITHUB_EVENT_NAME="push"))[0] == "fail"
    own_pr = _actions(ZOE_PRIVACY_CANARY_KEY="", GITHUB_EVENT_NAME="pull_request", GITHUB_EVENT_PATH=_event(tmp_path, fork=False))
    assert pc.missing_key_policy(own_pr)[0] == "fail"
    broken = _actions(ZOE_PRIVACY_CANARY_KEY="", GITHUB_EVENT_NAME="pull_request", GITHUB_EVENT_PATH=str(tmp_path / "none"))
    assert pc.missing_key_policy(broken)[0] == "fail"
    assert pc.missing_key_policy({"ZOE_PRIVACY_CANARY_REQUIRED": "1"})[0] == "fail"


def test_missing_key_skips_where_no_secret_can_exist(tmp_path):
    fork = _actions(ZOE_PRIVACY_CANARY_KEY="", GITHUB_EVENT_NAME="pull_request", GITHUB_EVENT_PATH=_event(tmp_path, fork=True))
    other = _actions(ZOE_PRIVACY_CANARY_KEY="", GITHUB_EVENT_NAME="pull_request",
                     GITHUB_EVENT_PATH=_event(tmp_path, fork=False, full_name="someone-else/repo"))
    bot = _actions(ZOE_PRIVACY_CANARY_KEY="", GITHUB_EVENT_NAME="push", GITHUB_ACTOR="dependabot[bot]")
    for env in (fork, other, bot, {}, _actions(GITHUB_EVENT_NAME="push")):
        verdict, why = pc.missing_key_policy(env)
        assert verdict == "skip" and "did NOT run" in why, env
