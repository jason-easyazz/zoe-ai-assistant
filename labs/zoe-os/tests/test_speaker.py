"""A surface cannot speak for a person: every person-bound op needs a speaker assertion (``zoe_os/speaker.py``).

The fault this pins: the kernel bound the person of ``forget`` / ``grant`` / ``revoke`` / ``undo`` / ``explain`` (and the authority's
``erase_member`` / ``erase_item``) to the surface's CLAIM, so any holder of a surface credential could erase or re-grant ANY member.
Now the request carries an assertion minted with the speaker-verifier's secret, for the person who decided and for that verb, used
once, within its TTL, after this kernel started; the member-delete ``eraser`` credential is the one exception (the forget family).

In-process tests drive the REAL dispatch (``KernelServer._answer``) with an injected clock; the socket tests run the kernel and the
authority daemon as their own processes. Each rule has a negative control. Synthetic people and words only.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from world import ALEX, KID, SAM, household

from zoe_os import speaker, wire
from zoe_os.client import Client
from zoe_os.server import KernelServer
from zoe_os.spine import Refused

HERE = Path(__file__).parent
T0 = 1_900_000_000.0
WORDS = "synthetic marmalade lantern words"

#: every op of the dispatch table that is NOT a person's decision, and why. A new op must be put in one set or the other.
NOT_PERSON_BOUND = {
    "publish": "an origin adapter records what happened; the read/emit rules decide",
    "subscribe": "reads are filtered by grants", "poll": "reads are filtered by grants",
    "act": "a skill acts under a person's grant; the dispatcher decides",
    "describe": "the caller's own manifest", "health": "no person",
    "list_digest": "keyed digests for the reconciler's own credential only (authority.py): no surface reads it",
}
#: the least each person-bound op takes, all naming SAM as the one who decides
MINIMAL = {
    "forget": {"subject": SAM, "actor": SAM}, "erase_member": {"subject": SAM},
    "erase_item": {"digest": "0" * 16, "subject": SAM}, "grant": {"person": SAM, "skill": "toy"},
    "revoke": {"person": SAM, "skill": "toy"}, "undo": {"person": SAM, "actor": SAM},
    "explain": {"person": SAM, "last": "action"},
}
AUDIENCE = {"forget": "forget.person", "erase_member": "forget.person", "erase_item": "forget.scoped",
            "grant": "grant", "revoke": "revoke", "undo": "undo", "explain": "explain"}


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def srv():
    base = Path(tempfile.mkdtemp(prefix="zs"))           # short: a socket path is <= 107 bytes
    k = household(base)
    k.grants.install({"skill": "toy", "reads": {"utterance": "private"}})
    k.grants.install({"skill": "eraser"})
    clock = Clock(T0)
    s = KernelServer(k, str(base / "k.sock"), clock=clock)       # bound, never served: _answer is driven directly
    s.issue("panel", surface=True)
    s.issue("voice")
    s.issue("eraser", eraser=True)
    yield s, k, clock
    s.shutdown()
    k.close()
    shutil.rmtree(base, ignore_errors=True)


def ask(s: KernelServer, name: str, op: str, **args) -> dict:
    return s._answer(SimpleNamespace(name=name, subs={}, next_sub=1), {"v": 1, "id": 1, "op": op, "args": args})


def mint(s, clock, person, audience, **kw) -> str:
    return speaker.mint(s.speaker_secret(), person, audience, now=kw.pop("now", clock.t), **kw)


def unproven(reply: dict) -> bool:
    return not reply["ok"] and reply["error"]["code"] == wire.REFUSED and reply["error"]["reason"].endswith(speaker.UNPROVEN)


def recorder(monkeypatch, s) -> list:
    calls: list = []
    for op in speaker.PERSON_BOUND:
        monkeypatch.setattr(s, "_op_" + op, lambda conn, a, op=op: calls.append(op) or {}, raising=False)
    return calls


# ---- structure: one table, one check ----------------------------------------------------------------------------------------------
def test_every_op_in_the_dispatch_table_is_classified_and_every_person_bound_op_names_who_must_be_proven():
    import authority
    import shadow_daemon
    assert speaker.PERSON_BOUND.isdisjoint(NOT_PERSON_BOUND)
    assert speaker.PERSON_BOUND | set(NOT_PERSON_BOUND) == set(wire.OPS)           # a new op must be classified
    assert set(MINIMAL) == speaker.PERSON_BOUND and speaker.ERASER_MAY <= speaker.PERSON_BOUND
    served = {n[4:] for cls in (KernelServer, authority.AuthorityServer, shadow_daemon.ShadowServer)
              for n in vars(cls) if n.startswith("_op_")}
    assert served <= set(wire.OPS)                                                  # no op the table does not name
    for op in speaker.PERSON_BOUND:
        assert "speaker" in wire.OPS[op] and wire.check_args(op, MINIMAL[op]) == ""
        assert speaker.binding(op, MINIMAL[op]) == (SAM, AUDIENCE[op])
    for op, spec in wire.OPS.items():
        if op in NOT_PERSON_BOUND:
            assert speaker.binding(op, {name: SAM for name in spec}) is None
    assert speaker.binding("explain", {"seq": 3}) is None                             # the caller's own standing: no person
    assert speaker.binding("forget", {"subject": SAM, "actor": SAM, "topic": "x"}) == (SAM, "forget.scoped")
    for cls in (authority.AuthorityServer, shadow_daemon.ShadowServer):           # the subclasses inherit the one check
        assert cls._answer is KernelServer._answer and cls._prove_speaker is KernelServer._prove_speaker


@pytest.mark.parametrize("op", sorted(speaker.PERSON_BOUND))
def test_a_surface_credential_alone_is_refused_for_every_person_bound_op_and_a_proven_one_goes_through(srv, monkeypatch, op):
    s, k, clock = srv
    calls = recorder(monkeypatch, s)
    before = k.spine.refusals
    assert unproven(ask(s, "panel", op, **MINIMAL[op]))                              # the surface's word alone
    assert unproven(ask(s, "panel", op, **MINIMAL[op], speaker=mint(s, clock, ALEX, AUDIENCE[op])))   # someone else's
    reply = ask(s, "voice", op, **MINIMAL[op], speaker=mint(s, clock, SAM, AUDIENCE[op]))
    assert reply["error"]["reason"].endswith("only a person, through a trusted surface")   # a skill, even holding a token
    assert calls == [] and k.spine.refusals == before + 3                            # refused, recorded, and the op never ran
    assert ask(s, "panel", op, **MINIMAL[op], speaker=mint(s, clock, SAM, AUDIENCE[op]))["ok"]
    assert calls == [op]
    eraser = ask(s, "eraser", op, **MINIMAL[op])
    assert eraser["ok"] is (op in speaker.ERASER_MAY)                                # member delete: the forget family only
    assert s._speaker.refused["missing"] == 1 and s._speaker.refused["person"] == 1


def test_control_without_the_one_check_a_surface_alone_reaches_every_person_bound_op(srv, monkeypatch):
    s, _k, _clock = srv
    calls = recorder(monkeypatch, s)
    monkeypatch.setattr(s, "_prove_speaker", lambda *a: None)
    for op in sorted(speaker.PERSON_BOUND):
        assert ask(s, "panel", op, **MINIMAL[op])["ok"]
    assert sorted(calls) == sorted(speaker.PERSON_BOUND)                              # the gap the check closes


# ---- an assertion is for one person, one verb, once, for a minute, after this start -----------------------------------------------
def test_an_assertion_for_one_person_verb_and_moment_is_good_for_nothing_else(srv):
    s, k, clock = srv
    forget = {"subject": SAM, "actor": SAM}
    assert unproven(ask(s, "panel", "forget", **forget, speaker=mint(s, clock, ALEX, "forget.person")))   # A's, for B
    assert unproven(ask(s, "panel", "forget", **forget, speaker=mint(s, clock, SAM, "grant")))           # another verb
    assert unproven(ask(s, "panel", "forget", **forget, speaker=mint(s, clock, SAM, "forget.scoped")))   # "forget that" != "forget me"
    assert unproven(ask(s, "panel", "forget", **forget, topic="x", speaker=mint(s, clock, SAM, "forget.person")))
    old = mint(s, clock, SAM, "forget.person")
    clock.t += speaker.TTL_S + 1
    assert unproven(ask(s, "panel", "forget", **forget, speaker=old))                                    # past the TTL
    ahead = mint(s, clock, SAM, "forget.person", now=clock.t + speaker.SKEW_S + 1)
    assert unproven(ask(s, "panel", "forget", **forget, speaker=ahead))                                  # from the future
    good = mint(s, clock, SAM, "forget.person")
    assert ask(s, "panel", "forget", **forget, speaker=good)["ok"]
    assert unproven(ask(s, "panel", "forget", **forget, speaker=good))                                   # twice
    assert dict(s._speaker.refused) == {"person": 1, "audience": 3, "expired": 1, "future": 1, "replay": 1}


def test_the_speaker_is_the_actor_and_the_kernels_own_rule_still_links_them_to_the_subject(srv):
    s, k, clock = srv
    sams = mint(s, clock, SAM, "forget.person")
    reply = ask(s, "panel", "forget", subject=ALEX, actor=SAM, speaker=sams)       # proven SAM asking to forget ALEX
    assert not reply["ok"] and reply["error"]["reason"].endswith("not theirs to forget")
    assert ask(s, "panel", "forget", subject=KID, actor=ALEX, speaker=mint(s, clock, ALEX, "forget.person"))["ok"]  # a guardian
    child = {"person": KID, "skill": "toy", "guardian": ALEX}
    assert ask(s, "panel", "grant", **child, speaker=mint(s, clock, ALEX, "grant"))["ok"]                      # the guardian signs
    assert unproven(ask(s, "panel", "grant", **child, speaker=mint(s, clock, KID, "grant")))
    adult = {"person": SAM, "skill": "toy", "guardian": ALEX}                                                  # no guardian for an adult
    assert unproven(ask(s, "panel", "grant", **adult, speaker=mint(s, clock, ALEX, "grant")))
    assert ask(s, "panel", "grant", **adult, speaker=mint(s, clock, SAM, "grant"))["ok"]


def test_a_forged_and_a_truncated_token_are_refused_in_the_same_shape(srv):
    s, k, clock = srv
    good = mint(s, clock, SAM, "forget.person")
    other = speaker.mint(secrets.token_bytes(32), SAM, "forget.person", now=clock.t)     # a key the kernel never issued
    flipped = good[:-1] + ("0" if good[-1] != "0" else "1")
    bad = [flipped, other, good[: len(good) // 2], good.split(".")[0], "." + good.split(".")[1], "x" * 600, "é." + "0" * 64, ""]
    replies = [ask(s, "panel", "forget", subject=SAM, actor=SAM, speaker=t) for t in bad]
    assert all(r == replies[0] for r in replies) and unproven(replies[0])             # one shape: nothing says which check failed
    refusals = [e.payload for e in k.spine._fetch(0, None, None, 100) if e.kind.value == "refusal"]
    assert refusals == [refusals[0]] * len(bad)
    assert sum(s._speaker.refused.values()) == len(bad) and s._speaker.accepted == 0
    assert ask(s, "panel", "forget", subject=SAM, actor=SAM, speaker=good)["ok"]        # the real one was never spent


def test_a_restart_kills_every_assertion_issued_before_it_even_with_the_same_secret():
    key, clock = secrets.token_bytes(32), Clock(T0)
    first = speaker.Verifier(key, clock=clock)
    clock.t += 1
    token = speaker.mint(key, SAM, "forget.person", now=clock.t)
    assert first.check(token, SAM, "forget.person")
    clock.t += 1
    again = speaker.Verifier(key, clock=clock)                  # the kernel restarted: its used-nonce store is empty
    assert not again.check(token, SAM, "forget.person") and again.refused == {"before start": 1}
    control = speaker.Verifier(key, clock=clock)
    control.started_ms = 0                                       # control: without the start rule the replay is accepted
    assert control.check(token, SAM, "forget.person")


def test_the_used_nonce_store_is_bounded_fails_closed_when_full_and_frees_itself_after_the_ttl():
    key, clock = secrets.token_bytes(32), Clock(T0)
    v = speaker.Verifier(key, clock=clock, max_live=2)
    clock.t += 1
    tokens = [speaker.mint(key, SAM, "undo", now=clock.t) for _ in range(3)]
    assert v.check(tokens[0], SAM, "undo") and v.check(tokens[1], SAM, "undo")
    assert not v.check(tokens[2], SAM, "undo") and v.refused == {"busy": 1}             # full: refuse, never forget a nonce early
    clock.t += speaker.TTL_S + speaker.SKEW_S + 1
    assert v.check(speaker.mint(key, SAM, "undo", now=clock.t), SAM, "undo") and len(v._used) == 1


# ---- through the socket ----------------------------------------------------------------------------------------------------------
def test_through_the_socket_the_surface_alone_is_refused_and_the_members_own_forget_works_end_to_end():
    from procs import HELPER, Box
    box = Box()
    try:
        box.client("voice").publish("utterance", subject=SAM, payload={"text": WORDS})
        bare = Client(box.socket, "panel", bytes.fromhex(box.creds["panel"]))       # a panel credential and nothing else
        for op in ("forget", "grant", "revoke", "undo", "explain"):
            args = {**MINIMAL[op], **({"skill": HELPER} if "skill" in MINIMAL[op] else {})}
            with pytest.raises(Refused, match=speaker.UNPROVEN):
                bare.call(op, **args)
        with pytest.raises(Refused, match=speaker.UNPROVEN):
            bare.call("forget", subject=SAM, actor=SAM, speaker=box.assertion(ALEX, "forget.person"))
        assert WORDS.encode() in box.on_disk()                                        # nothing was forgotten by the refused calls
        token = box.assertion(SAM, "forget.person")
        assert bare.call("forget", subject=SAM, actor=SAM, speaker=token)["count"] >= 1   # SAM: "forget everything about me"
        with pytest.raises(Refused, match=speaker.UNPROVEN):
            bare.call("forget", subject=SAM, actor=SAM, speaker=token)                # used once
        assert WORDS.encode() not in box.on_disk()
        early, old_key = box.assertion(SAM, "forget.person"), box.speaker_key
        box.restart()
        assert box.speaker_key != old_key                                              # a fresh secret per start
        again = Client(box.socket, "panel", bytes.fromhex(box.creds["panel"]))
        with pytest.raises(Refused, match=speaker.UNPROVEN):
            again.call("forget", subject=SAM, actor=SAM, speaker=early)              # minted before the restart: dead
        refusals = json.dumps(box.audit())
        for needle in (token, early, old_key.hex(), box.speaker_key.hex()):
            assert needle not in refusals and needle.encode() not in box.on_disk()
        assert refusals.count(speaker.UNPROVEN) == 8
    finally:
        box.close()


def test_the_token_and_the_secret_reach_no_file_but_the_identity_file_and_no_log_and_the_eraser_still_erases():
    from test_authority import A, B
    state = Path(tempfile.mkdtemp(prefix="zt"))
    (state / "people.json").write_text(json.dumps({"people": [A, B]}))
    proc = subprocess.Popen([sys.executable, "-B", str(HERE / "authority_proc.py"), "--dir", str(state)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env={"PATH": os.environ["PATH"], "HOME": str(state)})
    try:
        assert proc.stdout.readline().startswith("zoe-os authority kernel up")
        ident = json.loads((state / speaker.IDENTITY_CREDS).read_text())
        assert (state / speaker.IDENTITY_CREDS).stat().st_mode & 0o777 == 0o600 and ident["ttl_s"] == speaker.TTL_S
        key = bytes.fromhex(ident["creds"][speaker.IDENTITY])
        raw = json.loads((state / "creds.json").read_text())
        creds = {n: bytes.fromhex(v) for n, v in raw["creds"].items()}
        voice, brain = Client(raw["socket"], "voice", creds["voice"]), Client(raw["socket"], "brain", creds["brain"])
        panel = Client(raw["socket"], "panel", creds["panel"])
        for who in (A, B):
            said = voice.publish("utterance", subject=who, payload={"text": WORDS})
            brain.call("act", action="list.add", on_behalf_of=who, args={"item": "synthetic item", "asked": said.seq},
                       caused_by=[said.seq])
        good = speaker.mint(key, A, "forget.person")
        tokens, texts = [good, speaker.mint(key, B, "forget.person"), speaker.mint(key, A, "grant")], []
        for bad in (tokens[1], tokens[2], good[:-2] + "00", good[:40]):
            with pytest.raises(Refused) as exc:
                panel.call("forget", subject=A, actor=A, speaker=bad)
            texts.append(str(exc.value))
        assert panel.call("forget", subject=A, actor=A, speaker=good)["count"] >= 2
        with pytest.raises(Refused) as exc:
            panel.call("forget", subject=A, actor=A, speaker=good)
        texts.append(str(exc.value))
        erase = json.loads((state / "erase-creds.json").read_text())["creds"]["eraser"]
        assert Client(raw["socket"], "eraser", bytes.fromhex(erase)).call("erase_member", subject=B)["verified"] is True
    finally:
        proc.kill()
        out, err = proc.communicate(timeout=10)
    dkey = bytes.fromhex(json.loads((state / "reconcile-creds.json").read_text())["digest_key"])
    needles = [key, key.hex().encode(), dkey, dkey.hex().encode(), *(t.encode() for t in tokens),
               *(t.split(".")[1].encode() for t in tokens)]
    credential_files = {"creds.json", "erase-creds.json", speaker.IDENTITY_CREDS, "reconcile-creds.json"}
    scanned = [f for f in state.rglob("*") if f.is_file() and f.name not in credential_files]
    assert any(f.name.startswith("zoe-os.db") for f in scanned)
    for f in scanned:
        data = f.read_bytes()
        assert not [n for n in needles if n in data], f.name
    for n in needles:
        assert n.decode("latin-1") not in out + err + "".join(texts)
    for other in ("creds.json", "erase-creds.json", speaker.IDENTITY_CREDS, "reconcile-creds.json"):
        text = (state / other).read_text()
        assert (key.hex() in text) is (other == speaker.IDENTITY_CREDS) and (dkey.hex() in text) is (other == "reconcile-creds.json")
    assert all(t.endswith(speaker.UNPROVEN) for t in texts)
    shutil.rmtree(state, ignore_errors=True)


# ---- the acceptance window, to the millisecond -------------------------------------------------------------------------------------
TTL_MS, SKEW_MS = int(speaker.TTL_S * 1000), int(speaker.SKEW_S * 1000)


def _at(v: speaker.Verifier, clock: Clock, key: bytes, *, issued_ms: int, at_ms: int, nonce: str | None = None) -> bool:
    clock.t = at_ms / 1000
    return v.check(speaker.mint(key, SAM, "undo", now=issued_ms / 1000, nonce=nonce), SAM, "undo")


@pytest.mark.parametrize("edge, ok_at, bad_at", [
    ("expiry", TTL_MS - 1, TTL_MS),                    # age TTL-1 ms: accepted; age exactly TTL: expired
    ("future", -SKEW_MS, -SKEW_MS - 1),                # issued SKEW ahead: accepted; SKEW + 1 ms ahead: refused
])
def test_the_acceptance_window_on_both_sides_of_the_millisecond(edge, ok_at, bad_at):
    key, t0 = secrets.token_bytes(32), int(T0 * 1000)
    clock = Clock(T0)
    v = speaker.Verifier(key, clock=clock)
    issued = t0 + 10_000
    assert _at(v, clock, key, issued_ms=issued, at_ms=issued + ok_at)
    assert not _at(v, clock, key, issued_ms=issued, at_ms=issued + bad_at)
    assert v.refused == {"expired" if edge == "expiry" else "future": 1}


def test_the_kernel_start_on_both_sides_of_the_millisecond():
    key, clock = secrets.token_bytes(32), Clock(T0)
    v = speaker.Verifier(key, clock=clock)
    assert _at(v, clock, key, issued_ms=v.started_ms, at_ms=v.started_ms + 5)
    assert not _at(v, clock, key, issued_ms=v.started_ms - 1, at_ms=v.started_ms + 5)
    assert v.refused == {"before start": 1}


def test_the_reviewers_replay_a_token_from_the_edge_of_the_skew_presented_again_as_its_nonce_would_have_been_pruned():
    key, clock = secrets.token_bytes(32), Clock(T0)
    v = speaker.Verifier(key, clock=clock)
    accept = int(T0 * 1000) + 1000
    issued, nonce = accept + SKEW_MS, "a" * 32                      # issued 2 s ahead: inside the skew
    assert _at(v, clock, key, issued_ms=issued, at_ms=accept, nonce=nonce)
    # the old code pruned the nonce at accept + TTL + SKEW, when the token's age is exactly TTL and the old "> TTL" let it in
    for at in (accept + TTL_MS + SKEW_MS - 1, accept + TTL_MS + SKEW_MS, accept + TTL_MS + SKEW_MS + 1):
        assert not _at(v, clock, key, issued_ms=issued, at_ms=at, nonce=nonce)
    assert v.accepted == 1


def test_a_used_nonce_is_kept_to_the_millisecond_its_token_can_no_longer_be_accepted_and_no_longer():
    key, clock = secrets.token_bytes(32), Clock(T0)
    v = speaker.Verifier(key, clock=clock)
    issued = int(T0 * 1000) + 1000
    assert _at(v, clock, key, issued_ms=issued, at_ms=issued + 5, nonce="b" * 32)
    for at, held in ((issued + TTL_MS + SKEW_MS - 1, True), (issued + TTL_MS + SKEW_MS, False)):
        clock.t = at / 1000
        v._prune(at)
        assert ("b" * 32 in v._used) is held
    assert not _at(v, clock, key, issued_ms=issued, at_ms=issued + TTL_MS + SKEW_MS, nonce="b" * 32)   # pruned, and expired


def test_property_for_any_issue_time_inside_the_skew_and_any_two_presentations_the_second_is_never_accepted():
    import random
    rnd = random.Random(20261116)
    key, accepted = secrets.token_bytes(32), 0
    for _ in range(3000):
        clock = Clock(T0)
        v = speaker.Verifier(key, clock=clock, max_live=8)
        first = v.started_ms + TTL_MS + rnd.randrange(0, 5000)            # the first presentation
        # the issue time, anywhere a token could be accepted at ``first`` (and a few ms outside), often exactly at the skew edge
        issued = first + (SKEW_MS if rnd.random() < 0.3 else rnd.randrange(-TTL_MS - 5, SKEW_MS + 6))
        edges = (issued + TTL_MS, first + TTL_MS + SKEW_MS, issued + TTL_MS + SKEW_MS)     # where an off-by-one would live
        second = rnd.choice(edges) + rnd.randrange(-3, 4) if rnd.random() < 0.5 else first + rnd.randrange(0, TTL_MS + SKEW_MS + 10)
        first, second = min(first, second), max(first, second)
        nonce = secrets.token_hex(16)
        once = _at(v, clock, key, issued_ms=issued, at_ms=max(first, v.started_ms), nonce=nonce)
        twice = _at(v, clock, key, issued_ms=issued, at_ms=max(second, v.started_ms), nonce=nonce)
        assert not (once and twice)
        accepted += once
    assert accepted > 1000                                               # the property was exercised, not vacuous


def test_the_cap_holds_under_the_issue_time_retention_and_frees_by_issue_time_not_arrival():
    key, clock = secrets.token_bytes(32), Clock(T0)
    v = speaker.Verifier(key, clock=clock, max_live=2)
    t = v.started_ms
    assert _at(v, clock, key, issued_ms=t + 2000, at_ms=t + 100)        # issued later (inside the skew): kept longer
    assert _at(v, clock, key, issued_ms=t + 50, at_ms=t + 100)
    assert not _at(v, clock, key, issued_ms=t + 100, at_ms=t + 100) and v.refused == {"busy": 1}
    assert _at(v, clock, key, issued_ms=t + 50 + TTL_MS - 1, at_ms=t + 50 + TTL_MS + SKEW_MS)   # the t+50 nonce freed first
    assert len(v._used) == 2
