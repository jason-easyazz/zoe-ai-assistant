"""evolution_notice.py — Phase 6 of the nightly dreaming cycle.

NOTICE phase of the self-evolution loop:
1. Intent misses: cluster similar misses from last 7 days, propose patterns with ≥3 hits
2. LLM errors: detect agent tiers with >10% error rate in 24h
3. Frustration signals: picked up inline in chat.py, stored as proposals directly

All proposals go to the single evolution proposal queue (evolution_proposals, written ONLY through evolution_queue.py: class code,
risk tier 3, legal status moves enforced there). Deduplication prevents repeat entries.
"""
from __future__ import annotations

import asyncio
import json
import logging
from log_redaction import utterance_tag as _utag  # logs carry a tag, never fact/turn text
import os
import time
import uuid
from pathlib import Path

import evolution_queue

logger = logging.getLogger(__name__)

_MISS_PATH = Path.home() / "training" / "data" / "intent-misses.jsonl"
_LOOKBACK_DAYS = 7
_MIN_CLUSTER_SIZE = 3

# If > 0, proposals whose cluster size >= this value are auto-approved (skips human review).
# Low-risk pattern additions (e.g., 10+ identical misses) can be approved automatically.
# Set ZOE_AUTO_APPROVE_THRESHOLD=0 to require human review for all proposals.
_AUTO_APPROVE_THRESHOLD = int(os.environ.get("ZOE_AUTO_APPROVE_THRESHOLD", "0"))


#: Mirroring proposals onto the Multica engineering board is OPT-IN (default off): the board's dispatch has been paused since
#: 2026-08-04, so every mirrored issue was a ticket nobody would pick up (462 queue rows carry an issue id). The queue itself is the
#: owner's surface (``evolution_inbox.py``). Read at call time.
BOARD_MIRROR_ENV = "ZOE_EVOLUTION_BOARD_MIRROR"


def board_mirror_on() -> bool:
    from typed_env import env_bool

    return env_bool("ZOE_EVOLUTION_BOARD_MIRROR", False)


async def _mirror(db, prop_id: str, payload: dict) -> None:
    """Mirror a NEW proposal onto the board and keep its issue id - only with ``ZOE_EVOLUTION_BOARD_MIRROR`` on."""
    if not board_mirror_on():
        return
    from multica_client import sync_evolution_proposal_to_multica

    multica_id = await sync_evolution_proposal_to_multica(**payload)
    if multica_id:
        await db.execute("UPDATE evolution_proposals SET multica_issue_id=$1 WHERE id=$2", multica_id, prop_id)


def _load_recent_misses(days: int = _LOOKBACK_DAYS) -> list[str]:
    """Load intent miss texts from the last N days."""
    if not _MISS_PATH.exists():
        return []
    cutoff = time.time() - days * 86400
    misses = []
    try:
        for line in _MISS_PATH.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if row.get("ts", 0) >= cutoff:
                    misses.append(row.get("text", ""))
            except json.JSONDecodeError:
                pass
    except OSError:
        pass
    return [m for m in misses if m]


def _simple_cluster(texts: list[str]) -> list[tuple[str, list[str]]]:
    """Cluster texts by shared trigrams. Returns (representative, [members]) tuples."""
    clusters: list[list[str]] = []
    for text in texts:
        t_grams = _trigrams(text)
        best_idx = None
        best_sim = 0.0
        for i, cluster in enumerate(clusters):
            rep_grams = _trigrams(cluster[0])
            union = len(t_grams | rep_grams)
            if union == 0:
                continue
            sim = len(t_grams & rep_grams) / union
            if sim > best_sim and sim > 0.4:
                best_sim = sim
                best_idx = i
        if best_idx is not None:
            clusters[best_idx].append(text)
        else:
            clusters.append([text])

    return [(c[0], c) for c in clusters if len(c) >= _MIN_CLUSTER_SIZE]


async def _proposal_exists(title: str, prop_type: str, db) -> bool:
    """True when an open proposal with this type + title already exists; it is then marked SEEN again (``_seen_again``)."""
    rows = await db.fetch(
        """SELECT id FROM evolution_proposals
           WHERE type=$1 AND title=$2
           AND status NOT IN ('rejected','kept','reverted')""",
        prop_type, title,
    )
    if rows:
        await _seen_again(db, rows[0]["id"])
    return len(rows) > 0


async def _seen_again(db, pid: str, now: float | None = None) -> None:
    """A tracked problem RECURRED: record the latest occurrence as ``evidence_json.signal.last_seen`` (and a ``seen_count``), so the
    decide page ages a card on its newest evidence, not on the day it was first proposed. Never fails the writer."""
    t = time.time() if now is None else float(now)
    try:
        row = await evolution_queue._fetch(db, str(pid))
        sig = evolution_queue._json(row.get("evidence_json")).get("signal")
        sig = dict(sig) if isinstance(sig, dict) else {}
        sig["last_seen"] = round(t, 3)
        sig["seen_count"] = int(sig.get("seen_count") or 1) + 1
        await evolution_queue.attach_evidence(db, str(pid), {"signal": sig}, now=t)
    except Exception as exc:  # noqa: BLE001 - a recurrence mark is evidence, never a reason to drop the write path
        logger.warning("evolution_notice: could not mark %s seen again (%s)", pid, type(exc).__name__)


def _attach_notice_trace_to_row(
    row: dict,
    *,
    proposal_id: str,
    proposal_type: str,
    title: str,
    signal,
) -> dict:
    """Attach a non-persistent observation trace summary to proposal evidence."""

    try:
        from zoe_observation_trace import ObservationOutcome, ObservationTrace, ObservationTraceType
        from zoe_observation_trace_collector import ObservationTraceCollectorPolicy, collect_observation_traces

        evidence = json.loads(row["evidence"])
        candidate_ids = tuple(str(item) for item in evidence.get("candidate_ids", ()))
        trace = ObservationTrace(
            trace_id=f"trace_notice_{proposal_id}",
            trace_type=ObservationTraceType.PROPOSAL.value,
            surface="multica",
            scope=signal.scope,
            user_id=signal.user_id,
            outcome=ObservationOutcome.SUCCESS.value,
            summary=f"Runtime notice proposal prepared for review: {title}",
            evidence_refs=tuple(signal.evidence_refs),
            subject_id=proposal_id,
            related_ids=candidate_ids,
            metadata={
                "proposal_type": proposal_type,
                "signal_id": signal.signal_id,
                "signal_source": signal.source,
            },
        )
        collection = collect_observation_traces(
            (trace,),
            policy=ObservationTraceCollectorPolicy(
                max_batch_size=5,
                allowed_surfaces=("multica",),
                allowed_trace_types=(ObservationTraceType.PROPOSAL.value,),
            ),
        )
        collection_snapshot = collection.to_dict()
        if not collection.ok:
            logger.warning(
                "evolution_notice: trace collection rejected for proposal_id=%s proposal_type=%s rejected=%s",
                proposal_id,
                proposal_type,
                collection_snapshot["rejected"],
            )
        evidence["observation_trace_collection"] = collection_snapshot
        updated = dict(row)
        updated["evidence"] = json.dumps(evidence, sort_keys=True)
        return updated
    except Exception as exc:
        logger.warning(
            "evolution_notice: trace collection failed for proposal_id=%s proposal_type=%s: %s",
            proposal_id,
            proposal_type,
            exc,
        )
        return row


async def run_evolution_notice() -> dict:
    """Run the NOTICE phase — returns summary dict."""
    from db_pool import get_db_ctx
    from zoe_candidate_scoring import CandidateEvaluation, CandidateScore
    from zoe_evolution_proposal import EvolutionSignal, EvolutionSignalType
    from zoe_evolution_proposal_adapter import build_existing_zoe_proposal_candidate
    from zoe_evolution_runtime_intake import build_runtime_evolution_proposal_intake

    created = 0
    skipped = 0

    async with get_db_ctx() as db:
        # ── 1. Intent miss clustering ─────────────────────────────────────────
        misses = await asyncio.to_thread(_load_recent_misses)          # file read + O(n x clusters) trigrams: off the event loop
        clusters = await asyncio.to_thread(_simple_cluster, misses)
        logger.info(
            "evolution_notice: %d misses → %d clusters with ≥%d hits",
            len(misses), len(clusters), _MIN_CLUSTER_SIZE,
        )

        for rep, members in clusters:
            title = f"Intent gap: '{rep[:60]}'"
            prop_type = "intent_pattern"
            if await _proposal_exists(title, prop_type, db):
                skipped += 1
                continue
            prop_id = uuid.uuid4().hex
            description = (
                f"Intent router missed {len(members)} similar messages in the last "
                f"{_LOOKBACK_DAYS} days. Representative: '{rep}'. "
                f"Consider adding a new intent pattern or skill."
            )
            evidence = json.dumps({
                "miss_count": len(members),
                "examples": members[:5],
            })
            target_members = members[:20]
            evidence_ref = f"intent_miss_cluster:{prop_id}"
            signal = EvolutionSignal(
                signal_id=f"signal_{prop_id}",
                signal_type=EvolutionSignalType.REPEATED_FAILURE.value,
                summary=description,
                source="evolution_notice:intent_miss_cluster",
                evidence_refs=(evidence_ref,),
                scope="system",
                metadata={
                    "miss_count": len(members),
                    "examples": members[:5],
                    "representative": rep,
                },
            )
            candidate = build_existing_zoe_proposal_candidate(
                proposal_type=prop_type,
                title=title,
                evidence_refs=(
                    evidence_ref,
                    "services/zoe-data/evolution_notice.py:run_evolution_notice",
                    "training/data/intent-misses.jsonl",
                ),
                legacy_writer="evolution_notice:intent_miss_cluster",
                runtime_notes="Creates a review-only intent-gap proposal from clustered misses; no execution is granted.",
                target_patterns=target_members,
            )
            intake = build_runtime_evolution_proposal_intake(
                proposal_id=prop_id,
                proposal_type=prop_type,
                title=title,
                problem_statement=description,
                signal=signal,
                candidates=(candidate,),
                affected_capabilities=("intent_router", "chat_router", "observation_trace"),
                expected_benefit="Create a reviewable Zoe intent-gap proposal from clustered miss evidence before implementation work.",
                verification_plan=(
                    "human_or_multica_review_required_before_approval",
                    "implementation_pr_must_attach_tests_and_evidence_before_completion",
                ),
                rollback_plan="Reject or defer the proposal; no runtime change has been made by proposal creation.",
                legacy_target_patterns=target_members,
                metadata={"legacy_writer": "evolution_notice:intent_miss_cluster"},
            )
            row = _attach_notice_trace_to_row(
                intake.to_legacy_row(),
                proposal_id=prop_id,
                proposal_type=prop_type,
                title=title,
                signal=signal,
            )
            # autonomy_class/approval_required/risk are stamped by the DB trigger
            # (migration 0027) from the proposal type — no per-INSERT columns, so
            # this stays safe across the deploy window before the migration runs.
            await evolution_queue.propose(
                db, source="evolution_notice:intent_miss_cluster", change_class="code", component="intent_router",
                proposal_id=row["id"], legacy_type=row["type"], title=row["title"], description=row["description"],
                legacy_evidence=row["evidence"], target_patterns=row["target_patterns"],
                evidence={"signal": {"kind": "intent_miss_cluster", "miss_count": len(members)}},
            )
            created += 1
            logger.info("evolution_notice: proposed intent gap '%s' (%d hits)", rep[:60], len(members))

            # Auto-approve if cluster is large enough and threshold is set
            if _AUTO_APPROVE_THRESHOLD > 0 and len(members) >= _AUTO_APPROVE_THRESHOLD:
                await evolution_queue.decide(db, prop_id, "approved", actor="evolution_notice:auto_approve",
                                             note=f"cluster of {len(members)} >= {_AUTO_APPROVE_THRESHOLD}")
                logger.info("evolution_notice: auto-approved '%s' (%d hits >= threshold %d)",
                            rep[:60], len(members), _AUTO_APPROVE_THRESHOLD)

            await _mirror(db, prop_id, dict(intake.multica_payload))

        # ── 2. LLM error rate spike detection ────────────────────────────────
        cutoff_24h = time.time() - 86400
        tier_stats = await db.fetch(
            """SELECT agent_tier,
                      COUNT(*) as total,
                      SUM(CASE WHEN latency_ms < 0 OR latency_ms > 30000 THEN 1 ELSE 0 END) as errors
               FROM llm_call_log
               WHERE ts >= $1
               GROUP BY agent_tier""",
            cutoff_24h,
        )
        for row in tier_stats:
            total = row["total"] or 0
            errors = row["errors"] or 0
            if total < 10 or errors / total < 0.10:
                continue
            tier = row["agent_tier"]
            title = f"Agent health: {tier} error spike"
            if await _proposal_exists(title, "agent_health", db):
                skipped += 1
                continue
            prop_id = uuid.uuid4().hex
            description = f"{tier} had {errors}/{total} ({int(100*errors/total)}%) slow/error calls in 24h."
            evidence_ref = f"llm_call_log:agent_health:{tier}:{prop_id}"
            signal = EvolutionSignal(
                signal_id=f"signal_{prop_id}",
                signal_type=EvolutionSignalType.OUTCOME_EVAL_FAILURE.value,
                summary=description,
                source="evolution_notice:agent_health",
                evidence_refs=(evidence_ref,),
                scope="system",
                metadata={"agent_tier": tier, "total": total, "errors": errors},
            )
            candidate = CandidateEvaluation(
                candidate_id="existing_zoe_agent_health_triage",
                name="Existing Zoe agent health triage",
                source="existing_zoe",
                task="review agent health spike",
                score=CandidateScore(
                    fit=4,
                    activity=4,
                    license=5,
                    offline=5,
                    security=4,
                    footprint=5,
                    tests=3,
                    maintainability=4,
                    overlap=5,
                ),
                evidence_refs=(
                    evidence_ref,
                    "services/zoe-data/evolution_notice.py:run_evolution_notice",
                    "llm_call_log:agent_tier_latency",
                ),
                license_risk="compatible",
                offline_viability="required",
                runtime_notes="Creates a review-only agent-health evolution proposal and Multica ticket; no execution is granted.",
                overlaps_existing=("evolution_proposals", "multica_governance", "llm_call_log"),
                recommendation="needs_review",
                metadata={"legacy_proposal_type": "agent_health"},
            )
            intake = build_runtime_evolution_proposal_intake(
                proposal_id=prop_id,
                proposal_type="agent_health",
                title=title,
                problem_statement=description,
                signal=signal,
                candidates=(candidate,),
                affected_capabilities=("agent_runtime", "multica_governance", "observation_trace"),
                expected_benefit="Create a reviewable Zoe health proposal from measured LLM error evidence before implementation work.",
                verification_plan=(
                    "human_or_multica_review_required_before_approval",
                    "implementation_pr_must_attach_tests_and_evidence_before_completion",
                ),
                rollback_plan="Reject or defer the proposal; no runtime change has been made by proposal creation.",
                metadata={"legacy_writer": "evolution_notice:agent_health"},
            )
            row_payload = _attach_notice_trace_to_row(
                intake.to_legacy_row(),
                proposal_id=prop_id,
                proposal_type="agent_health",
                title=title,
                signal=signal,
            )
            await evolution_queue.propose(
                db, source="evolution_notice:agent_health", change_class="code", component="brain",
                proposal_id=row_payload["id"], legacy_type="agent_health", title=row_payload["title"],
                description=row_payload["description"], legacy_evidence=row_payload["evidence"],
                target_patterns=row_payload["target_patterns"],
                evidence={"signal": {"kind": "agent_health", "agent_tier": tier, "total": total, "errors": errors}},
            )
            created += 1

            await _mirror(db, row_payload["id"], dict(intake.multica_payload))

    return {"created": created, "skipped_dedup": skipped, "clusters": len(clusters)}


async def record_frustration_signal(
    user_id: str,
    normalized_message: str,
    session_id: str,
    repeat_count: int,
) -> None:
    """Write a user frustration proposal (called from chat.py inline)."""
    from db_pool import get_db_ctx
    from zoe_candidate_scoring import CandidateEvaluation, CandidateScore
    from zoe_evolution_proposal import EvolutionSignal, EvolutionSignalType
    from zoe_evolution_runtime_intake import build_runtime_evolution_proposal_intake

    title = f"User frustration: '{normalized_message[:60]}'"
    async with get_db_ctx() as db:
        rows = await db.fetch(
            """SELECT id FROM evolution_proposals
               WHERE type='user_frustration' AND title=$1
               AND status NOT IN ('rejected','kept','reverted')""",
            title,
        )
        if rows:
            await _seen_again(db, rows[0]["id"])     # recurred: the card ages on its newest evidence
            return  # already tracked

        prop_id = uuid.uuid4().hex
        description = (
            f"User {user_id} sent a substantially similar message {repeat_count} times "
            f"in session {session_id} without a satisfying response."
        )
        evidence_ref = f"chat_user_frustration:{user_id}:{session_id}:{prop_id}"
        signal = EvolutionSignal(
            signal_id=f"signal_{prop_id}",
            signal_type=EvolutionSignalType.USER_REQUEST.value,
            summary=description,
            source="evolution_notice:user_frustration",
            evidence_refs=(evidence_ref,),
            user_id=user_id,
            scope="personal",
            metadata={
                "session_id": session_id,
                "repeat_count": repeat_count,
                "message_excerpt": normalized_message[:500],
            },
        )
        candidate = CandidateEvaluation(
            candidate_id="existing_zoe_frustration_triage",
            name="Existing Zoe frustration triage",
            source="existing_zoe",
            task="review repeated user frustration",
            score=CandidateScore(
                fit=4,
                activity=4,
                license=5,
                offline=5,
                security=4,
                footprint=5,
                tests=3,
                maintainability=4,
                overlap=5,
            ),
            evidence_refs=(
                evidence_ref,
                "services/zoe-data/evolution_notice.py:record_frustration_signal",
                "services/zoe-data/routers/chat.py:frustration_signal",
            ),
            license_risk="compatible",
            offline_viability="required",
            runtime_notes="Creates a review-only evolution proposal and Multica ticket; no execution or memory write is granted.",
            overlaps_existing=("evolution_proposals", "multica_governance"),
            recommendation="needs_review",
            metadata={"legacy_proposal_type": "user_frustration"},
        )
        intake = build_runtime_evolution_proposal_intake(
            proposal_id=prop_id,
            proposal_type="user_frustration",
            title=title,
            problem_statement=description,
            signal=signal,
            candidates=(candidate,),
            affected_capabilities=("chat_experience", "memory_router", "observation_trace"),
            expected_benefit="Create a reviewable Zoe improvement proposal with explicit repeated-frustration evidence before implementation work.",
            verification_plan=(
                "human_or_multica_review_required_before_approval",
                "implementation_pr_must_attach_tests_and_evidence_before_completion",
            ),
            rollback_plan="Reject or defer the proposal; no runtime change has been made by proposal creation.",
            legacy_target_patterns=(normalized_message,),
            metadata={"legacy_writer": "evolution_notice:user_frustration"},
        )
        row = _attach_notice_trace_to_row(
            intake.to_legacy_row(),
            proposal_id=prop_id,
            proposal_type="user_frustration",
            title=title,
            signal=signal,
        )
        # autonomy_class is stamped by the DB trigger (migration 0027) from the type.
        await evolution_queue.propose(
            db, source="evolution_notice:user_frustration", change_class="code", component="chat",
            proposal_id=row["id"], legacy_type="user_frustration", title=row["title"], description=row["description"],
            legacy_evidence=row["evidence"], target_patterns=row["target_patterns"],
            evidence={"signal": {"kind": "user_frustration", "repeat_count": repeat_count}},
        )
        logger.info(
            "evolution_notice: frustration signal for user=%s message=%s repeats=%d",
            user_id, _utag(normalized_message), repeat_count,
        )

        await _mirror(db, row["id"], dict(intake.multica_payload))


async def record_user_issue(message: str, user_id: str) -> None:
    """Write a user-reported issue proposal (called from intent handler or agent tool).

    Unlike record_frustration_signal, this fires on a single explicit report —
    no repeat threshold required. Deduplicates by title so the same complaint
    phrased identically won't create multiple issues.
    """
    from db_pool import get_db_ctx
    from zoe_candidate_scoring import CandidateEvaluation, CandidateScore
    from zoe_evolution_proposal import EvolutionSignal, EvolutionSignalType
    from zoe_evolution_runtime_intake import build_runtime_evolution_proposal_intake

    title = f"User report: '{message[:60]}'"
    async with get_db_ctx() as db:
        rows = await db.fetch(
            """SELECT id FROM evolution_proposals
               WHERE type='user_issue_report' AND title=$1
               AND status NOT IN ('rejected','kept','reverted')""",
            title,
        )
        if rows:
            await _seen_again(db, rows[0]["id"])     # recurred: the card ages on its newest evidence
            return  # already tracked

        prop_id = uuid.uuid4().hex
        description = f"User {user_id} explicitly reported a problem: {message}"
        evidence_ref = f"chat_user_issue:{user_id}:{prop_id}"
        signal = EvolutionSignal(
            signal_id=f"signal_{prop_id}",
            signal_type=EvolutionSignalType.USER_REQUEST.value,
            summary=description,
            source="evolution_notice:user_issue_report",
            evidence_refs=(evidence_ref,),
            user_id=user_id,
            scope="personal",
            metadata={"message_excerpt": message[:500]},
        )
        candidate = CandidateEvaluation(
            candidate_id="existing_zoe_user_issue_triage",
            name="Existing Zoe user issue triage",
            source="existing_zoe",
            task="review user-reported issue",
            score=CandidateScore(
                fit=4,
                activity=4,
                license=5,
                offline=5,
                security=4,
                footprint=5,
                tests=3,
                maintainability=4,
                overlap=5,
            ),
            evidence_refs=(
                evidence_ref,
                "services/zoe-data/evolution_notice.py:record_user_issue",
                "services/zoe-data/intent_router.py:user_issue_report",
            ),
            license_risk="compatible",
            offline_viability="required",
            runtime_notes="Creates a review-only evolution proposal and Multica ticket; no execution or memory write is granted.",
            overlaps_existing=("evolution_proposals", "multica_governance"),
            recommendation="needs_review",
            metadata={"legacy_proposal_type": "user_issue_report"},
        )
        intake = build_runtime_evolution_proposal_intake(
            proposal_id=prop_id,
            proposal_type="user_issue_report",
            title=title,
            problem_statement=description,
            signal=signal,
            candidates=(candidate,),
            affected_capabilities=("chat_experience", "intent_router", "multica_governance"),
            expected_benefit="Create a reviewable Zoe improvement proposal with explicit user evidence before implementation work.",
            verification_plan=(
                "human_or_multica_review_required_before_approval",
                "implementation_pr_must_attach_tests_and_evidence_before_completion",
            ),
            rollback_plan="Reject or defer the proposal; no runtime change has been made by proposal creation.",
            legacy_target_patterns=(message,),
            metadata={"legacy_writer": "evolution_notice:user_issue_report"},
        )
        row = _attach_notice_trace_to_row(
            intake.to_legacy_row(),
            proposal_id=prop_id,
            proposal_type="user_issue_report",
            title=title,
            signal=signal,
        )
        await evolution_queue.propose(
            db, source="evolution_notice:user_issue_report", change_class="code", component="chat",
            proposal_id=row["id"], legacy_type="user_issue_report", title=row["title"], description=row["description"],
            legacy_evidence=row["evidence"], target_patterns=row["target_patterns"],
            evidence={"signal": {"kind": "user_issue_report"}},
        )
        logger.info(
            "evolution_notice: user issue report from user=%s message=%s",
            user_id, _utag(message),
        )

        await _mirror(db, row["id"], dict(intake.multica_payload, label_name="user-feedback"))


# ── MEASURE phase ─────────────────────────────────────────────────────────────
_MEASURE_WINDOW_S = 48 * 3600  # 48-hour monitoring window post-deployment


def _load_target_patterns(raw: str | None) -> list[str]:
    """Load target patterns from legacy arrays or proposal contract envelopes."""

    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if isinstance(payload, list):
        return [str(item) for item in payload if item is not None]
    if isinstance(payload, dict) and payload.get("schema") == "zoe_evolution_proposal":
        proposal = payload.get("proposal")
        metadata = proposal.get("metadata") if isinstance(proposal, dict) else None
        target_patterns = metadata.get("legacy_target_patterns") if isinstance(metadata, dict) else None
        if isinstance(target_patterns, list):
            return [str(item) for item in target_patterns if item is not None]
    return []


#: the honest measure (2026-11-13). ``validated`` needs a before / after comparison of the proposal's OWN target signal as a RATE: household
#: intent misses (``local_text_logs.INTENT_MISSES``) similar to its target patterns, per household turn the router logged
#: (``local_text_logs.ROUTER_SHADOW``: one record per routed turn, ``ts`` + ``traffic``), 48 h before vs 48 h after it was applied. It
#: needs PROOF OF COVERAGE: at least ``MEASURE_MIN_TURNS`` household turns in EACH window (there was traffic and the router logged
#: it), at least one miss-log record of any kind inside the after window (the miss writer was alive), and at least
#: ``MEASURE_MIN_BEFORE`` target misses before. A log that just stopped is never an improvement: it is ``unmeasured``.
MEASURE_MIN_BEFORE = 5
MEASURE_MIN_TURNS = 30
MEASURE_BETTER = 0.8          # after-rate <= 0.8 x before-rate: validated (misses per turn fell by at least a fifth)
MEASURE_WORSE = 1.2           # after-rate >= 1.2 x before-rate: regressed
_SIMILAR = 0.4                # the trigram overlap the NOTICE clustering used to group the misses in the first place


def _trigrams(s: str) -> set[str]:
    s = s.lower().strip()
    return {s[i:i + 3] for i in range(len(s) - 2)} if len(s) >= 3 else {s}


def _similar(a: str, b: str) -> bool:
    ta, tb = _trigrams(a), _trigrams(b)
    union = len(ta | tb)
    return bool(union) and len(ta & tb) / union > _SIMILAR


def measure_verdict(patterns: list, before: int | None, after: int | None, *, turns_before: int | None = None,
                    turns_after: int | None = None, min_before: int = MEASURE_MIN_BEFORE,
                    min_turns: int = MEASURE_MIN_TURNS, truncated: bool = False) -> tuple[str, str]:
    """(verdict, reason). verdict: validated | regressed | no_change | unmeasured. ``None`` = no coverage of that window."""
    if not patterns:
        return "unmeasured", "no_target_signal"
    if truncated:
        return "unmeasured", "scan_truncated"
    if before is None or turns_before is None or turns_before < min_turns:
        return "unmeasured", "no_before_data"
    if after is None or turns_after is None or turns_after < min_turns:
        return "unmeasured", "no_after_coverage"
    if before < min_before:
        return "unmeasured", "sample_too_small"
    rate_before, rate_after = before / turns_before, after / turns_after
    if rate_after <= rate_before * MEASURE_BETTER:
        return "validated", "fewer_misses_per_turn"
    if rate_after >= rate_before * MEASURE_WORSE:
        return "regressed", "more_misses_per_turn"
    return "no_change", "within_band"


#: scan bounds (2026-11-13). The measure runs inside zoe-data's event loop on the digest path, so the scan itself goes to a worker
#: thread (``asyncio.to_thread``) AND is bounded: a segment last written before the window is skipped by its mtime, a time-ordered
#: log stops at the first record past the window, and past these caps the scan stops and says so (``truncated``), which makes the
#: verdict ``unmeasured`` / ``scan_truncated``, never a guess. Sized ~10x above the live logs (the router log held ~13.5k lines in four
#: months; the miss log ~30 lines a week).
MEASURE_MAX_LINES = 200_000          # per log
MEASURE_MAX_BYTES = 64 * 1024 * 1024  # per log
MEASURE_MAX_COMPARISONS = 200_000    # trigram comparisons (target-window misses x patterns)


def _window_records(log, start: float, end: float) -> tuple[list[dict], bool, bool]:
    """The records of ``log`` with ``start <= ts < end``, whether the log reaches back to ``start`` (a record or a whole segment at or
    before it), and whether the scan was truncated by a cap."""
    import local_text_logs as ltl

    try:
        paths = ltl.segments(log)
    except Exception:  # noqa: BLE001
        paths = [ltl.log_path(log)]
    out: list[dict] = []
    covers = truncated = done = False
    lines = nbytes = 0
    for path in paths:                                     # oldest first
        try:
            if os.path.getmtime(path) < start:            # last written before the window: no record of it is inside
                covers = True
                continue
            fh = open(path, "rb")
        except OSError:
            continue
        with fh:
            for raw in fh:
                lines += 1
                nbytes += len(raw)
                if lines > MEASURE_MAX_LINES or nbytes > MEASURE_MAX_BYTES:
                    truncated = done = True
                    break
                try:
                    row = json.loads(raw)
                    ts = float(row["ts"])
                except (ValueError, KeyError, TypeError):
                    continue
                if ts <= start:
                    covers = True
                if ts >= end:                              # append-only and time-ordered: nothing later is inside
                    done = True
                    break
                if ts >= start:
                    row["ts"] = ts
                    out.append(row)
        if done:
            break
    return out, covers, truncated


def scan_windows(patterns: list, deployed_at: float, *, window_s: float = _MEASURE_WINDOW_S) -> dict:
    """BLOCKING (file reads, JSON, trigrams): call it through ``asyncio.to_thread`` from async code. The counts of both windows:
    target misses (``None`` = no coverage: before, the miss log does not reach back to the window start; after, it holds no record of
    any kind in the window, so the writer may have stopped) and household turns the router logged (``None`` = no coverage before)."""
    import local_text_logs as ltl

    start, end = deployed_at - window_s, deployed_at + window_s
    misses, miss_covers, t1 = _window_records(ltl.INTENT_MISSES, start, end)
    turns, turn_covers, t2 = _window_records(ltl.ROUTER_SHADOW, start, end)
    before = after = comparisons = 0
    alive_after = truncated = False
    for r in misses:
        if r["ts"] >= deployed_at:
            alive_after = True
        text = str(r.get("text") or "") if r.get("traffic_class") in (None, "household") else ""
        if not text:
            continue
        comparisons += len(patterns)
        if comparisons > MEASURE_MAX_COMPARISONS:
            truncated = True
            break
        if any(_similar(text, p) for p in patterns):
            if r["ts"] < deployed_at:
                before += 1
            else:
                after += 1
    tb = sum(1 for r in turns if r.get("traffic") == "household" and r["ts"] < deployed_at)
    ta = sum(1 for r in turns if r.get("traffic") == "household" and r["ts"] >= deployed_at)
    return {"before": before if miss_covers else None, "after": after if alive_after else None,
            "turns_before": tb if turn_covers else None, "turns_after": ta, "truncated": truncated or t1 or t2}


async def run_measure_phase() -> dict:
    """MEASURE: close the monitoring window of each APPLIED proposal (``deployed_at`` older than ``_MEASURE_WINDOW_S``, no result yet).

    Only an intent-gap proposal has a target signal today (its own intent misses); every other kind is recorded ``unmeasured`` /
    ``no_target_signal``. ``validated`` moves the row applied -> kept, ``regressed`` applied -> reverted (it got worse; nothing is rolled
    back automatically), ``no_change`` and ``unmeasured`` leave it applied. Every outcome is written to ``validation_result`` (schema 2,
    counts only) so the owner's decide page can show it. ``applied_due`` is how many rows were waiting: 0 means nothing was ever applied.
    """
    from db_pool import get_db_ctx

    now = time.time()
    results: dict = {"applied_due": 0, "evaluated": 0, "validated": 0, "regressed": 0, "no_change": 0, "unmeasured": 0}

    async with get_db_ctx() as db:
        rows = await db.fetch(
            """SELECT id, type, title, target_patterns, deployed_at
               FROM evolution_proposals
               WHERE status='applied'
                 AND COALESCE(source, '') <> 'struggle'   -- struggle proposals are measured by their own retrain ledger
                 AND validation_result IS NULL
                 AND deployed_at IS NOT NULL
                 AND deployed_at < $1""",
            now - _MEASURE_WINDOW_S,
        )
        results["applied_due"] = len(rows)

        for row in rows:
            proposal_id = row["id"]
            deployed_at = row["deployed_at"]
            patterns = _load_target_patterns(row["target_patterns"]) if row["type"] == "intent_pattern" else []
            # the scan is blocking file work: off the event loop (zoe-data's digest path runs this in-process)
            scan = (await asyncio.to_thread(scan_windows, patterns[:20], deployed_at) if patterns else
                    {"before": None, "after": None, "turns_before": None, "turns_after": None, "truncated": False})
            before, after, turns_before, turns_after = scan["before"], scan["after"], scan["turns_before"], scan["turns_after"]
            verdict, reason = measure_verdict(patterns, before, after, turns_before=turns_before, turns_after=turns_after,
                                              truncated=scan["truncated"])
            validation_result = {"schema": 2, "verdict": verdict, "reason": reason, "misses_before": before, "misses_after": after,
                                 "turns_before": turns_before, "turns_after": turns_after, "window_h": _MEASURE_WINDOW_S / 3600,
                                 "min_before": MEASURE_MIN_BEFORE, "min_turns": MEASURE_MIN_TURNS, "measured_at": now}
            move = {"validated": "kept", "regressed": "reverted"}.get(verdict)
            # the status move (when there is one) and the result are ONE transaction, and the result write is conditional on the
            # deployment it measured: a failed write leaves the row applied with no result, so the next pass measures it again
            try:
                async with evolution_queue.atomic(db):
                    if move:
                        await evolution_queue.transition(
                            db, proposal_id, move, actor="evolution_notice:measure", frm="applied", note="measured: " + verdict,
                            evidence={"before": {"misses_48h": before}, "after": {"misses_48h": after}},
                        )
                    written = await db.execute(
                        "UPDATE evolution_proposals SET validation_result=$1 "
                        "WHERE id=$2 AND validation_result IS NULL AND deployed_at = $3",
                        json.dumps(validation_result), proposal_id, deployed_at,
                    )
                    if int(getattr(written, "rowcount", 0) or 0) < 1:
                        raise evolution_queue.QueueError("the deployment changed while it was measured")
            except evolution_queue.QueueError as exc:
                logger.warning("evolution_measure: proposal %s not recorded: %s", proposal_id, exc)
                continue
            results["evaluated"] += 1
            results[verdict] += 1
            logger.info("evolution_measure: proposal %s -> %s (%s, before=%s after=%s)", proposal_id, verdict, reason, before, after)

            if move and board_mirror_on():
                from multica_client import update_multica_issue_on_proposal_status_change

                linked = await db.fetch("SELECT multica_issue_id FROM evolution_proposals WHERE id=$1", proposal_id)
                if linked and linked[0]["multica_issue_id"]:
                    await update_multica_issue_on_proposal_status_change(
                        linked[0]["multica_issue_id"], "validated" if verdict == "validated" else "failed")

    return results
