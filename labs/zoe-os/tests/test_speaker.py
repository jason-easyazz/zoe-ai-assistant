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
    return speaker.mint(s.speaker_secret(), person, audience, generation=s.speaker_generation(), now=kw.pop("now", clock.t), **kw)


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
    other = speaker.mint(secrets.token_bytes(32), SAM, "forget.person", generation=s.speaker_generation(), now=clock.t)  # foreign key
    flipped = good[:-1] + ("0" if good[-1] != "0" else "1")
    bad = [flipped, other, good[: len(good) // 2], good.split(".")[0], "." + good.split(".")[1], "x" * 600, "é." + "0" * 64, ""]
    replies = [ask(s, "panel", "forget", subject=SAM, actor=SAM, speaker=t) for t in bad]
    assert all(r == replies[0] for r in replies) and unproven(replies[0])             # one shape: nothing says which check failed
    refusals = [e.payload for e in k.spine._fetch(0, None, None, 100) if e.kind.value == "refusal"]
    assert refusals == [refusals[0]] * len(bad)
    assert sum(s._speaker.refused.values()) == len(bad) and s._speaker.accepted == 0
    assert ask(s, "panel", "forget", subject=SAM, actor=SAM, speaker=good)["ok"]        # the real one was never spent


def test_a_restart_kills_every_assertion_minted_for_the_run_before_it_even_with_the_same_secret():
    key, clock = secrets.token_bytes(32), Clock(T0)
    first = speaker.Verifier(key, clock=clock)
    clock.t += 1
    token = speaker.mint(key, SAM, "forget.person", generation=first.generation, now=clock.t)
    assert first.check(token, SAM, "forget.person")
    clock.t += 1
    again = speaker.Verifier(key, clock=clock)                  # the kernel restarted: a new generation, an empty nonce store
    assert again.generation != first.generation
    assert not again.check(token, SAM, "forget.person") and again.refused == {"generation": 1}
    control = speaker.Verifier(key, clock=clock)
    control.generation = first.generation                       # control: a run that kept the old generation accepts the replay
    assert control.check(token, SAM, "forget.person")


def test_a_full_nonce_store_refuses_rolls_the_generation_and_never_forgets_a_nonce_early():
    key, clock = secrets.token_bytes(32), Clock(T0)
    rolled: list = []
    v = speaker.Verifier(key, clock=clock, max_live=2, on_roll=rolled.append)
    clock.t += 1                                                         # ... and then the clock is STUCK here
    old = v.generation
    tokens = [speaker.mint(key, SAM, "undo", generation=old, now=clock.t) for _ in range(3)]
    assert v.check(tokens[0], SAM, "undo") and v.check(tokens[1], SAM, "undo")
    assert not v.check(tokens[2], SAM, "undo") and v.refused == {"busy": 1}             # full: refused, and the generation rolls
    assert v.generation != old and rolled == [v.generation] and v.rolls == {"store full": 1} and not v._used
    assert not v.check(tokens[0], SAM, "undo") and v.refused["generation"] == 1        # an accepted token never comes back
    fresh = speaker.mint(key, SAM, "undo", generation=v.generation, now=clock.t)
    assert v.check(fresh, SAM, "undo") and len(v._used) == 1                            # a stuck clock never locks clients out


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
        gen = ident["generation"]
        good = speaker.mint(key, A, "forget.person", generation=gen)
        tokens, texts = [good, speaker.mint(key, B, "forget.person", generation=gen),
                         speaker.mint(key, A, "grant", generation=gen)], []
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


def _tok(key: bytes, gen: str, issued_ms: int, nonce: str | None = None) -> str:
    return speaker.mint(key, SAM, "undo", generation=gen, now=issued_ms / 1000, nonce=nonce)


def _present(v: speaker.Verifier, clock: Clock, token: str, at_ms: int) -> bool:
    clock.t = at_ms / 1000
    return v.check(token, SAM, "undo")


def _at(v: speaker.Verifier, clock: Clock, key: bytes, *, issued_ms: int, at_ms: int, nonce: str | None = None) -> bool:
    return _present(v, clock, _tok(key, v.generation, issued_ms, nonce), at_ms)


@pytest.mark.parametrize("edge, ok_at, bad_at", [
    ("expiry", TTL_MS - 1, TTL_MS),                    # age TTL-1 ms: accepted; age exactly TTL: expired
    ("future", -SKEW_MS, -SKEW_MS - 1),                # issued SKEW ahead: accepted; SKEW + 1 ms ahead: refused
])
def test_the_acceptance_window_on_both_sides_of_the_millisecond(edge, ok_at, bad_at):
    key, t0 = secrets.token_bytes(32), int(T0 * 1000)
    clock = Clock(T0)
    issued = t0 + 10_000
    ok_run, bad_run = speaker.Verifier(key, clock=clock), speaker.Verifier(key, clock=clock)   # one run per side: time never goes back
    assert _at(ok_run, clock, key, issued_ms=issued, at_ms=issued + ok_at)
    assert not _at(bad_run, clock, key, issued_ms=issued, at_ms=issued + bad_at)
    assert bad_run.refused == {"expired" if edge == "expiry" else "future": 1} and not ok_run.refused


def test_the_reviewers_restart_case_issued_t_plus_2_accepted_at_t_restart_at_t_plus_half_is_refused():
    key, t = secrets.token_bytes(32), int(T0 * 1000)
    clock = Clock(T0)
    old = speaker.Verifier(key, clock=clock)
    token = _tok(key, old.generation, t + SKEW_MS)                      # issued T+2 s (inside the skew)
    assert _present(old, clock, token, t)                               # accepted at T
    clock.t = (t + 500) / 1000
    new = speaker.Verifier(key, clock=clock)                            # restart at T+0.5 s, the SAME provisioned secret
    assert not _present(new, clock, token, t + 600) and new.refused == {"generation": 1}
    # and an honest token for the new run, issued right at its start, is accepted at once: no dead window after a start
    assert _present(new, clock, _tok(key, new.generation, t + 500), t + 600)


def test_a_wall_clock_stepped_backwards_between_runs_still_refuses_the_old_runs_tokens():
    key, t = secrets.token_bytes(32), int(T0 * 1000)
    clock = Clock(T0)
    old = speaker.Verifier(key, clock=clock)
    token = _tok(key, old.generation, t)
    assert _present(old, clock, token, t + 10)
    clock.t = (t - 3_600_000) / 1000                                    # the next run starts an hour EARLIER on the wall clock
    new = speaker.Verifier(key, clock=clock)
    assert not _present(new, clock, token, t + 20)                      # a start-time rule alone would have accepted this


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
        first = int(T0 * 1000) + TTL_MS + rnd.randrange(0, 5000)          # the first presentation
        # the issue time, anywhere a token could be accepted at ``first`` (and a few ms outside), often exactly at the skew edge
        issued = first + (SKEW_MS if rnd.random() < 0.3 else rnd.randrange(-TTL_MS - 5, SKEW_MS + 6))
        edges = (issued + TTL_MS, first + TTL_MS + SKEW_MS, issued + TTL_MS + SKEW_MS)     # where an off-by-one would live
        second = rnd.choice(edges) + rnd.randrange(-3, 4) if rnd.random() < 0.5 else first + rnd.randrange(0, TTL_MS + SKEW_MS + 10)
        first, second = min(first, second), max(first, second)
        nonce = secrets.token_hex(16)
        once = _at(v, clock, key, issued_ms=issued, at_ms=first, nonce=nonce)
        twice = _at(v, clock, key, issued_ms=issued, at_ms=second, nonce=nonce)
        assert not (once and twice)
        accepted += once
    assert accepted > 1000                                               # the property was exercised, not vacuous


def test_property_across_restarts_nothing_a_previous_run_could_accept_is_accepted_by_a_later_one():
    """Accept, restart at a random offset (before the accept, inside the skew, after, with the clock stepped back), re-present."""
    import random
    rnd = random.Random(20261117)
    key, accepted = secrets.token_bytes(32), 0
    for _ in range(2000):
        t = int(T0 * 1000) + rnd.randrange(0, 10_000)
        clock = Clock(t / 1000)
        runs = [speaker.Verifier(key, clock=clock)]
        token = _tok(key, runs[0].generation, t + rnd.randrange(-TTL_MS, SKEW_MS + 1), secrets.token_hex(16))
        first = t + rnd.randrange(0, 100)
        accepted += (once := _present(runs[0], clock, token, first))
        for _ in range(rnd.randrange(1, 4)):                             # one to three restarts, each at a random wall-clock time
            clock.t = (first + rnd.randrange(-3_600_000, TTL_MS + SKEW_MS + 10)) / 1000
            runs.append(speaker.Verifier(key, clock=clock))
            again = _present(runs[-1], clock, token, first + rnd.choice((-SKEW_MS, 0, 1, 500, SKEW_MS, TTL_MS - 1)))
            assert not again                                             # never accepted by a later run, accepted before or not
    assert accepted > 1000


def test_used_nonces_are_freed_by_their_tokens_issue_time_not_by_arrival():
    key, clock = secrets.token_bytes(32), Clock(T0)
    v = speaker.Verifier(key, clock=clock, max_live=3)
    t = int(T0 * 1000)
    assert _at(v, clock, key, issued_ms=t + 2000, at_ms=t + 100)        # issued later (inside the skew): kept longer
    assert _at(v, clock, key, issued_ms=t + 50, at_ms=t + 100)
    assert _at(v, clock, key, issued_ms=t + 50 + TTL_MS - 1, at_ms=t + 50 + TTL_MS + SKEW_MS)   # prunes the t+50 nonce only
    assert len(v._used) == 2 and not v.rolls


# ---- a guardian link reaches only a CHILD, by the kernel's own roster -----------------------------------------------------------------
def _reason(reply: dict) -> str:
    return "" if reply["ok"] else reply["error"]["reason"]


def test_a_proven_guardian_cannot_forget_or_undo_for_an_adult_who_carries_a_guardian_link(srv):
    from zoe_os.capability import ADULT, Person
    s, k, clock = srv
    k.grants.people[SAM] = Person(SAM, ADULT, guardians=(ALEX,))      # an adult with a (stale or mistaken) guardian link
    for scope in ({}, {"topic": "synthetic words"}):
        aud = "forget.scoped" if scope else "forget.person"
        reply = ask(s, "panel", "forget", subject=SAM, actor=ALEX, **scope, speaker=mint(s, clock, ALEX, aud))
        assert _reason(reply).endswith("not theirs to forget")
    reply = ask(s, "panel", "undo", person=SAM, actor=ALEX, speaker=mint(s, clock, ALEX, "undo"))
    assert _reason(reply).endswith("not theirs to undo")
    assert not k.explainer._is(ALEX, SAM)                               # nor may the guardian see the adult's private events
    reply = ask(s, "panel", "grant", person=SAM, skill="toy", guardian=ALEX, speaker=mint(s, clock, ALEX, "grant"))
    assert unproven(reply)                                              # grant: the adult's own word only (binding.is_child)


def test_the_same_link_on_a_child_reaches_and_stops_the_moment_the_roster_says_adult(srv):
    from zoe_os.capability import ADULT, Person
    s, k, clock = srv
    assert ask(s, "panel", "forget", subject=KID, actor=ALEX, speaker=mint(s, clock, ALEX, "forget.person"))["ok"]
    undo = ask(s, "panel", "undo", person=KID, actor=ALEX, speaker=mint(s, clock, ALEX, "undo"))
    assert not _reason(undo).endswith("not theirs to undo")             # allowed in principle (KID has nothing to undo)
    assert k.explainer._is(ALEX, KID)
    assert ask(s, "panel", "grant", person=KID, skill="toy", guardian=ALEX, speaker=mint(s, clock, ALEX, "grant"))["ok"]
    k.grants.people[KID] = Person(KID, ADULT, guardians=(ALEX,))      # KID turns 18; the link is left behind
    reply = ask(s, "panel", "forget", subject=KID, actor=ALEX, speaker=mint(s, clock, ALEX, "forget.person"))
    assert _reason(reply).endswith("not theirs to forget")              # immediately: the roster's role decides, not the link
    assert _reason(ask(s, "panel", "undo", person=KID, actor=ALEX, speaker=mint(s, clock, ALEX, "undo"))).endswith(
        "not theirs to undo")
    assert not k.explainer._is(ALEX, KID)
    assert ask(s, "panel", "forget", subject=KID, actor=KID, speaker=mint(s, clock, KID, "forget.person"))["ok"]   # their own


# ---- the wall clock may do anything inside one run ---------------------------------------------------------------------------------
def test_the_reviewers_rollback_case_accept_at_t_request_at_t63_clock_back_to_t1_is_refused():
    key, t = secrets.token_bytes(32), int(T0 * 1000)
    clock, rolled = Clock(T0), []
    v = speaker.Verifier(key, clock=clock, on_roll=rolled.append)
    token = _tok(key, v.generation, t)
    assert _present(v, clock, token, t)                                  # accepted at T
    assert _present(v, clock, _tok(key, v.generation, t + 63_000), t + 63_000)   # a fresh request at T+63 s prunes its nonce
    assert len(v._used) == 1                                             # only the fresh request's nonce is left
    assert not _present(v, clock, token, t + 1_000)                      # the wall clock steps back to T+1 s: refused
    assert v.refused == {"generation": 1} and v.rolls == {"clock went back": 1} and rolled == [v.generation]
    assert _present(v, clock, _tok(key, v.generation, t + 1_000), t + 1_100)    # honest clients recover at once (re-read the file)


def test_a_backward_step_inside_the_skew_holds_time_without_rolling_and_still_refuses_the_replay():
    key, t = secrets.token_bytes(32), int(T0 * 1000)
    clock = Clock(T0)
    v = speaker.Verifier(key, clock=clock)
    token = _tok(key, v.generation, t)
    assert _present(v, clock, token, t)
    assert _present(v, clock, _tok(key, v.generation, t + 62_500), t + 62_500)  # prunes the first nonce (t + 62 s)
    assert not _present(v, clock, token, t + 62_500 - SKEW_MS)            # back by exactly the skew: no roll, time holds -> expired
    assert v.refused == {"expired": 1} and not v.rolls


def test_a_large_forward_jump_corrected_back_rolls_once_and_honest_clients_are_not_locked_out():
    key, t = secrets.token_bytes(32), int(T0 * 1000)
    clock, rolled = Clock(T0), []
    v = speaker.Verifier(key, clock=clock, on_roll=rolled.append)
    assert _present(v, clock, _tok(key, v.generation, t + 3_600_000), t + 3_600_000)   # the clock jumps an hour ahead
    gen = v.generation
    honest = _tok(key, gen, t + 10)                                      # minted after the correction, with the file it had
    assert not _present(v, clock, honest, t + 10) and v.rolls == {"clock went back": 1}  # one refusal ...
    assert _present(v, clock, _tok(key, rolled[-1], t + 20), t + 20)      # ... then the re-read generation works at once


def test_a_roll_rewrites_identity_creds_with_the_new_generation(tmp_path):
    from shadow_daemon import write_identity
    base = Path(tempfile.mkdtemp(prefix="zr"))
    k = household(base)
    clock = Clock(T0)
    s = KernelServer(k, str(base / "k.sock"), clock=clock)
    try:
        write_identity(base, s)
        s.on_speaker_roll(lambda _g: write_identity(base, s))
        before = json.loads((base / speaker.IDENTITY_CREDS).read_text())["generation"]
        clock.t = T0 - 60                                                # the wall clock goes back a minute
        s._speaker.check(speaker.mint(s.speaker_secret(), SAM, "undo", generation=before, now=clock.t), SAM, "undo")
        after = json.loads((base / speaker.IDENTITY_CREDS).read_text())
        assert after["generation"] == s.speaker_generation() != before
        assert (base / speaker.IDENTITY_CREDS).stat().st_mode & 0o777 == 0o600
    finally:
        s.shutdown()
        k.close()
        shutil.rmtree(base, ignore_errors=True)


def test_property_within_one_run_an_accepted_token_is_never_accepted_again_for_any_clock_sequence():
    import random
    rnd = random.Random(20261118)
    key, accepted, rolls = secrets.token_bytes(32), 0, 0
    for _ in range(1500):
        t = int(T0 * 1000)
        clock = Clock(t / 1000)
        v = speaker.Verifier(key, clock=clock, max_live=rnd.choice((2, 4, 1024)))
        wall = t
        token = _tok(key, v.generation, wall + rnd.randrange(-TTL_MS, SKEW_MS + 1), secrets.token_hex(16))
        hits = 0
        for _ in range(rnd.randrange(2, 12)):
            step = rnd.choice((0, 0, 1, -1, SKEW_MS, -SKEW_MS, -SKEW_MS - 1, TTL_MS, TTL_MS + SKEW_MS + 1, 63_000, -62_000,
                               3_600_000, -3_600_000, rnd.randrange(-100_000, 100_000)))
            wall += step                                                  # forward jumps, backward steps, repeated values
            if rnd.random() < 0.5:                                        # an honest fresh request at the current wall time
                _present(v, clock, _tok(key, v.generation, wall), wall)
            hits += _present(v, clock, token, wall)
        assert hits <= 1
        accepted += hits
        rolls += sum(v.rolls.values())
    assert accepted > 300 and rolls > 100                                 # both paths were exercised, not vacuous
