"""A guest sentinel must never pose as a panel identity.

Live bug (2026-08-10): the operator was signed in on the touch panel, yet every
voice turn logged `bound=guest ... scope_user=guest` and the voice lane kept
forcing him to re-authenticate.

`_VOICE_SESSIONS[panel]["bound_user_id"]` is an in-process cache of "who is at
this panel". A turn taken while nobody was signed in stamped it with the string
`"guest"`, which is TRUTHY — so it

  * blocked its own refresh (`if not _bound_user: ...` never fired again),
  * survived every session rollover (the carry-forward is `if entry.get(...)`),
  * and out-ranked the real signed-in user in `identified or bound or recent`.

The panel login therefore became invisible to voice until an unrelated
speaker-ID turn happened to overwrite the slot. Absent identity must be None,
never a sentinel string.
"""
import pytest

from routers import voice_tts

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def _clean_sessions():
    voice_tts._VOICE_SESSIONS.clear()
    yield
    voice_tts._VOICE_SESSIONS.clear()


@pytest.mark.parametrize("sentinel", sorted(voice_tts._GUEST_SENTINEL_USERS))
def test_every_sentinel_normalises_to_none(sentinel):
    """No sentinel may survive as an identity — that is the whole bug class."""
    assert voice_tts._real_user_or_none(sentinel) is None


@pytest.mark.parametrize("value", [None, 123, object()])
def test_non_strings_are_not_identities(value):
    assert voice_tts._real_user_or_none(value) is None


def test_real_user_survives_and_is_trimmed():
    assert voice_tts._real_user_or_none("jason") == "jason"
    assert voice_tts._real_user_or_none("  jason  ") == "jason"
    # Whitespace-only is empty, i.e. the "" sentinel.
    assert voice_tts._real_user_or_none("   ") is None


def test_sentinel_binding_is_not_carried_across_session_rollover(monkeypatch):
    """The poisoned-cache half of the bug.

    A session minted while nobody was signed in must not hand "guest" to its
    successor, or the panel looks permanently bound to a guest.
    """
    panel = "zoe-touch-pi"
    # Force the TTL to have elapsed so the next call mints a NEW session.
    monkeypatch.setattr(voice_tts, "_VOICE_SESSION_TTL_S", 0)

    first = voice_tts._get_or_create_voice_session(panel)
    voice_tts._VOICE_SESSIONS[panel]["bound_user_id"] = "guest"

    second = voice_tts._get_or_create_voice_session(panel)

    assert second != first, "precondition: a new session must have been minted"
    assert voice_tts._VOICE_SESSIONS[panel].get("bound_user_id") is None, (
        "a guest sentinel was carried into the new session — it would keep "
        "shadowing the real signed-in user"
    )


def test_real_binding_is_still_carried_across_session_rollover(monkeypatch):
    """Negative control: the fix must not break legitimate carry-forward.

    If this passes when the sentinel test above also passes, the carry-forward
    is discriminating rather than simply disabled — the failure mode that would
    make an authenticated panel re-challenge on every idle rollover.
    """
    panel = "zoe-touch-pi"
    monkeypatch.setattr(voice_tts, "_VOICE_SESSION_TTL_S", 0)

    voice_tts._get_or_create_voice_session(panel)
    voice_tts._VOICE_SESSIONS[panel]["bound_user_id"] = "jason"

    voice_tts._get_or_create_voice_session(panel)

    assert voice_tts._VOICE_SESSIONS[panel].get("bound_user_id") == "jason"


def test_sentinel_never_outranks_a_real_user_in_the_scope_chain():
    """The identity `or`-chain that decides whether the PIN gate fires.

    `_scope_identity_user = real(identified) or bound or recent`. With a raw
    sentinel in the middle slot this resolved to "guest" and the real panel
    user was never reached.
    """
    identified, bound, recent = "voice-guest", "guest", "jason"

    naive = identified or bound or recent
    assert naive == "voice-guest", "precondition: the raw chain picks a sentinel"

    scope_identity = (
        voice_tts._real_user_or_none(identified)
        or voice_tts._real_user_or_none(bound)
        or voice_tts._real_user_or_none(recent)
    )
    assert scope_identity == "jason"


def test_every_bound_user_id_read_is_normalised():
    """Source guard: a helper-level test cannot see an unwrapped READ site.

    Cross-review found the gap — mutating just the `_bound_user` read in
    `voice_command` to skip normalisation reintroduces the live bug while every
    behavioural test above still passes, because they exercise the helper, not
    the call sites. There are only a handful of sites and they are the whole
    fix, so assert on them directly: every expression that pulls
    `bound_user_id` out of the session dict must pass through
    `_real_user_or_none`.
    """
    import ast
    import inspect

    src = inspect.getsource(voice_tts)
    tree = ast.parse(src)

    unwrapped: list[int] = []
    for node in ast.walk(tree):
        # Match `<something>.get("bound_user_id")` …
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "get"):
            continue
        if not (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "bound_user_id"
        ):
            continue
        # … and require an enclosing _real_user_or_none(...) call.
        wrapped = any(
            isinstance(outer, ast.Call)
            and isinstance(outer.func, ast.Name)
            and outer.func.id == "_real_user_or_none"
            and any(n is node for n in ast.walk(outer))
            for outer in ast.walk(tree)
            if isinstance(outer, ast.Call)
        )
        if not wrapped:
            unwrapped.append(node.lineno)

    assert not unwrapped, (
        "raw bound_user_id read(s) at voice_tts.py line(s) "
        f"{unwrapped} — a cached guest sentinel would shadow the signed-in "
        "panel user again"
    )


def test_absent_identity_is_falsy_so_the_pin_gate_still_fires():
    """The security direction of the same fix.

    `_has_scope_identity = bool(_scope_identity_user)`. Because `bool("guest")`
    is True, a guest used to SATISFY the `user_scoped` gates and walk through
    them. With no real user present the value must be None → gate fires.
    """
    scope_identity = (
        voice_tts._real_user_or_none(None)
        or voice_tts._real_user_or_none("guest")
        or voice_tts._real_user_or_none("guest")
    )
    assert scope_identity is None
    assert bool(scope_identity) is False
