"""Service log lines never carry fact text, what someone said, or a person's name (privacy sweep 2026-11-14).

The service log sits outside the forget cascade and rotates by size, so a fact a person later forgets would live on in
it. Every site this sweep touched now logs a stable marker, ids, and ``log_redaction.utterance_tag`` (``len=N utt=<12
hex>``) in place of the words.

Two layers:
- behaviour (caplog at DEBUG, planted synthetic name + fact): ``expert_dispatch._ingest_or_supersede`` on its skip,
  supersede and dropped paths (``MEMORY_DEDUP_SKIP`` / ``MEMORY_SUPERSEDE`` / ``MEMORY_STORE_DROPPED``),
  ``stt_snap.log_line`` and ``device_confirm.log_line`` (member lines).
- a static pin over every other touched site: no logger call in those files passes a raw text/name variable (bare, or
  sliced) as an argument. Negative control: the same scanner flags the pre-fix form.
"""
from __future__ import annotations

import ast
import asyncio
import logging
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import log_redaction  # noqa: E402

pytestmark = pytest.mark.ci_safe

NAME = "Quillon Marsh"                                # synthetic
FACT = f"{NAME} is allergic to tangerines"           # synthetic
FACT_TAG = log_redaction.utterance_tag(FACT)


def _leaks(caplog) -> list[str]:
    msgs = [r.getMessage() for r in caplog.records]
    return [m for m in msgs if "Quillon" in m or "tangerine" in m]


# ── behaviour: expert_dispatch ───────────────────────────────────────────────

class _Metric:
    def labels(self, **_):
        return self

    def inc(self, *_):
        return None


class _Ref:
    def __init__(self, id_):
        self.id = id_


class _Svc:
    def __init__(self, ref):
        self._ref = ref

    async def ingest(self, *_a, **_k):
        return self._ref

    async def supersede_by(self, *_a, **_k):
        return True


def _fakes(monkeypatch, action, match_id, *, pii_reject=False):
    mq = types.ModuleType("memory_quality")

    async def reconcile_for_ingest(svc, text, user_id):
        return action, match_id
    mq.reconcile_for_ingest = reconcile_for_ingest
    mm = types.ModuleType("memory_metrics")
    mm.memory_dedup_skip_count = _Metric()
    mm.memory_supersede_count = _Metric()
    ma = types.ModuleType("memory_authority")
    ma.is_candidate = lambda ref: False
    ms = types.ModuleType("memory_service")
    ms.scrub_pii = lambda text: (text, pii_reject)
    for mod in (mq, mm, ma, ms):
        monkeypatch.setitem(sys.modules, mod.__name__, mod)


def _ingest(svc):
    import expert_dispatch
    return asyncio.run(expert_dispatch._ingest_or_supersede(
        svc, FACT, user_id="user-test-1", source="test", session_id="sess-1", user_turn_id=None,
        memory_type="fact", confidence=0.9, tags=[]))


@pytest.mark.parametrize("action,match_id,ref,pii,marker,outcome", [
    ("skip", "row-old", _Ref("row-new"), False, "MEMORY_DEDUP_SKIP source=test kept=row-old cand=", "skip"),
    ("update", "row-old", _Ref("row-new"), False, "MEMORY_SUPERSEDE source=test old=row-old new=", "stored"),
    ("add", None, None, True, "MEMORY_STORE_DROPPED source=test text=", "dropped"),
])
def test_expert_dispatch_memory_lines_carry_a_tag_not_the_fact(monkeypatch, caplog, action, match_id, ref, pii,
                                                                marker, outcome):
    _fakes(monkeypatch, action, match_id, pii_reject=pii)
    with caplog.at_level(logging.DEBUG):
        assert _ingest(_Svc(ref)) == outcome
    lines = [r.getMessage() for r in caplog.records if marker in r.getMessage()]
    assert lines == [marker + FACT_TAG]
    assert _leaks(caplog) == []


# ── behaviour: voice-path member lines ───────────────────────────────────────

def test_stt_snap_member_line_carries_a_tag_not_the_words(caplog):
    import stt_snap
    said = f"call {NAME} about the tangerines"
    s = types.SimpleNamespace(heard=said, text=said, from_hash="0123456789ab", intent="sky:test", safety="act",
                              cost=0.5, position=1)
    with caplog.at_level(logging.DEBUG):
        stt_snap.log_line(s, "acted", guest=False)
    (line,) = [r.getMessage() for r in caplog.records if "STT_SNAP" in r.getMessage()]
    assert "from=0123456789ab" in line and ("as_" + log_redaction.utterance_tag(said)) in line
    assert _leaks(caplog) == []


def test_device_confirm_member_line_carries_a_tag_not_the_words(caplog):
    import device_confirm
    said = f"turn on {NAME}'s tangerine lamp"
    ask = device_confirm.Ask(heard=said, polarity="on", question="Light on?", command="turn on the light")
    with caplog.at_level(logging.DEBUG):
        device_confirm.log_line(ask, "asked", guest=False)
    (line,) = [r.getMessage() for r in caplog.records if "DEVICE_CONFIRM" in r.getMessage()]
    assert line.endswith(log_redaction.utterance_tag(said)) and "outcome=asked" in line
    assert _leaks(caplog) == []


# ── static pin: every other touched site ─────────────────────────────────────

#: file -> variables that hold fact / turn / name text in that file. Passing one bare or sliced to a logger call leaks
#: it; wrapping it in a reducing call (the tag, len, type) is fine.
PINNED = {
    "expert_dispatch.py": {"text"},
    "memory_digest.py": {"fact", "text", "synthesis_text", "fact_text"},
    "memory_extractor.py": {"name", "text"},
    "person_extractor.py": {"name", "name_a", "name_b", "text"},
    "person_extractor_llm.py": {"name", "value"},
    "nlu_extractor.py": {"text"},
    "intent_classifier_llm.py": {"text"},
    "evolution_notice.py": {"message", "normalized_message"},
    "pending_suggestions.py": {"name"},
    "zoe_agent.py": {"raw_streaming_args", "tool_result", "tool_args"},   # ``raw`` is also a URL there (kept)
    "zoe_flue_client.py": {"line"},   # SEAM_CONTINUITY_DEBUG reply= is harness-ids only (test_/demo_), kept
    "correction_marker.py": {"user_message", "words", "content", "text", "texts", "found", "keys", "out"},   # PENDING_CORRECTION: ids + counts only
    "routers/ui_actions.py": {"result_text"},
    "routers/voice_tts.py": {"_skybridge_reply"},
    "stt_snap.py": set(),
    "device_confirm.py": set(),
}
SAFE_CALLS = {"_utag", "_utt_tag", "utterance_tag", "kind_refusal_tag", "len", "type", "bool"}   # never ``sorted``/``join``: they keep every string
LOG_METHODS = {"debug", "info", "warning", "error", "critical", "exception", "log"}


def _raw(node: ast.AST, raw: set[str]) -> list[str]:
    if isinstance(node, ast.Call):
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
        if name in SAFE_CALLS:
            return []
    if isinstance(node, ast.Name):
        return [node.id] if node.id in raw else []
    if isinstance(node, ast.Attribute) and node.attr in raw:   # ``c.text``
        return [node.attr]
    out: list[str] = []
    for child in ast.iter_child_nodes(node):
        out += _raw(child, raw)
    return out


def leaking_calls(source: str, raw: set[str]) -> list[int]:
    bad = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LOG_METHODS):
            continue
        owner = node.func.value
        owner_name = owner.id if isinstance(owner, ast.Name) else getattr(owner, "attr", "")
        if "log" not in owner_name.lower():
            continue
        if any(_raw(a, raw) for a in node.args[1:]) or any(_raw(k.value, raw) for k in node.keywords):
            bad.append(node.lineno)
    return bad


@pytest.mark.parametrize("rel", sorted(PINNED))
def test_no_logger_call_passes_raw_text(rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    assert leaking_calls(src, PINNED[rel]) == [], f"{rel}: a logger call passes fact/turn/name text"


@pytest.mark.parametrize("rel,old", [
    ("expert_dispatch.py", 'MEMORY_DEDUP_SKIP source=%s kept=%s cand=%r'),
    ("expert_dispatch.py", 'MEMORY_SUPERSEDE source=%s old=%s new=%r'),
    ("expert_dispatch.py", 'MEMORY_STORE_DROPPED source=%s text=%r'),
    ("stt_snap.py", 'heard={snap.heard'),
    ("device_confirm.py", 'heard={ask.heard'),
    ("routers/voice_tts.py", 'stream=%s reply=%r'),
    ("zoe_agent.py", 'args parse error, raw=%r'),
    ("zoe_agent.py", 'args=%s",\n                iteration, tool_name, json.dumps(tool_args)[:120]'),
])
def test_old_leaking_format_is_gone(rel, old):
    assert old not in (ROOT / rel).read_text(encoding="utf-8")


def test_scanner_negative_control_flags_the_pre_fix_forms():
    pre_fix = (
        'logger.info("MEMORY_DEDUP_SKIP source=%s kept=%s cand=%r", source, match_id, text[:80])\n'
        'logger.debug("apply_person_fact failed for %r: %s", name, exc)\n'
        'logger.info("memory_extractor: superseded %s with correction %r", target_id, c.text[:60])\n'
        'logger.info("x %s", f"{text}")\n'
    )
    assert leaking_calls(pre_fix, {"text", "name"}) == [1, 2, 3, 4]
    post_fix = 'logger.info("MEMORY_DEDUP_SKIP source=%s kept=%s cand=%s", source, match_id, _utag(text or ""))\n'
    assert leaking_calls(post_fix, {"text"}) == []


def test_tool_arg_keys_are_counted_never_logged():
    """Tool-argument KEYS are model-produced and can carry a name or a fact: both zoe_agent tool lines log a count, and the
    scanner flags a line that joins or sorts them (review of PR 2455)."""
    src = (Path(__file__).resolve().parents[1] / "zoe_agent.py").read_text(encoding="utf-8")
    assert "arg_keys" not in src
    assert src.count("n_args=%d args=%s") == 2 and src.count("len(tool_args or {}), _utag(json.dumps(tool_args))") == 2
    planted = 'logger.info("t arg_keys=%s", ",".join(sorted(tool_args or {})))'
    call = ast.parse(planted).body[0].value
    assert any(_raw(a, {"tool_args"}) for a in call.args), "the scanner must see keys passed through sorted()/join()"


def test_kind_refusal_tag_is_a_safe_reducer():
    """``kind_refusal_tag`` is in SAFE_CALLS because it returns a code, kind labels and a hash: never the fact or a name."""
    from memory_quality import kind_refusal_tag

    tag = kind_refusal_tag("Quillon is a cat")
    assert "Quillon" not in tag and "cat" not in tag.replace("cand_kind", "")
    assert tag.startswith("KIND_FLIP_REFUSED code=kind_flip") and "fact=" in tag
    src = (Path(__file__).resolve().parents[1] / "person_extractor.py").read_text(encoding="utf-8")
    assert src.count('logger.info("person_extractor: %s", kind_refusal_tag(text))') == 2
