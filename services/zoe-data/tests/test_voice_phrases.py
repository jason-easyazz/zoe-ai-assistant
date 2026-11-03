"""One voice for every tier: ``voice_phrases.json`` + ``voice_phrases.py``.

Pins: every phrase id used in code exists; the variant pick is deterministic (sha256 of id + key, never random); every line
keeps the brain's manner (no exclamation, no "Please try again", contractions); the sentinel lines other code compares by
equality stay single and byte-equal where they are mirrored; the "brain did not answer" lines stay ERRORs for the struggle
detector and the replay verdict, and no other line trips the replay's CANT_DO classifier (said-vs-did must not move); the
scanned tier paths carry no new hard-coded seam line. Each checker has a negative control.
"""
from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

import pytest

import voice_phrases as vp

pytestmark = pytest.mark.ci_safe

ZD = Path(__file__).resolve().parents[1]
REPO = ZD.parents[1]

# ── the hard-coded-line scan ─────────────────────────────────────────────────
# The tier paths this PR moved onto the phrase file. A NEW literal here that reads like a seam line (apology, retry,
# re-ask, bare acknowledgement) must go in voice_phrases.json instead, or into ALLOWED with the reason it must stay.
SCANNED = (
    "fast_tiers.py", "expert_dispatch.py", "brain_dispatch.py", "zoe_flue_client.py",
    "routers/voice_tts.py", "routers/voice_livekit.py", "routers/chat.py",
)
_SEAM = re.compile(
    r"(?i)\b(?:sorry|try again|try that again|didn'?t catch|did not catch|went wrong|that failed|having trouble|"
    r"had trouble|trouble with that|trouble processing|couldn'?t reach|can'?t reach|say (?:that|it) again|missed that|"
    r"one moment)\b")
_BARE_ACK = re.compile(r"^(?:Done|Okay|OK|Got it|On it|All done|Sure)[.!]?$")   # case-sensitive: dict keys are lower case
ALLOWED = {
    ("routers/voice_tts.py", ", but I can't show it on the panel right now, so I haven't saved it. Try again in a moment."):
        "write outcome: names that nothing was saved (false-write-claim guard)",
    ("routers/voice_tts.py", "I couldn't add that just now. Want me to try again?"):
        "write outcome: names that the add did not happen and offers the retry",
    ("routers/voice_tts.py", "I couldn't add that to your calendar just now. Want me to try again?"):
        "write outcome: names the calendar write that did not happen",
    ("routers/voice_tts.py",
     "I could not open the authentication screen just now. Please open the touch login page and try again."):
        "auth hand-off: names the exact screen to open instead",
    ("zoe_flue_client.py", "i couldn't reach my memory right now."):
        "a MATCHER for the sidecar's recall_memory error result, never spoken from here",
    ("routers/chat.py", "I had trouble fetching panel status. Check `/api/panels` for details."):
        "operator panel-status answer naming the endpoint, not a conversational seam",
    ("routers/chat.py", "I couldn't reach Hermes right now. Please try again."):
        "legacy Hermes escalation lane (retiring); names the backend that failed",
}


def _docstring_ids(tree: ast.AST) -> set[int]:
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
            first = n.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                out.add(id(first.value))
    return out


def seam_literals(source: str) -> list[str]:
    """String literals in ``source`` that read like a user-facing seam line (docstrings and regex sources skipped)."""
    tree = ast.parse(source)
    docs = _docstring_ids(tree)
    found = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            s = n.value.strip()
            if not s or len(s) > 200 or "\\" in s or "(?" in s:
                continue
            if _SEAM.search(s) or _BARE_ACK.match(s):
                found.append(s)
    return found


def test_no_new_hard_coded_seam_lines_in_the_tier_paths():
    stray = []
    for rel in SCANNED:
        for lit in seam_literals((ZD / rel).read_text(encoding="utf-8")):
            if (rel, lit) not in ALLOWED:
                stray.append((rel, lit))
    assert not stray, f"move these into voice_phrases.json (or ALLOWED, with a reason): {stray}"


def test_allowlist_has_no_dead_entries():
    live = {(rel, lit) for rel in SCANNED for lit in seam_literals((ZD / rel).read_text(encoding="utf-8"))}
    assert set(ALLOWED) <= live, set(ALLOWED) - live


def test_scan_negative_control():
    src = 'def f():\n    """Sorry, docstrings are fine."""\n    a = "Sorry, I didn\'t catch that."\n    b = "Done!"\n' \
          '    c = {"ok": True}\n    d = r"(?:sorry\\s+)?i meant"\n'
    assert seam_literals(src) == ["Sorry, I didn't catch that.", "Done!"]


# ── ids used in code exist ───────────────────────────────────────────────────
_SAY_CALL = re.compile(r"""\b(?:voice_phrases|_vp)\.say\(\s*["']([a-z_]+)["']""")


def ids_used(source: str) -> set[str]:
    return set(_SAY_CALL.findall(source))


def test_every_phrase_id_used_in_code_exists():
    used: dict[str, str] = {}
    for path in ZD.rglob("*.py"):
        if "tests" in path.relative_to(ZD).parts:
            continue
        for pid in ids_used(path.read_text(encoding="utf-8", errors="replace")):
            used.setdefault(pid, str(path.relative_to(ZD)))
    assert used, "the scan found no say() calls: the regex is broken"
    missing = {pid: where for pid, where in used.items() if pid not in vp.ids()}
    assert not missing, missing


def test_every_phrase_in_the_file_is_used():
    used = set()
    for path in ZD.rglob("*.py"):
        if "tests" not in path.relative_to(ZD).parts:
            used |= ids_used(path.read_text(encoding="utf-8", errors="replace"))
    mirrored = {pid for pid in vp.ids() if vp.entry(pid).get("mirrors")}
    assert set(vp.ids()) - used - mirrored == set()


def test_id_check_negative_control():
    assert ids_used('x = voice_phrases.say("no_such_line", k)') == {"no_such_line"}
    assert "no_such_line" not in vp.ids()
    assert vp.say("no_such_line", "k") == vp.LAST_RESORT  # never raises on the speaking path


# ── deterministic variants ───────────────────────────────────────────────────

def test_variant_pick_is_sha256_of_id_and_key():
    n = len(vp.variants("reask"))
    for key in ("turn-1", "sess:42", "add milk"):
        want = int(hashlib.sha256(f"reask\x00{key}".encode()).hexdigest()[:8], 16) % n
        assert vp.say("reask", key) == vp.variants("reask")[want]
        assert vp.say("reask", key) == vp.say("reask", key)


def test_variants_spread_and_no_key_is_the_first():
    seen = {vp.say("reask", f"turn-{i}") for i in range(60)}
    assert seen == set(vp.variants("reask")), "a variant is never picked"
    assert vp.say("reask") == vp.variants("reask")[0]
    assert vp.say("done", None) == vp.variants("done")[0]


def test_params_fill_the_confirmation():
    assert vp.say("memory_saved", fact="your sister is Marisol") == "Got it — I'll remember your sister is Marisol."
    assert vp.say("memory_saved", fact="a {brace}") == "Got it — I'll remember a {brace}."


# ── the manner ───────────────────────────────────────────────────────────────
_UNCONTRACTED = re.compile(r"\b(?:I am|I did not|I could not|I can not|I cannot|do not|does not|it is|that is)\b")
_BANNED = ("please try again", "i'm sorry, i didn't catch that", "i did not catch", "great!", "of course!")


def manner_problems(text: str) -> list[str]:
    out = []
    low = text.lower()
    if "!" in text:
        out.append("exclamation")
    out += [b for b in _BANNED if b in low]
    if _UNCONTRACTED.search(text):
        out.append("uncontracted")
    if text.count("?") > 1:
        out.append("more than one question")
    if len(text) > 100:
        out.append("too long")
    return out


@pytest.mark.parametrize("pid", vp.ids())
def test_every_line_keeps_the_manner(pid):
    for text in vp.variants(pid):
        assert manner_problems(text) == [], (pid, text)
    assert manner_problems(vp.LAST_RESORT) == []


def test_manner_negative_control():
    assert "exclamation" in manner_problems("Done!")
    assert "please try again" in manner_problems("Sorry, something went wrong. Please try again.")
    assert "uncontracted" in manner_problems("I did not catch the list item.")


def test_reask_is_short_and_human():
    for text in vp.variants("reask"):
        assert len(text) <= 45 and "sorry, i didn't catch that" not in text.lower(), text


# ── sentinels and mirrors ────────────────────────────────────────────────────

def test_sentinels_have_exactly_one_variant():
    for pid in vp.ids():
        if vp.entry(pid).get("sentinel"):
            assert len(vp.variants(pid)) == 1, pid


def test_module_sentinels_are_the_file_lines():
    import zoe_flue_client

    assert zoe_flue_client._FALLBACK_TEXT == vp.variants("brain_unreachable")[0]
    src = (ZD / "routers" / "voice_tts.py").read_text(encoding="utf-8")
    assert '_FALLBACK_PHRASE = _vp.say("turn_failed")' in src


def test_daemon_mirrors_are_byte_equal():
    src = (REPO / "scripts" / "setup" / "zoe_voice_daemon.py").read_text(encoding="utf-8")
    for pid in vp.ids():
        for mirror in vp.entry(pid).get("mirrors") or ():
            name = mirror.split(":", 1)[1]
            m = re.search(rf'^{re.escape(name)}\s*=\s*"([^"\n]+)"', src, re.M)
            assert m, mirror
            assert m.group(1) == vp.variants(pid)[0], (mirror, m.group(1))


# ── classifiers: struggle detector + replay verdict ──────────────────────────

def test_brain_fallback_lines_are_struggle_errors():
    import struggle_signals as ss

    texts = vp.brain_fallback_texts()
    assert len(texts) >= 3
    for text in texts:
        assert ss.cant_do(text) == "error", text


def test_replay_verdicts_do_not_move():
    """said-vs-did: only the brain fallback is an ERROR; no other phrase may read as a capability gap (CANT_DO)."""
    import os

    cwd = os.getcwd()
    try:
        import replay_samples as rs     # chdirs at import; contained like test_replay_harness_hygiene
    finally:
        os.chdir(cwd)

    assert rs._classify("what's on my calendar", vp.say("brain_unreachable"), "brain") == "ERROR"
    fallbacks = set(vp.brain_fallback_texts())
    for pid in vp.ids():
        for text in vp.variants(pid):
            if text not in fallbacks:
                assert not rs._CANT_DO_RE.search(text.format(fact="x")), (pid, text)
