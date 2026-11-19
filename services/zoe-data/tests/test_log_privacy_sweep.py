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

    async def _scrub_async(text, **_k):                                 # the async form expert_dispatch awaits
        return text, pii_reject
    ms.scrub_pii_async = _scrub_async
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


def test_memory_row_line_carries_a_tag_not_the_row_text_on_every_handler():
    """``memory_digest.log_row`` (the MEMORY_ROW line): proved on the LOG RECORD (message AND args), and through two
    handlers with different formatters (the app.log / stderr pair), so no handler can re-introduce the words."""
    import io
    import memory_digest
    row_text = f"{NAME} keeps a spare tangerine key"
    ref = types.SimpleNamespace(id="row-1", text=row_text, metadata={"authority_class": "user_stated_derived"})
    seen: list[logging.LogRecord] = []

    class _Cap(logging.Handler):
        def emit(self, record):
            seen.append(record)

    streams = [io.StringIO(), io.StringIO()]
    handlers = [_Cap(level=logging.DEBUG)]
    for st, fmt in zip(streams, ("%(message)s", "%(asctime)s %(name)s %(levelname)s %(message)r")):
        h = logging.StreamHandler(st)
        h.setFormatter(logging.Formatter(fmt))
        handlers.append(h)
    lg = logging.getLogger(memory_digest.__name__)
    old = lg.level
    lg.setLevel(logging.DEBUG)
    for h in handlers:
        lg.addHandler(h)
    try:
        memory_digest.log_row("turn_digest", "u-1", ref, "stored")
    finally:
        for h in handlers:
            lg.removeHandler(h)
        lg.setLevel(old)
    assert seen
    for r in seen:
        for part in (r.getMessage(), str(r.msg), repr(r.args)):
            assert "Quillon" not in part and "tangerine" not in part, part
    assert any("MEMORY_ROW " in r.getMessage() and log_redaction.utterance_tag(row_text) in r.getMessage() for r in seen)
    for st in streams:
        assert "Quillon" not in st.getvalue() and "tangerine" not in st.getvalue()
        assert log_redaction.utterance_tag(row_text) in st.getvalue()


def _claim_md(**over):
    claim = {"subj": "user", "pred": "residence", "obj": "Zorblax-tangerine-9137", "pol": "affirm", "mod": "asserted",
             "tense": "current", "quote": "I live at Zorblax-tangerine-9137"}
    claim.update(over)
    import json
    return {"claim": json.dumps(claim), "claim_lexical": "promote", "claim_structural": "anchor"}


def _claim_records(md):
    import memory_digest
    seen: list[logging.LogRecord] = []

    class _Cap(logging.Handler):
        def emit(self, record):
            seen.append(record)

    lg = logging.getLogger(memory_digest.__name__)
    old = lg.level
    h = _Cap(level=logging.DEBUG)
    lg.setLevel(logging.DEBUG)
    lg.addHandler(h)
    try:
        memory_digest.log_row("turn_digest", "u-1", types.SimpleNamespace(id="row-1", text="t", metadata=md), "stored")
    finally:
        lg.removeHandler(h)
        lg.setLevel(old)
    return [r for r in seen if r.getMessage().startswith("MEMORY_ROW_CLAIM")]


def test_memory_row_claim_line_never_carries_extractor_free_text():
    """Every field of the claim row is model JSON: pred (free text after ``kin:``), pol / mod / tense and both labels are
    clamped to their closed vocabularies, anything else logs as ``other``. Planted sentinel in ALL of them, checked on the
    record (message, msg, args)."""
    s = "Zorblax-tangerine-9137"
    md = _claim_md(pred="kin:" + s, pol=s, mod=s, tense=s)
    md["claim_lexical"] = s
    md["claim_structural"] = s
    (rec,) = _claim_records(md)
    for part in (rec.getMessage(), str(rec.msg), repr(rec.args)):
        assert "Zorblax" not in part and "tangerine" not in part, part
    assert rec.getMessage().endswith("claim=kin/other/other/other:other>other")
    (ok,) = _claim_records(_claim_md())
    assert ok.getMessage().endswith("claim=residence/affirm/asserted/current:promote>anchor")   # a closed value stays readable
    (free,) = _claim_records(_claim_md(pred=s))
    assert "Zorblax" not in free.getMessage() and "claim=other/" in free.getMessage()


def test_negative_control_unclamped_claim_label_would_leak():
    """The pre-fix label joined the raw claim fields: the same planted pred reaches the message."""
    md = _claim_md(pred="Zorblax-tangerine-9137")
    import json
    c = json.loads(md["claim"])
    old_shape = "%s/%s/%s/%s" % (c.get("pred"), c.get("pol"), c.get("mod"), c.get("tense"))
    assert "Zorblax" in old_shape


def test_no_exception_traceback_or_message_logged_in_memory_digest():
    """``exc_info=`` / ``logger.exception`` print the exception MESSAGE in the traceback (which can quote the stored text); a
    bare ``exc`` argument is ``str(exc)``. Neither may appear in a logger call of the digest module."""
    src = (ROOT / "memory_digest.py").read_text(encoding="utf-8")
    bad = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LOG_METHODS:
            owner = node.func.value
            if "log" not in (owner.id if isinstance(owner, ast.Name) else getattr(owner, "attr", "")).lower():
                continue
            if node.func.attr == "exception" or any(k.arg == "exc_info" for k in node.keywords):
                bad.append(node.lineno)
    assert bad == []


def test_negative_control_the_sweep_flags_a_bare_exception_argument():
    src = "import logging\nlogger = logging.getLogger('x')\ntry:\n    pass\nexcept Exception as exc:\n    logger.warning('f: %s', exc)\n"
    assert leaking_calls(src, {"exc"}) == [6]
    ok = src.replace("'f: %s', exc", "'f: %s', type(exc).__name__")
    assert leaking_calls(ok, {"exc"}) == []


class _Boom(RuntimeError):
    pass


PLANTED = "Zorblax-tangerine-9137 lives at the shed"


def _assert_type_only(caplog, expect="exc=_Boom"):
    assert caplog.records
    for r in caplog.records:
        for part in (r.getMessage(), str(r.msg), repr(r.args)):
            assert "Zorblax" not in part and "tangerine" not in part and "shed" not in part, part
        assert r.exc_info is None
    assert any(expect in r.getMessage() for r in caplog.records)


def test_a_failing_vector_store_call_logs_the_exception_type_not_its_message(monkeypatch, caplog):
    import memory_digest
    import memory_service

    class _Svc:
        def __getattr__(self, _name):
            async def boom(*_a, **_k):
                raise _Boom(PLANTED)
            return boom

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _Svc())
    caplog.set_level(logging.DEBUG)
    asyncio.run(memory_digest.run_weekly_consolidation("u-1"))
    _assert_type_only(caplog)
    assert any("consolidation: sweep failed" in r.getMessage() for r in caplog.records)


def test_a_failing_model_call_logs_the_exception_type_not_its_message(monkeypatch, caplog):
    import memory_digest

    class _Client:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            raise _Boom(PLANTED)

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(memory_digest.httpx, "AsyncClient", _Client)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(memory_digest.ExtractorError):
        asyncio.run(memory_digest._facts_call("transcript", 1.0))
    _assert_type_only(caplog)
    assert any("LLM call failed" in r.getMessage() for r in caplog.records)


def test_negative_control_the_old_exception_shape_leaks_the_message(caplog):
    caplog.set_level(logging.DEBUG)
    exc = _Boom(PLANTED)
    logging.getLogger("memory_digest").warning("consolidation: sweep failed user=%s: %s", "u-1", exc)
    assert any("Zorblax" in r.getMessage() for r in caplog.records)
    caplog.clear()
    try:
        raise exc
    except _Boom:
        logging.getLogger("memory_digest").error("failed", exc_info=True)
    assert any(r.exc_info for r in caplog.records)      # a traceback would print the message too


# ── static pin: every other touched site ─────────────────────────────────────

#: file -> variables that hold fact / turn / name text in that file. Passing one bare or sliced to a logger call leaks
#: it; wrapping it in a reducing call (the tag, len, type) is fine.
PINNED = {
    "expert_dispatch.py": {"text"},
    "memory_digest.py": {"fact", "text", "synthesis_text", "fact_text", "wording", "moment", "emotion", "tag", "pred", "obj", "subj",
                         "quote", "claim", "label", "item", "exc", "je", "e", "err"},
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
    "child_safety_floor.py": {"text", "s"},   # one class-code line; the words and the age never ride it
}
SAFE_CALLS = {"_utag", "_utt_tag", "utterance_tag", "kind_refusal_tag", "exc_tag", "len", "type", "bool"}   # never ``sorted``/``join``: they keep every string
LOG_METHODS = {"debug", "info", "warning", "error", "critical", "exception", "log"}


def _raw(node: ast.AST, raw: set[str]) -> list[str]:
    if isinstance(node, ast.Call):
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
        if name in SAFE_CALLS:
            return []
    if isinstance(node, ast.Attribute) and node.attr == "kind" and isinstance(node.value, ast.Name):
        return []   # ``ExtractorError.kind``: the underlying exception CLASS NAME (or ``NoJSONArray``), never its message
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


#: os_shadow.py: an ALLOWLIST, not a denylist. Every argument of every logger call there is one of these names, each mapped onto a
#: closed set (or a number) by ``os_shadow._finish`` before the line is written, or ``type(exc).__name__`` (a program identifier).
OS_SHADOW_LOG_FIELDS = {"verb", "source", "cls", "live", "kernel_state", "result", "reason", "hop_ms", "q_ms",
                        "decision", "ask_ms", "area",   # the door (``_door_log`` clamps each to its closed set first)
                        "code"}     # the forget path: ``type(exc).__name__`` or ``"queue_full"`` (pinned below)


def _os_shadow_log_args(src: str) -> list:
    out = []
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LOG_METHODS
                and getattr(node.func.value, "id", "") == "logger"):
            out += [(node.lineno, a) for a in node.args[1:]] + [(node.lineno, k.value) for k in node.keywords]
    return out


def _closed(arg) -> bool:
    if isinstance(arg, ast.Name):
        return arg.id in OS_SHADOW_LOG_FIELDS
    return (isinstance(arg, ast.Attribute) and arg.attr == "__name__" and isinstance(arg.value, ast.Call)
            and getattr(arg.value.func, "id", "") == "type")


def test_os_shadow_logs_only_closed_code_fields():
    bad = [ln for ln, a in _os_shadow_log_args((ROOT / "os_shadow.py").read_text("utf-8")) if not _closed(a)]
    assert bad == [], f"os_shadow.py lines {bad}: a logger argument that is not a closed-code field"
    planted = 'logger.info("x %s %s", item, list_name)\nlogger.info("y %s", str(exc))\nlogger.info("z %s", result)\n'
    assert [ln for ln, a in _os_shadow_log_args(planted) if not _closed(a)] == [1, 1, 2]


def test_os_shadow_maps_every_logged_field_onto_its_closed_set():
    import os_shadow

    src = (ROOT / "os_shadow.py").read_text("utf-8")
    for name, closed in (("verb", "VERBS"), ("source", "SOURCES"), ("cls", "CLASSES"), ("live", "LIVE"), ("kernel_state", "KERNEL_STATES"),
                         ("reason", "REASONS")):
        assert f"{name} if {name} in {closed}" in src or f"in {closed} else" in src, name
        assert isinstance(getattr(os_shadow, closed), (tuple, dict))


def test_os_shadow_forget_code_is_a_class_name_or_queue_full():
    tree = ast.parse((ROOT / "os_shadow.py").read_text("utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "code" for t in n.targets):
            v = n.value
            ok = (_closed(v) or (isinstance(v, ast.Constant) and v.value in ("", "queue_full"))
                  or (isinstance(v, ast.IfExp) and isinstance(v.body, ast.Constant) and _closed(v.orelse)))
            assert ok, n.lineno
