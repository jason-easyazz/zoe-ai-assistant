"""No log or print call anywhere in zoe-data (or scripts/maintenance) formats a person's words (audit row G4, 2026-11-16).

``test_log_privacy_sweep.py`` pins 17 files with hand-listed variable names. This is the service-wide floor: an ``ast`` scan of
every ``services/zoe-data/**/*.py`` (tests excluded: they are not the service's log) and ``scripts/maintenance/*.py``. A
``<...log...>.debug/info/warning/error/critical/exception/log(...)`` or ``print(...)`` call may not pass, in ANY argument (the format
string included, so f-strings count), ``%`` formatting, ``.format(...)``, a method call on it, or a keyword (``extra=`` too), a
variable whose name marks it as someone's words: ``CONTENT_WORDS`` below (derived from the 17-file sweep's per-file names, widened
to the general ones) or a name ending in ``CONTENT_SUFFIXES``.

What is NOT a leak, so the floor stays precise: a reducing call (``len``, ``type``, ``bool``, the ``log_redaction`` tags), a
comparison (``reply == X`` is a bool), the TEST of a conditional expression, and an attribute OF a word variable (``body.action``
is a closed field). ``.name`` is a program identifier (``Path.name``, ``intent.name``) unless its owner is a person-ish variable.

``BASELINE`` is a RATCHET of the hits on main when this landed: per file, the count may only go DOWN. A new hit fails; fixing one
fails until its count is lowered here. Many are not words at all (a file or tool name): reducing or renaming them clears them.
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[3]

CONTENT_WORDS = frozenset({
    "text", "fact", "fact_text", "transcript", "utterance", "utt", "query", "message", "content", "note", "title", "body", "item",
    "name", "words", "wording", "quote", "claim", "said", "heard", "spoken", "reply", "prompt", "person_name", "contact_name",
    "display_name", "synthesis_text", "normalized_message", "user_message", "result_text",
})   # not "moment"/"emotion": memory_digest's own sweep pins those; elsewhere they are closed codes (voice_enrollment)
CONTENT_SUFFIXES = ("_text", "_transcript", "_utterance", "_message", "_words", "_query")
PERSONISH = frozenset({"person", "contact", "member", "guest", "people", "p", "user", "speaker"})
SAFE_CALLS = frozenset({"_utag", "_utt_tag", "utterance_tag", "kind_refusal_tag", "exc_tag", "len", "type", "bool", "id", "hash",
                        "isinstance"})
LOG_METHODS = frozenset({"debug", "info", "warning", "warn", "error", "critical", "exception", "log"})
NON_DATA_KWARGS = frozenset({"exc_info", "stack_info", "stacklevel", "file", "flush", "end", "sep"})

# file -> hit count on main, 2026-11-16 (reason per file). May only go down.
BASELINE = {
    # services/zoe-data
    "services/zoe-data/async_subprocess.py": 2,          # env-var NAME of a bad timeout setting (not words)
    "services/zoe-data/chat_stream_protocol.py": 1,      # tool name on a card-build failure
    "services/zoe-data/device_confirm.py": 1,            # ``words`` is already the _utag hash suffix (behaviour-tested)
    "services/zoe-data/ma_ondemand.py": 1,               # stamp file name
    "services/zoe-data/main.py": 5,                      # Multica board issue titles (engineering board, not household)
    "services/zoe-data/mcp_server.py": 2,                # MCP tool name
    "services/zoe-data/memory_service.py": 1,            # background task name
    "services/zoe-data/middleware/logging.py": 1,        # log_with_context's own format argument (pass-through API)
    "services/zoe-data/mishear_clarify.py": 1,           # ``words=%d`` is a word COUNT
    "services/zoe-data/multica_autopilot_sync.py": 1,    # Multica issue title (engineering board)
    "services/zoe-data/multica_client.py": 1,            # evolution proposal title (engineering board)
    "services/zoe-data/music_service.py": 1,             # Music Assistant HTTP error body (may echo a search term)
    "services/zoe-data/now_card.py": 1,                  # reduced by est_tokens/version: counts and a hash (scanner is conservative)
    "services/zoe-data/panel_speaker_health.py": 1,      # alarm text built from panel id, model and state (no words)
    "services/zoe-data/routers/voice_tts.py": 1,         # context-stage name (history/memory/domain)
    "services/zoe-data/stt_snap.py": 1,                  # ``words`` is already the _utag hash suffix (behaviour-tested)
    "services/zoe-data/tool_text_guard.py": 1,           # regex match of a tool name
    "services/zoe-data/ytmusic_signin.py": 1,            # sign-in step name
    "services/zoe-data/zoe_agent.py": 2,                 # web-research product query and business name: REAL, fix by tagging
    "services/zoe-data/zoe_flue_client.py": 1,           # SEAM_CONTINUITY_DEBUG reply (harness ids only, kept by the 17-file sweep)
    # scripts/maintenance (operator CLIs: ``print`` is their output channel)
    "scripts/maintenance/agent_spike_metrics.py": 1,     # prints its own report
    "scripts/maintenance/chroma_migrate_rehearsal.py": 4,  # collection / check names
    "scripts/maintenance/curate_voice_corpus.py": 1,     # corpus file name and reason
    "scripts/maintenance/export_router_heads.py": 2,     # router head names
    "scripts/maintenance/fix_secret_perms.py": 2,        # a fixed note per path rule
    "scripts/maintenance/harness_quiet.py": 1,           # harness name
    "scripts/maintenance/multica_apply_triage_dispositions.py": 1,  # Multica issue identifier
    "scripts/maintenance/multica_finalize_board_repair.py": 1,      # Multica disposition note
    "scripts/maintenance/music_discovery_batch.py": 1,   # mood/discover search query: REAL, fix by tagging
    "scripts/maintenance/panel_screenshots.py": 1,       # screenshot file name
    "scripts/maintenance/pi_intent_lab_http_probe.py": 1,  # lab probe's fixed wake-ack text
    "scripts/maintenance/pi_intent_probe.py": 1,         # probe timing items
    "scripts/maintenance/pi_promotion_eval.py": 1,       # prints its own report
    "scripts/maintenance/requirements_drift_check.py": 1,  # package name
    "scripts/maintenance/restore_memories_from_export.py": 1,  # export file name in the plan render
    "scripts/maintenance/rotate_service_logs.py": 3,     # log file names and a with-text COUNT
    "scripts/maintenance/sync_multica_to_kanban.py": 1,  # webhook skip reason
    "scripts/maintenance/voice_regression_probe.py": 1,  # self-test fault note
    "scripts/maintenance/zoe_offbox.py": 2,              # alert text (paths/sizes) and an unsafe index path
}


def _is_word(name: str) -> bool:
    return name in CONTENT_WORDS or name.endswith(CONTENT_SUFFIXES)


def words_in(node: ast.AST) -> list[str]:
    """The word-variable names ``node`` carries into a log line (empty when it is reduced first)."""
    if isinstance(node, ast.Compare):
        return []
    if isinstance(node, ast.IfExp):
        return words_in(node.body) + words_in(node.orelse)
    if isinstance(node, ast.Name):
        return [node.id] if _is_word(node.id) else []
    if isinstance(node, ast.Attribute):
        if node.attr == "name":
            return ["name"] if isinstance(node.value, ast.Name) and node.value.id in PERSONISH else []
        return [node.attr] if _is_word(node.attr) else []
    if isinstance(node, ast.Call):
        fn = node.func
        if (fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else "") in SAFE_CALLS:
            return []
        out = words_in(fn.value) if isinstance(fn, ast.Attribute) else []   # ``text.strip()`` / ``"{}".format(text)``
        return out + [w for a in [*node.args, *(k.value for k in node.keywords)] for w in words_in(a)]
    return [w for child in ast.iter_child_nodes(node) for w in words_in(child)]


def leaks(source: str) -> list[tuple[int, list[str]]]:
    out = []
    for n in ast.walk(ast.parse(source)):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Attribute) and f.attr in LOG_METHODS:
            owner = f.value.id if isinstance(f.value, ast.Name) else getattr(f.value, "attr", "")
            if "log" not in owner.lower():
                continue
        elif not (isinstance(f, ast.Name) and f.id == "print"):
            continue
        found = [w for a in [*n.args, *(k.value for k in n.keywords if k.arg not in NON_DATA_KWARGS)] for w in words_in(a)]
        if found:
            out.append((n.lineno, found))
    return out


def scanned_files() -> list[Path]:
    svc = REPO / "services/zoe-data"
    files = [p for p in svc.rglob("*.py") if "tests" not in p.relative_to(svc).parts]
    return sorted(files + list((REPO / "scripts/maintenance").glob("*.py")))


def test_no_log_call_formats_content_words_anywhere():
    files = scanned_files()
    assert len(files) > 500, "the scan must cover the whole service"
    counts = Counter()
    for p in files:
        counts[str(p.relative_to(REPO))] += len(leaks(p.read_text(encoding="utf-8")))
    counts = +counts
    grew = {f: (n, BASELINE.get(f, 0)) for f, n in counts.items() if n > BASELINE.get(f, 0)}
    assert not grew, ("a log/print call formats a variable named like someone's words (use log_redaction.utterance_tag, len, or "
                      f"an id): file -> (now, allowed) {grew}")
    shrank = {f: (counts.get(f, 0), n) for f, n in BASELINE.items() if counts.get(f, 0) < n}
    assert not shrank, f"fewer hits than BASELINE allows - lower the ratchet (file -> (now, pinned)): {shrank}"


@pytest.mark.parametrize("line", [
    'logger.info("heard %s", text)',
    'logger.info(f"heard {text}")',
    'logger.info("heard %s" % text)',
    'logger.info("heard {}".format(text))',
    'log.warning("x %s", text.strip()[:40])',
    'logging.info("fact=%r", fact)',
    'logger.info("x", extra={"q": user_query})',
    'print(f"note: {note}")',
    'logger.debug("x %s", c.transcript)',
    'logger.info("who %s", person.name)',
])
def test_negative_control_a_module_that_logs_words_is_caught(line):
    assert leaks(line + "\n") != []


@pytest.mark.parametrize("line", [
    'logger.info("len=%d", len(text))',
    'logger.info("t %s", _utag(text))',
    'logger.info("private=%d", int(reply == PRIVATE))',
    'logger.info("outcome=%s", "hit" if text else "miss")',
    'logger.info("act=%s", body.action)',
    'logger.info("file %s", Path(p).name)',
    'logger.info("x", exc_info=True)',
    'cache.info(text)',
])
def test_reduced_or_non_log_forms_pass(line):
    assert leaks(line + "\n") == []
