"""zoe-data's speaker assertions and keyed digest against the REAL lab code (``labs/zoe-os/zoe_os/speaker.py``'s ``Verifier`` and
``labs/zoe-os/authority.py``'s ``keyed_digest``): the token format and the digest cannot drift from the kernel's.

SKIPPED, with a named reason, when the lab kernel is not in this tree (it lands on its own branch). Test code may read ``labs/``
(loaded by file path, stdlib only); zoe-data never imports it. The end-to-end proof through a running kernel is
``test_os_authority_contract.py``. Names are invented.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

import os_speaker

REPO = Path(__file__).resolve().parents[3]
LAB = REPO / "labs" / "zoe-os"
pytestmark = [pytest.mark.ci_safe, pytest.mark.skipif(
    not (LAB / "zoe_os" / "speaker.py").is_file(), reason="the lab kernel (labs/zoe-os) is not in this tree: the contract is unproven here")]

KEY = bytes(range(32))
WHO = "member-a"


@pytest.fixture(scope="module")
def lab():
    spec = importlib.util.spec_from_file_location("lab_speaker_contract", LAB / "zoe_os" / "speaker.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def test_the_lab_verifier_accepts_zoe_datas_token_once_for_that_person_and_audience_only(lab):
    clock = Clock(1_000_000.0)
    v = lab.Verifier(KEY, clock=clock)
    assert os_speaker.AUDIENCES == lab.AUDIENCES and os_speaker.UNPROVEN == lab.UNPROVEN
    assert os_speaker.IDENTITY == lab.IDENTITY and os_speaker.IDENTITY_CREDS == lab.IDENTITY_CREDS
    tok = os_speaker.mint(WHO, "forget.scoped", key=KEY, now=clock.t)
    assert not v.check(tok, WHO, "forget.person") and not v.check(tok, "member-b", "forget.scoped")
    assert v.check(tok, WHO, "forget.scoped")
    assert not v.check(tok, WHO, "forget.scoped")                          # its nonce is spent
    assert not v.check(os_speaker.mint(WHO, "undo", key=bytes(32), now=clock.t), WHO, "undo")      # another secret


@pytest.mark.parametrize("issued_ms,now_ms,ok", [
    (0, 59_999, True), (0, 60_000, False),                                 # accepted only while now < issued + 60 s
    (2_000, 0, True), (2_001, 0, False),                                   # issued at most 2 s ahead
    (-1, 0, False),                                                        # never before the kernel's start
])
def test_the_time_window_matches_to_the_millisecond(lab, issued_ms, now_ms, ok):
    start = 1_000_000.0
    clock = Clock(start)
    v = lab.Verifier(KEY, clock=clock)
    tok = os_speaker.mint(WHO, "revoke", key=KEY, now=start + issued_ms / 1000)
    clock.t = start + now_ms / 1000
    assert v.check(tok, WHO, "revoke") is ok


def test_milliseconds_are_rounded_like_the_kernel(lab):
    for now in (1000.0004, 1000.0005, 1000.0006, 1763251200.1235):
        assert os_speaker._ms(now) == lab._ms(now)


def test_the_keyed_digest_is_the_kernels(lab):
    src = (LAB / "authority.py").read_text("utf-8")
    body = src[src.index("def keyed_digest("):]
    body = body[:body.index("\n\n\n")]
    scope: dict = {}
    domain = re.search(r'_DIGEST_DOMAIN = (b"[^"]*")', src).group(1)
    exec("import hashlib, hmac, json\n_DIGEST_DOMAIN = " + domain + "\n" + body, scope)      # the kernel's own function, run here
    for member, lst, item in ((WHO, "Shopping", " Oat Milk "), ("member-b", "Shopping", "oat milk"), (WHO, "Café", "crème")):
        assert os_speaker.keyed_digest(KEY, member, lst, item) == scope["keyed_digest"](KEY, member, lst, item)
