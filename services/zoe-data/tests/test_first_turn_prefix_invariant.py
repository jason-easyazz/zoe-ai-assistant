"""LOCK: the wake KV warm and the first real turn must send the SAME system-prompt prefix.

The first voice turn regressed three times (6 s cold vs ~1.2 s warm) because something was added to the brain's
system prompt per member and the wake warm did not render it. This builds the REAL warm request
(``zoe_flue_client._kv_warm_outbound``) and the REAL first-turn request (``run_flue_brain_streaming``) through
production code and requires every system-prompt-affecting envelope line to be byte-identical, for a member, a
guest and a non-English member. A new envelope provider (python or sidecar) that is not classified below fails
until the warm renders it too. Rule: services/zoe-data/AGENTS.md "First-turn prefix lock".
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

import zoe_flue_client as zfc

pytestmark = pytest.mark.ci_safe

SPANISH = "¿Cómo estás esta noche? Estoy muy cansado después de trabajar todo el día."
ENGLISH = "How are you going tonight?"

# Envelope lines the sidecar folds into the SYSTEM PROMPT (they decide the cached prefix): warm must equal turn.
SYSTEM_PROMPT_LINES = {"uid", "manner", "persona"}
# Lines the sidecar strips without touching the prompt text (transport / harness markers).
TRANSPORT_ONLY_LINES = {"replay", "spec"}

_LINE = re.compile(r"^ zoe-([a-z]+):[^\n]*\n")


def _leading(wire: str) -> "list[tuple[str, str]]":
    out, rest = [], wire
    while True:
        m = _LINE.match(rest)
        if not m:
            return out
        out.append((m.group(1), m.group(0)))
        rest = rest[len(m.group(0)):]


def _prompt_prefix(wire: str) -> "list[tuple[str, str]]":
    """The leading envelope lines that can reach the system prompt, in wire order."""
    return [(n, ln) for n, ln in _leading(wire) if n not in TRANSPORT_ONLY_LINES]


def assert_prefix_identical(warm: str, turn: str) -> None:
    w, t = _prompt_prefix(warm), _prompt_prefix(turn)
    assert [n for n, _ in w] == [n for n, _ in t], f"warm/turn envelope lines differ: {w!r} vs {t!r}"
    assert w == t, f"warm/turn envelope bytes differ: {w!r} vs {t!r}"
    unknown = {n for n, _ in t} - SYSTEM_PROMPT_LINES
    assert not unknown, f"unclassified envelope line(s) {unknown}: add to SYSTEM_PROMPT_LINES/TRANSPORT_ONLY_LINES"


@pytest.fixture
def prod(monkeypatch):
    import manner_block

    monkeypatch.setattr(manner_block, "enabled", lambda: True)

    async def _eligible(uid, db=None):
        return uid == "zed_uid"

    monkeypatch.setattr(manner_block, "eligible", _eligible)

    async def _persona(uid):
        return "PERSONA-FOR-" + uid if uid == "zed_uid" else ""

    async def _card(uid):
        zfc._CARD_SEEN[uid] = "cardv1"
        return "cardv1"

    monkeypatch.setattr(zfc, "_CARD_SEEN", {"zed_uid": "cardv1"})
    monkeypatch.setattr(zfc, "_persona_context_block", _persona)
    monkeypatch.setattr(zfc, "_card_version", _card)
    monkeypatch.setattr(zfc, "_LAST_TURN", {})
    monkeypatch.setattr(zfc, "_SESSION_UID", {})
    monkeypatch.setenv("ZOE_SEAM_CONTINUITY_INJECT", "0")
    monkeypatch.setenv("ZOE_IDENTITY_BLOCK", "0")
    for k in ("ZOE_FLUE_WIRE", "ZOE_FLUE_STREAM_ENABLED", "ZOE_SEAM_RECALL_INJECT", "ZOE_VERIFY_ON_CHALLENGE",
              "ZOE_TRIVIA_HEDGE"):
        monkeypatch.delenv(k, raising=False)

    def turn(message, uid):
        cap = []

        async def fake_wire2(session_id, payload, **kw):
            cap.append(json.loads(payload)["body"])
            yield "ok"

        monkeypatch.setattr(zfc, "_run_turn_aggregated_wire2", fake_wire2)

        async def go():
            return [c async for c in zfc.run_flue_brain_streaming(message, "voice-panel-t-1", uid)]

        asyncio.run(go())
        return cap[0]

    return turn


def _warm(member: str) -> str:
    return asyncio.run(zfc._kv_warm_outbound(member))[0]


CASES = [
    ("member", "zed_uid", ENGLISH),
    ("guest", "guest", ENGLISH),
    ("non_english_member", "zed_uid", SPANISH),
]


@pytest.mark.parametrize("name,uid,words", CASES, ids=[c[0] for c in CASES])
def test_warm_and_first_turn_share_the_system_prompt_prefix(prod, name, uid, words):
    turn = prod(words, uid)
    if uid == "zed_uid":
        assert {"manner", "persona", "uid"} <= {n for n, _ in _prompt_prefix(turn)}, "fixture must exercise every block"
    assert_prefix_identical(_warm(uid), turn)
    assert zfc.prefix_key(_warm(uid), "cardv1") == zfc.prefix_key(turn, "cardv1")


# `zoe-<name>:` tokens that are NOT prompt envelope lines (a render mark and a request header).
NOT_ENVELOPE_TOKENS = {"harness-render", "abort-on-cancel"}
_TOKEN = re.compile(r"zoe-([a-z][a-z-]*):")
SIDECAR = Path(__file__).resolve().parents[3] / "labs" / "flue-zoe-brain-2x" / "src"


def _tokens(text: str) -> "set[str]":
    return set(_TOKEN.findall(text)) - NOT_ENVELOPE_TOKENS


def test_generic_every_python_envelope_token_is_classified():
    """ANY `zoe-<name>:` literal in the client source (constant, f-string, inline, any quoting or wrapper name) must
    be classified: a new envelope line fails here until the warm renders it and it is listed in SYSTEM_PROMPT_LINES."""
    src = (Path(zfc.__file__)).read_text(encoding="utf-8")
    assert _tokens(src) == SYSTEM_PROMPT_LINES | TRANSPORT_ONLY_LINES, (
        f"unclassified envelope token(s): {_tokens(src) ^ (SYSTEM_PROMPT_LINES | TRANSPORT_ONLY_LINES)} -- if it "
        "reaches the system prompt, _kv_warm_outbound must render it for the likely speaker, then list it above")


def test_generic_every_sidecar_envelope_token_is_classified():
    """Same for the sidecar's parsers, whatever the quoting/concatenation. The directory is load-bearing: absent = red."""
    assert SIDECAR.is_dir(), "labs/flue-zoe-brain-2x/src missing: the sidecar side of the lock cannot be checked"
    found: "set[str]" = set()
    for f in SIDECAR.rglob("*.ts"):
        found |= _tokens(f.read_text(encoding="utf-8"))
    assert found == SYSTEM_PROMPT_LINES | TRANSPORT_ONLY_LINES, f"sidecar envelope tokens changed: {found}"


def test_sidecar_prompt_construction_seam_is_pinned():
    """The per-member inputs of the sidecar's system prompt are exactly (persona, manner, user-model suffix); a NEW
    per-member input to `applyPolicies` (a parameter or another with*Block) is not seen by the client-wire comparison,
    so it fails here until the warm provably reproduces it."""
    ts = (SIDECAR / "providers" / "capped-completions.ts").read_text(encoding="utf-8")
    m = re.search(r"export function applyPolicies\(([^)]*)\)", ts)
    assert m, "applyPolicies signature moved: re-pin this test"
    params = [p.split(":")[0].split("=")[0].strip().rstrip("?") for p in m.group(1).split(",") if p.strip()]
    assert params == ["context", "userModelSuffix", "turnHarness"], params
    body = ts[m.end():ts.index("\n}\n", m.end())]
    assert sorted(set(re.findall(r"\bwith(\w+Block)\(", body))) == ["MannerBlock", "PersonaBlock", "UserModelBlock"]


def test_generic_every_wrap_provider_is_known():
    """Every ``_wrap_message_with_*`` provider in the client is a classified one."""
    wrappers = {k[len("_wrap_message_with_"):] for k in vars(zfc) if k.startswith("_wrap_message_with_")}
    assert wrappers == {"identity", "persona", "manner", "replay", "speculative_turn"}, (
        f"new message wrapper(s) {wrappers}: make _kv_warm_outbound render it, then extend this set")


def test_negative_control_turn_only_member_block_goes_red(prod, monkeypatch):
    """A per-member line added only on the turn path (what shipped three times) must fail the invariant."""
    orig = zfc._wrap_message_with_persona

    def persona_plus_flavor(message, block):
        out = orig(message, block)
        return f" zoe-flavor:{json.dumps('per-member')}\n{out}" if block else out

    monkeypatch.setattr(zfc, "_wrap_message_with_persona", persona_plus_flavor)
    turn = prod(ENGLISH, "zed_uid")
    assert "zoe-flavor" in turn
    with pytest.raises(AssertionError):
        assert_prefix_identical(_warm("zed_uid"), turn)


def test_negative_control_stale_manner_memo_goes_red(prod):
    """The warm that ignores the member's last real turn (the 2026-10-27 regression) renders English for a Spanish turn."""
    turn = prod(SPANISH, "zed_uid")
    zfc._LAST_TURN.clear()
    with pytest.raises(AssertionError):
        assert_prefix_identical(_warm("zed_uid"), turn)


def test_negative_control_the_scans_see_an_inline_unnamed_provider():
    """The scans are literal-based: an inline f-string line in a function with any name still changes the token set."""
    src = Path(zfc.__file__).read_text(encoding="utf-8") + '\nasync def _sneaky(m, v):\n    return f" zoe-flavor:{v}\\n{m}"\n'
    assert _tokens(src) == SYSTEM_PROMPT_LINES | TRANSPORT_ONLY_LINES | {"flavor"}
    ts = (SIDECAR / "providers" / "capped-completions.ts").read_text(encoding="utf-8")
    mutated = ts.replace("userModelSuffix = '',", "userModelSuffix = '', moodSuffix = '',")
    assert mutated != ts and "moodSuffix" in re.search(r"export function applyPolicies\(([^)]*)\)", mutated).group(1)
