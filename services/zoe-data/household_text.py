"""Where household WORDS can live on this box: the household-text inventory (ROADMAP A5-A7, 2026-11-14).

``forget_reach.py`` is the per-member inventory (every table with a member column, every sink the service names) and says what
each forget path does about it. This module is the narrower, wider-reaching question the owner asks: *where can something a
person SAID or WROTE end up?* It is DATA, stdlib only, no runtime caller:

  * every ``forget_reach`` store that holds words or text derived from them (``WORDS`` / ``DERIVED``) plus the two AUDIO stores
    (recorded speech is words) appears here, with its location as a PATTERN (never a real value), the kind of text, its
    retention and its owner. Its two reach columns are READ from ``forget_reach`` (never restated, so they cannot drift):
      - ``forget``        the per-subject forget ("forget Dana": ``memory_forget_entity`` + the cascade);
      - ``delete_my_data`` the member erase (admin ``/api/users/{id}/forget`` -> ``MemoryService.delete_user``).
  * ``EXTRA`` adds the stores ``forget_reach`` does not hold because they are not keyed by a member and are not a ``*.jsonl``
    sink: opt-in capture directories, operator tool outputs, the off-box stage, the retired agents' home directories.
  * ``WRITE_SITES`` is the REVIEWED list of code that writes a file from a function handling text-like values; every one maps
    to a store key here, or to a reason in ``NOT_HOUSEHOLD``. ``tests/test_household_text_inventory.py`` scans the code and
    FAILS on a write site that is in neither (the detector: see ``docs/knowledge/household-text-inventory.md`` for what it
    can and cannot see).

Regenerate the doc table: ``python3 services/zoe-data/household_text.py --write docs/knowledge/household-text-inventory.md``.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import forget_reach as fr

KEPT = "no age limit: kept until deleted"
AUDIO_KEYS = ("voice_sample_corpus", "omi_audio_files")      # BIOMETRIC in forget_reach, but recorded speech IS words


@dataclass(frozen=True)
class TextStore:
    key: str
    location: str                    # a PATTERN: ``pg:<table>``, ``~/.zoe/x/<night>.jsonl`` - never a real value
    text: str                        # what kind of household text it holds
    retention: str
    owner: str                       # the module (or tool family) that writes it
    status: str = ""                 # EXTRA only: reached | gap | out_of_scope (forget_reach stores take theirs from there)
    forget: str = ""                 # EXTRA only: the per-subject forget's reach, "-" when none
    delete_my_data: str = ""         # EXTRA only: the member erase's reach, "-" when none
    why: str = ""                    # EXTRA only: one line


def _pg(key: str, text: str, owner: str, retention: str = KEPT) -> TextStore:
    return TextStore(key, f"pg:{key}", text, retention, owner)


# -- stores forget_reach already holds: location, text, retention, owner (reach and status come from forget_reach) -------------
FROM_FORGET_REACH: tuple[TextStore, ...] = (
    _pg("exact_turns", "verbatim turns (exact words)", "exact_words.py"),
    _pg("night_observations", "night-mind observations about a member's day", "night_store.py"),
    _pg("night_threads", "night-mind threads (summaries of what was said)", "night_store.py"),
    _pg("now_cards", "the member's 'now' card text", "now_card.py"),
    _pg("struggle_events", "a failed turn's classes and, for the owner, its words", "struggle_detector.py"),
    _pg("struggle_proposal_events", "proposals derived from failed turns", "struggle_proposals.py"),
    _pg("turn_outcomes", "failure capture of a turn (route, outcome, words for review)", "turn_outcomes.py"),
    _pg("restraint_mutes", "topics the member asked Zoe not to raise", "restraint.py"),
    _pg("restraint_classes", "sensitivity classes of what was said", "restraint.py"),
    _pg("reply_sources", "which memory a reply drew on (ids + snippets)", "reply_ledger.py"),
    _pg("commitments", "promises made in conversation", "commitments.py"),
    _pg("omi_recordings", "pendant recording rows and their transcript", "omi_recordings.py"),
    _pg("omi_segments", "pendant transcript segments (who said what)", "omi_voices.py"),
    _pg("omi_check_items", "the morning transcript check's queued lines", "transcript_check.py"),
    _pg("omi_day_reviews", "the pendant day review (meetings, lists, reminders)", "omi_day_review.py"),
    _pg("omi_day_candidates", "day-review candidates (words lifted from the day)", "omi_day_review.py"),
    _pg("conversation_notes", "short notes about past conversations", "conversation_notes.py"),
    _pg("contact_fields", "contact details (names, addresses, notes)", "contacts_store.py"),
    _pg("source_contacts", "synced contact cards", "contacts_store.py"),
    _pg("contact_merge_reviews", "contact merge proposals", "contacts_store.py"),
    _pg("named_guest_turns", "what a named guest said", "named_guest_memory.py"),
    _pg("user_portraits", "the member portrait (derived prose)", "user_portrait.py"),
    _pg("user_model_cards", "the user-model card (derived prose)", "user_model_card.py"),
    _pg("open_loops", "unfinished things the member mentioned", "memory_digest.py, omi_review_apply.py"),
    _pg("proactive_candidates", "things Zoe might raise (derived text)", "commitments.py, proactive/selector.py"),
    _pg("people", "people the household talks about (names, notes)", "contacts_store.py, pending_suggestions.py"),
    _pg("person_relationships", "who is related to whom", "people_graph.py"),
    _pg("event_attendees", "attendee names and emails", "calendar_service.py"),
    _pg("events", "calendar event titles, places, descriptions", "skybridge_service.py, pending_suggestions.py"),
    _pg("chat_sessions", "chat titles", "routers/chat.py, proactive/session_utils.py"),
    _pg("chat_messages", "the verbatim chat transcript (every channel)", "routers/chat.py, proactive/session_utils.py"),
    _pg("chat_feedback", "a corrected response", "conversation_feedback.py"),
    _pg("background_tasks", "a background task's text and result", "background_runner.py (read side; no live writer in zoe-data)"),
    _pg("pending_suggestions", "offer text (may name people)", "pending_suggestions.py, anticipation.py"),
    _pg("notes", "the member's notes", "intent_router.py, pending_suggestions.py"),
    _pg("journal_entries", "the member's journal", "intent_router.py, mcp_server.py"),
    _pg("lists", "list names", "list_service.py"),
    _pg("list_items", "list items (shopping and other lists)", "list_service.py"),
    _pg("people_field_values", "custom fields about a person", "routers/people.py"),
    _pg("chat_ag_ui_runs", "AG-UI event streams of a chat run (the reply text)", "routers/chat.py"),
    _pg("reminders", "reminder text", "reminder_service.py"),
    _pg("transactions", "transaction descriptions", "routers/transactions.py, mcp_server.py"),
    _pg("person_activities", "a person's activities", "person_extractor.py, routers/people.py"),
    _pg("person_bucket_list", "a person's bucket list", "person_extractor.py, routers/people.py"),
    _pg("person_gift_ideas", "gift ideas for a person", "person_extractor.py, routers/people.py"),
    _pg("calendars", "calendar names and descriptions", "skybridge_service.py, calendar_forget.py"),
    _pg("ambient_memory", "ambient captured text", "routers/voice_tts.py"),
    _pg("notifications", "notification text", "notifications_store.py (distress_handoff.py, omi_recordings.py, ...)"),
    _pg("proactive_deliveries", "delivery ledger (keys; text is in the candidate)", "proactive/ledger.py"),
    _pg("proactive_outcome_edges", "outcome edges (opaque ids)", "proactive/outcome_edges.py"),
    _pg("proactive_ledger_lines", "the spoken proactive line", "proactive/lines.py"),
    _pg("proactive_pending", "a queued proactive message", "proactive/session_utils.py"),
    _pg("proactive_scheduled", "a scheduled proactive message", "proactive/triggers/reminders.py"),
    _pg("voice_announcements", "an announcement message", "voice_announce.py", "until expires_at (short-lived), then swept"),
    _pg("member_register", "topics the member likes", "member_register.py"),
    _pg("alarms", "alarm labels", "alarms.py"),
    _pg("ui_actions", "UI action payloads (e.g. a typed list item)", "ui_orchestrator.py"),
    _pg("openclaw_approvals", "old OpenClaw request text (retired, no writer)", "none (retired; read by routers/chat.py)"),
    _pg("openclaw_run_state", "old OpenClaw request/response text (retired)", "routers/chat.py (retired lane)"),
    TextStore("palace_sqlite", "~/.mempalace/**/chroma.sqlite3", "the memory palace: every remembered fact", KEPT, "memory_service.py"),
    TextStore("chroma_collections", "chroma:mempalace_drawers, chroma:mempalace_audit", "the palace's collections (same store)", KEPT,
              "memory_service.py"),
    TextStore("router_shadow_log", "services/zoe-data/data/router_head_shadow.jsonl(.N)", "utterance text beside its hash",
              "text stripped after ZOE_TEXT_LOG_RETENTION_DAYS (30)", "semantic_router.py (local_text_logs.py erases)"),
    TextStore("intent_miss_log", "~/training/data/intent-misses.jsonl", "utterances the intent router missed (crudely masked)",
              "dropped after ZOE_TEXT_LOG_RETENTION_DAYS (30)", "intent_router.py (local_text_logs.py erases)"),
    TextStore("recall_gate_trace", "~/.zoe/recall-gate-trace/trace.jsonl", "the owner's opt-in trace: turns + row text", "7 days",
              "recall_gate_trace.py"),
    TextStore("production_feedback_log", "~/training/data/production-feedback.jsonl", "message + reply verbatim (opt-in only)",
              "none (not written unless ZOE_TRAINING_FEEDBACK_LOG=1)", "zoe_agent.py"),
    TextStore("voice_stt_log", "$ZOE_VOICE_STT_LOG (~/.zoe-voice/voice_stt.jsonl, .1)", "the Moonshine transcript of every utterance",
              "size rotation only (ZOE_VOICE_STT_LOG_MAX_BYTES, one .1 segment)", "routers/voice_tts.py"),
    TextStore("flue_session_log", "labs/flue-zoe-brain-2x/data/zoe-brain.db", "the Flue brain's own session event log", KEPT,
              "the Flue brain sidecar (TypeScript, outside this scan)"),
    TextStore("flue_session_store", "labs/flue-zoe-brain-2x/data/zoe-brain.db", "the Flue brain's session store (replies, STT text)",
              KEPT, "the Flue brain sidecar (TypeScript, outside this scan)"),
    TextStore("omi_repass_work", "~/.zoe/omi-repass/work/<id>.{manifest.json,out.jsonl}", "one night run's recognised words",
              "minutes (deleted when the run applies)", "omi_repass.py, scripts/night/jobs/omi_repass_worker.py"),
    TextStore("omi_repass_hyps", "~/.zoe/omi-repass/hyps/hyps-<night>.jsonl", "every engine's hypothesis per chunk", "7 days",
              "omi_repass_eval.py"),
    TextStore("router_retrain_texts", "$ZOE_ROUTER_RETRAIN_DIR/work/<stamp>/*.texts.jsonl", "failed turns' words for one retrain run",
              "one run", "scripts/maintenance/router_struggle_retrain.py"),
    TextStore("self_review_records", "~/.zoe/self-review/<night>.self-review.jsonl + .md", "the owner's flagged turns verbatim", KEPT,
              "self_review.py"),
    TextStore("night_extract_facts", "~/.zoe/night-extract/<night>.night-extract.jsonl", "facts proposed from the member's day", KEPT,
              "night_extract.py"),
    TextStore("self_improver_candidates", "~/.zoe/self-improver/candidate-cells.jsonl", "synthetic asks in the shape of a failure", KEPT,
              "self_improver (night)"),
    TextStore("app_logs", "~/.zoe-logs/zoe-data.*", "pre-2026-11-03 lines may hold utterances (now len + hash only)",
              "size rotation (50 MB x4)", "the service logger"),
    TextStore("pi_intent_evidence", "~/.zoe/data/ (pi-*.jsonl)", "intent-miss evidence and shadow labels (masked text)", KEPT,
              "pi_intent_evidence.py, pi_intent_shadow.py"),
    TextStore("backup_postgres", "~/.zoe-backups/postgres/*.sql.gz", "every Postgres row above, as a dump", "7 daily + 4 weekly (<= 35 days)",
              "scripts/maintenance/postgres-nightly-backup.sh"),
    TextStore("backup_palace_tar", "~/.zoe-backups/mempalace/*.tar.gz", "the palace, as a tarball", "7 daily", "scripts/maintenance/mempalace-nightly-backup.sh"),
    TextStore("backup_json_exports", "~/.zoe/memory-exports/*.json, palace-backups/", "palace JSON exports", "redacted by the forget ledger",
              "scripts/maintenance/export_memory_store.py"),
    TextStore("backup_adhoc", "~/.zoe/backups/<tool>-<ts>/", "one-off backups owner scripts make (raw text)", "14 days, enforced",
              "owner scripts (redact_router_shadow.py, ...)"),
    TextStore("voice_sample_corpus", "~/.zoe-voice-samples/*.wav", "recorded panel speech (audio of words)", "owner corpus: kept; guest clips 30 days",
              "routers/voice_tts.py (ZOE_VOICE_SAVE_AUDIO), voice_clips.py"),
    TextStore("omi_audio_files", "~/.zoe/omi-recordings/<user>/*", "pendant audio (audio of words)", "claim-time retention",
              "omi_recordings.py"),
)

# -- stores forget_reach does not hold (no member key, not a *.jsonl sink) ---------------------------------------------------------
EXTRA: tuple[TextStore, ...] = (
    TextStore("telegram_stt_capture", "$ZOE_TELEGRAM_STT_CAPTURE_DIR/tg-<stamp>-<pid>.{wav,txt}",
              "a Telegram voice note's audio and its transcript (opt-in; unset = nothing written)", "none", "routers/telegram_media.py",
              fr.GAP, "-", "-", "Opt-in phone-mic capture corpus; no member id, no retention, outside the cascade."),
    TextStore("operator_tool_outputs", "scratch dirs, ~/.zoe/voice-gate/, ~/.zoe/struggle-eval/, --out paths",
              "reports, labelling sheets and replays the owner's tools write (may quote transcripts and replies)",
              "none automatic; the owner's own files", "scripts/perf/*, scripts/maintenance/* operator tools, scripts/train/generate_gemma_training_data.py",
              fr.GAP, "-", "-", "Operator-run tools write where the owner points them; the forget cannot find them. Most refuse an "
              "in-repo path and write 0600 (each tool's own doc)."),
    TextStore("offbox_stage", "~/.cache/zoe-offbox/stage/<tier>/<hmac>.age",
              "age ciphertext of the household + secrets tiers (no plaintext; names are keyed HMACs)",
              "mirrors the on-box sources: a file deleted there is deleted here on the next backup", "scripts/maintenance/zoe_offbox.py",
              fr.REACHED, "via its sources (the next backup drops what they dropped)", "via its sources", ""),
    TextStore("offbox_snapshots", "the backup target (restic repo: the411 rest-server or a local directory)",
              "snapshots of the stage (ciphertext)", "household tier <= 35 days (RETENTION in zoe_offbox.py)", "scripts/maintenance/zoe_offbox.py",
              fr.GAP, "-", "-", "A snapshot cannot be rewritten; it ages out within 35 days, as the on-box dumps do. Restore => re-issue the forget."),
    TextStore("evolution_proposals", "pg:evolution_proposals, pg:evolution_proposal_history (+ the Multica issue a proposal opens)",
              "intent-gap proposals quote missed utterances verbatim (title, description, evidence examples)", KEPT,
              "evolution_notice.py, evolution_queue.py", fr.GAP, "-", "-",
              "The intent-miss log is reached by both forget paths, but evolution_notice copies its clusters (representative + up to 5 "
              "examples) into proposal rows and the board issue, which no member key or eraser reaches."),
    TextStore("retired_agent_stores", "~/.openclaw/, ~/.hermes/", "the retired agents' session stores (OpenClaw, Hermes)",
              "deleted 2026-10-18 (owner-authorised) bar ~/.hermes/.env and an OpenClaw workspace stub", "none (retired)",
              fr.OUT_OF_SCOPE, "-", "-", "Retired, their session stores deleted live 2026-10-18; no writer of household text remains in the "
              "repo (agent_sync.py writes generated self-docs there, not household words)."),
)

STORES: tuple[TextStore, ...] = FROM_FORGET_REACH + EXTRA
BY_KEY = {s.key: s for s in STORES}

# -- the detector's allowlist: reviewed write sites (``relpath::function``, fnmatch) -> a store key or a NOT_HOUSEHOLD reason ------
WRITE_SITES: dict[str, str] = {
    "services/zoe-data/routers/telegram_media.py::_maybe_capture": "telegram_stt_capture",
    "services/zoe-data/semantic_router.py::_append_shadow_line": "router_shadow_log",
    "services/zoe-data/zoe_agent.py::_log_feedback_triple": "production_feedback_log",
    "services/zoe-data/omi_repass.py::*": "omi_repass_work",
    "services/zoe-data/omi_repass_eval.py::*": "omi_repass_hyps",
    "scripts/night/jobs/omi_repass_worker.py::run": "omi_repass_work",
    "scripts/maintenance/zoe_restore_drill.py::restore": "backup_postgres",   # a plaintext copy of the newest dump in the drill's temp dir, deleted with it
    "scripts/perf/*::*": "operator_tool_outputs",
    "scripts/maintenance/stt_accuracy_report.py::*": "operator_tool_outputs",
    "scripts/maintenance/struggle_replay_eval.py::*": "operator_tool_outputs",
    "scripts/maintenance/capability_gap_backlog.py::*": "operator_tool_outputs",
    "scripts/maintenance/pi_*.py::*": "operator_tool_outputs",
    "scripts/maintenance/voice_corpus_strata.py::*": "operator_tool_outputs",
    "scripts/maintenance/voice_regression_probe.py::*": "operator_tool_outputs",
    "scripts/maintenance/zoe-night-mind.py::*": "operator_tool_outputs",
    "scripts/maintenance/zoe-omi-day-review.py::*": "operator_tool_outputs",
    "scripts/maintenance/chroma_migrate_rehearsal.py::*": "operator_tool_outputs",
    "scripts/maintenance/agent_spike_metrics.py::*": "operator_tool_outputs",
}
NOT_HOUSEHOLD: dict[str, str] = {
    "services/zoe-data/forget_reach.py::main": "writes the generated inventory doc",
    "services/zoe-data/household_text.py::main": "writes the generated inventory doc",
    "services/zoe-data/tts_waterfall.py::*": "a temporary WAV of Zoe's own reply for playback",
    "services/zoe-data/routers/telegram_media.py::telegram_*": "temporary audio files of one request, removed after it",
    "scripts/maintenance/ha_intents_corpus.py::*": "the public Home Assistant intents corpus",
    "scripts/maintenance/mtp_draft_vocab_trim.py::*": "token ids",
    "scripts/maintenance/zoe_cheap_pr_agent.py::*": "edits a repo file",
    "services/zoe-data/self_model.py::<module>": "the self-model snapshot (capability facts from code; --snapshot only)",
    "scripts/setup/zoe_voice_daemon.py::*": "in-memory or temporary WAVs (chimes, warm-up; deleted); recorded speech goes to the server",
    "scripts/utilities/*::*": "legacy 2025 one-off utilities: their own logs and benchmark results",
}


def classify(site: str) -> Optional[str]:
    """``site`` = ``relpath::function`` -> the store key, ``-`` for a reviewed non-household writer, None for an unreviewed one."""
    for pat, key in WRITE_SITES.items():
        if fnmatch.fnmatchcase(site, pat):
            return key
    for pat in NOT_HOUSEHOLD:
        if fnmatch.fnmatchcase(site, pat):
            return "-"
    return None


def reach(s: TextStore) -> tuple[str, str, str]:
    """``(status, forget, delete_my_data)``: from forget_reach for its stores, from the entry for ``EXTRA``."""
    f = fr.BY_KEY.get(s.key)
    if f is None:
        return s.status, s.forget or "-", s.delete_my_data or "-"
    return f.status, f"`{f.entity}`" if f.entity else "-", f"`{f.member}`" if f.member else "-"


def must_cover() -> set[str]:
    """forget_reach keys this inventory must hold: every store of words or derived text, and the audio of speech."""
    return {s.key for s in fr.STORES if s.holds in (fr.WORDS, fr.DERIVED)} | set(AUDIO_KEYS)


def counts() -> dict[str, int]:
    out = {"stores": len(STORES), "forget": 0, "delete_my_data": 0, "both": 0, "neither": 0}
    for s in STORES:
        _st, fo, de = reach(s)
        out["forget"] += fo != "-"
        out["delete_my_data"] += de != "-"
        out["both"] += fo != "-" and de != "-"
        out["neither"] += fo == "-" and de == "-"
    return out


BEGIN, END = "<!-- BEGIN GENERATED: household_text.py -->", "<!-- END GENERATED -->"


def render_markdown() -> str:
    c = counts()
    head = ("| store | location (pattern) | text | forget reaches | delete-my-data reaches | status | retention | owner |\n"
            "|---|---|---|---|---|---|---|---|")
    rows = []
    for s in STORES:
        st, fo, de = reach(s)
        rows.append(f"| `{s.key}` | `{s.location}` | {s.text} | {fo} | {de} | {st} | {s.retention} | {s.owner} |".replace("\n", " "))
    summary = (f"{c['stores']} stores: a per-subject forget reaches {c['forget']}, delete-my-data reaches {c['delete_my_data']}, "
               f"both {c['both']}, neither {c['neither']}.")
    return "\n".join([BEGIN, "", summary, "", head, *rows, "", END])


def main(argv: Optional[list[str]] = None) -> int:
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "--write":
        path = Path(args[1])
        text = path.read_text(encoding="utf-8")
        a, b = text.index(BEGIN), text.index(END) + len(END)
        path.write_text(text[:a] + render_markdown() + text[b:], encoding="utf-8")
        return 0
    print(render_markdown())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
