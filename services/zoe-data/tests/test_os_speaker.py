"""zoe-data mints the Zoe OS kernel's speaker assertions itself (os_speaker.py), and every person-bound request it sends carries one:
the token format from the spec, one per request, the secret read again after a restart, an owner-only file, and a forget that is
never widened when the kernel says the speaker is unproven. The real lab verifier's verdict is ``test_os_speaker_contract.py``.
Names and items are invented.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

import pytest

import os_shadow
import os_speaker
from test_os_authority import ak, provision, _secret_file  # noqa: F401 - the fixture
from test_os_shadow import MEMBER, SVC

pytestmark = pytest.mark.ci_safe

KEY = bytes(range(32))


def claims(tok: str) -> dict:
    body = tok.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def test_the_token_is_the_spec_format_with_rounded_milliseconds_and_a_fresh_nonce():
    tok = os_speaker.mint(MEMBER, "forget.scoped", key=KEY, now=1000.0006)
    body, mac = tok.split(".")
    assert "=" not in body and mac == hmac.new(KEY, b"zoe-os speaker assertion v1\0" + body.encode(), hashlib.sha256).hexdigest()
    c = claims(tok)
    assert set(c) == {"p", "t", "n", "a"} and c["p"] == MEMBER and c["a"] == "forget.scoped"
    assert c["t"] == 1000001                                   # ROUNDED (1000000.6 ms), as the kernel rounds on verify
    assert len(c["n"]) == 32 and int(c["n"], 16) >= 0
    assert claims(os_speaker.mint(MEMBER, "forget.scoped", key=KEY))["n"] != c["n"]
    with pytest.raises(ValueError):
        os_speaker.mint(MEMBER, "everything", key=KEY)
    with pytest.raises(ValueError):
        os_speaker.mint("", "undo", key=KEY)


def test_the_keyed_digest_is_the_spec_function():
    msg = json.dumps([MEMBER, "Shopping", "oat milk"], separators=(",", ":"), ensure_ascii=False).encode()
    want = hmac.new(KEY, b"zoe-os list digest v1\0" + msg, hashlib.sha256).hexdigest()
    assert os_speaker.keyed_digest(KEY, MEMBER, "Shopping", " Oat Milk ") == want and len(want) == 64


def test_a_secret_file_that_is_not_owner_only_is_treated_as_absent(ak):
    path = ak.state / os_speaker.IDENTITY_CREDS
    assert os_speaker.mint(MEMBER, "undo")
    os.chmod(path, 0o644)
    with pytest.raises(os_shadow.KernelUnavailable):
        os_speaker.mint(MEMBER, "undo")
    os.chmod(path, 0o600)
    path.write_text("not json")
    with pytest.raises(os_shadow.KernelUnavailable):
        os_speaker.mint(MEMBER, "undo")
    path.unlink()
    assert os_speaker.speaker_arg(MEMBER, "undo") == {}           # an older kernel: no identity secret, no speaker argument


def test_the_secret_is_read_again_for_every_mint(ak):
    old = os_speaker.mint(MEMBER, "undo")
    ak.identity = secrets.token_bytes(32)
    provision(ak.state, ak)                                     # a kernel restart writes a fresh secret
    new = os_speaker.mint(MEMBER, "undo")
    assert not ak.unproven({"speaker": new}, MEMBER, "undo") and ak.unproven({"speaker": old}, MEMBER, "undo")


# ---- the shadow forget carries the proof ------------------------------------------------------------------------------------------
class _Recorder:
    """Stand-in for ``os_shadow._Kernel``: records (skill, op, args) and answers through a rule."""

    def __init__(self, rule):
        self.calls, self.rule = [], rule

    def call(self, skill, op, until=None, **args):
        self.calls.append((skill, op, args))
        return self.rule(skill, op, args)


def test_a_whole_member_forget_goes_as_the_eraser_when_the_kernel_provisioned_one(ak):
    k = _Recorder(lambda s, o, a: {"count": 3})
    assert os_shadow._kernel_forget(k, MEMBER, None, None) == 3
    assert k.calls == [("eraser", "forget", {"subject": MEMBER, "actor": MEMBER})]
    (ak.state / os_speaker.ERASE_CREDS).unlink()                # the shadow daemon writes no eraser: the member's own proof
    k.calls.clear()
    os_shadow._kernel_forget(k, MEMBER, None, None)
    ((skill, _op, args),) = k.calls
    assert skill == "panel" and claims(args["speaker"])["a"] == "forget.person" and claims(args["speaker"])["p"] == MEMBER


def test_a_scoped_forget_carries_a_scoped_assertion_and_an_unproven_one_is_never_widened(ak):
    k = _Recorder(lambda s, o, a: {"count": 1})
    os_shadow._kernel_forget(k, MEMBER, "Dana", None)
    ((skill, _op, args),) = k.calls
    assert skill == "panel" and args["entity"] == "Dana" and claims(args["speaker"])["a"] == "forget.scoped"

    def unproven(s, o, a):
        raise os_shadow.KernelRefused("speaker unproven")
    k = _Recorder(unproven)
    with pytest.raises(os_shadow.KernelRefused):
        os_shadow._kernel_forget(k, MEMBER, "Dana", None)
    assert [a.get("entity") for _s, _o, a in k.calls] == ["Dana", "Dana"]     # retried once, fresh; never "forget me" instead
    assert len({a["speaker"] for _s, _o, a in k.calls}) == 2


def test_an_unmatchable_scope_still_over_forgets_as_before(ak):
    def rule(s, o, a):
        if "entity" in a:
            raise os_shadow.KernelRefused("nothing to match")
        return {"count": 2}
    k = _Recorder(rule)
    assert os_shadow._kernel_forget(k, MEMBER, "Dana", None) == 2
    assert [s for s, _o, _a in k.calls] == ["panel", "eraser"]


def test_no_service_module_imports_the_lab_and_nothing_logs_a_token():
    src = (SVC / "os_speaker.py").read_text("utf-8")
    assert "zoe_os" not in src.replace("zoe-os", "") and "sys.path" not in src and "logger" not in src and "print(" not in src
