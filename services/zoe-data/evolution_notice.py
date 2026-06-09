"""evolution_notice.py — Phase 6 of the nightly dreaming cycle.

NOTICE phase of the self-evolution loop:
1. Intent misses: cluster similar misses from last 7 days, propose patterns with ≥3 hits
2. LLM errors: detect agent tiers with >10% error rate in 24h
3. Frustration signals: picked up inline in chat.py, stored as proposals directly

All proposals go to evolution_proposals table. Deduplication prevents repeat entries.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_MISS_PATH = Path.home() / "training" / "data" / "intent-misses.jsonl"
_LOOKBACK_DAYS = 7
_MIN_CLUSTER_SIZE = 3

# If > 0, proposals whose cluster size >= this value are auto-approved (skips human review).
# Low-risk pattern additions (e.g., 10+ identical misses) can be approved automatically.
# Set ZOE_AUTO_APPROVE_THRESHOLD=0 to require human review for all proposals.
_AUTO_APPROVE_THRESHOLD = int(os.environ.get("ZOE_AUTO_APPROVE_THRESHOLD", "0"))


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
    def trigrams(s: str) -> set[str]:
        s = s.lower().strip()
        return {s[i:i+3] for i in range(len(s) - 2)} if len(s) >= 3 else {s}

    clusters: list[list[str]] = []
    for text in texts:
        t_grams = trigrams(text)
        best_idx = None
        best_sim = 0.0
        for i, cluster in enumerate(clusters):
            rep_grams = trigrams(cluster[0])
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
    """Check if an open proposal with this type+title already exists."""
    rows = await db.fetch(
        """SELECT id FROM evolution_proposals
           WHERE type=$1 AND title=$2
           AND status NOT IN ('rejected','validated','failed')""",
        prop_type, title,
    )
    return len(rows) > 0


async def run_evolution_notice() -> dict:
    """Run the NOTICE phase — returns summary dict."""
    from db_pool import get_db_ctx
    from multica_client import sync_evolution_proposal_to_multica
    from zoe_evolution_proposal_adapter import dump_legacy_evolution_proposal_contract

    created = 0
    skipped = 0

    async with get_db_ctx() as db:
        # ── 1. Intent miss clustering ─────────────────────────────────────────
        misses = _load_recent_misses()
        clusters = _simple_cluster(misses)
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
            target_patterns = dump_legacy_evolution_proposal_contract(
                proposal_id=prop_id,
                title=title,
                description=description,
                evidence=evidence,
                proposal_type=prop_type,
                legacy_writer="evolution_notice:intent_miss_cluster",
                target_patterns=target_members,
            )
            await db.execute(
                """INSERT INTO evolution_proposals
                   (id, type, title, description, evidence, target_patterns, status, proposed_at)
                   VALUES ($1,$2,$3,$4,$5,$6,'pending',$7)""",
                prop_id,
                prop_type,
                title,
                description,
                evidence,
                target_patterns,
                time.time(),
            )
            created += 1
            logger.info("evolution_notice: proposed intent gap '%s' (%d hits)", rep[:60], len(members))

            # Auto-approve if cluster is large enough and threshold is set
            if _AUTO_APPROVE_THRESHOLD > 0 and len(members) >= _AUTO_APPROVE_THRESHOLD:
                await db.execute(
                    "UPDATE evolution_proposals SET status='approved' WHERE id=$1", prop_id
                )
                logger.info("evolution_notice: auto-approved '%s' (%d hits >= threshold %d)",
                            rep[:60], len(members), _AUTO_APPROVE_THRESHOLD)

            # Sync to Multica board
            multica_id = await sync_evolution_proposal_to_multica(
                proposal_id=prop_id,
                title=title,
                description=description,
                evidence=evidence,
                proposal_type=prop_type,
                contract_snapshot=target_patterns,
            )
            if multica_id:
                await db.execute(
                    "UPDATE evolution_proposals SET multica_issue_id=$1 WHERE id=$2",
                    multica_id, prop_id,
                )

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
            evidence_data = json.dumps({"agent_tier": tier, "total": total, "errors": errors})
            target_patterns = dump_legacy_evolution_proposal_contract(
                proposal_id=prop_id,
                title=title,
                description=description,
                evidence=evidence_data,
                proposal_type="agent_health",
                legacy_writer="evolution_notice:agent_health",
            )
            await db.execute(
                """INSERT INTO evolution_proposals
                   (id, type, title, description, evidence, target_patterns, status, proposed_at)
                   VALUES ($1,'agent_health',$2,$3,$4,$5,'pending',$6)""",
                prop_id,
                title,
                description,
                evidence_data,
                target_patterns,
                time.time(),
            )
            created += 1

            # Sync to Multica board
            multica_id = await sync_evolution_proposal_to_multica(
                proposal_id=prop_id,
                title=title,
                description=description,
                evidence=evidence_data,
                proposal_type="agent_health",
                contract_snapshot=target_patterns,
            )
            if multica_id:
                await db.execute(
                    "UPDATE evolution_proposals SET multica_issue_id=$1 WHERE id=$2",
                    multica_id, prop_id,
                )

    return {"created": created, "skipped_dedup": skipped, "clusters": len(clusters)}


async def record_frustration_signal(
    user_id: str,
    normalized_message: str,
    session_id: str,
    repeat_count: int,
) -> None:
    """Write a user frustration proposal (called from chat.py inline)."""
    from db_pool import get_db_ctx
    from multica_client import sync_evolution_proposal_to_multica
    from zoe_evolution_proposal_adapter import dump_legacy_evolution_proposal_contract

    title = f"User frustration: '{normalized_message[:60]}'"
    async with get_db_ctx() as db:
        rows = await db.fetch(
            """SELECT id FROM evolution_proposals
               WHERE type='user_frustration' AND title=$1
               AND status NOT IN ('rejected','validated','failed')""",
            title,
        )
        if rows:
            return  # already tracked

        prop_id = uuid.uuid4().hex
        description = (
            f"User {user_id} sent a substantially similar message {repeat_count} times "
            f"in session {session_id} without a satisfying response."
        )
        evidence = json.dumps({
            "user_id": user_id,
            "session_id": session_id,
            "repeat_count": repeat_count,
            "message": normalized_message,
        })
        target_patterns = dump_legacy_evolution_proposal_contract(
            proposal_id=prop_id,
            title=title,
            description=description,
            evidence=evidence,
            proposal_type="user_frustration",
            legacy_writer="evolution_notice:user_frustration",
            user_id=user_id,
            target_patterns=(normalized_message,),
        )
        await db.execute(
            """INSERT INTO evolution_proposals
               (id, type, title, description, evidence, target_patterns, status, proposed_at)
               VALUES ($1,'user_frustration',$2,$3,$4,$5,'pending',$6)""",
            prop_id,
            title,
            description,
            evidence,
            target_patterns,
            time.time(),
        )
        logger.info(
            "evolution_notice: frustration signal for user=%s message='%s' repeats=%d",
            user_id, normalized_message[:60], repeat_count,
        )

        # Sync to Multica board
        multica_id = await sync_evolution_proposal_to_multica(
            proposal_id=prop_id,
            title=title,
            description=description,
            evidence=evidence,
            proposal_type="user_frustration",
            contract_snapshot=target_patterns,
        )
        if multica_id:
            await db.execute(
                "UPDATE evolution_proposals SET multica_issue_id=$1 WHERE id=$2",
                multica_id, prop_id,
            )


async def record_user_issue(message: str, user_id: str) -> None:
    """Write a user-reported issue proposal (called from intent handler or agent tool).

    Unlike record_frustration_signal, this fires on a single explicit report —
    no repeat threshold required. Deduplicates by title so the same complaint
    phrased identically won't create multiple issues.
    """
    from db_pool import get_db_ctx
    from multica_client import sync_evolution_proposal_to_multica
    from zoe_evolution_proposal_adapter import dump_legacy_evolution_proposal_contract

    title = f"User report: '{message[:60]}'"
    async with get_db_ctx() as db:
        rows = await db.fetch(
            """SELECT id FROM evolution_proposals
               WHERE type='user_issue_report' AND title=$1
               AND status NOT IN ('rejected','validated','failed')""",
            title,
        )
        if rows:
            return  # already tracked

        prop_id = uuid.uuid4().hex
        description = f"User {user_id} explicitly reported a problem: {message}"
        evidence = json.dumps({"user_id": user_id, "message": message})
        target_patterns = dump_legacy_evolution_proposal_contract(
            proposal_id=prop_id,
            title=title,
            description=description,
            evidence=evidence,
            proposal_type="user_issue_report",
            legacy_writer="evolution_notice:user_issue_report",
            user_id=user_id,
            target_patterns=(message,),
        )
        await db.execute(
            """INSERT INTO evolution_proposals
               (id, type, title, description, evidence, target_patterns, status, proposed_at)
               VALUES ($1,'user_issue_report',$2,$3,$4,$5,'pending',$6)""",
            prop_id,
            title,
            description,
            evidence,
            target_patterns,
            time.time(),
        )
        logger.info(
            "evolution_notice: user issue report from user=%s message='%s'",
            user_id, message[:60],
        )

        multica_id = await sync_evolution_proposal_to_multica(
            proposal_id=prop_id,
            title=title,
            description=description,
            evidence=evidence,
            proposal_type="user_issue_report",
            label_name="user-feedback",
            contract_snapshot=target_patterns,
        )
        if multica_id:
            await db.execute(
                "UPDATE evolution_proposals SET multica_issue_id=$1 WHERE id=$2",
                multica_id, prop_id,
            )


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


async def run_measure_phase() -> dict:
    """MEASURE: close monitoring window for deployed proposals.

    For each proposal that has been deployed but whose validation_result is null
    and the deployed_at timestamp is older than _MEASURE_WINDOW_S, evaluate
    whether the change improved things (simple heuristic: intent miss rate
    dropped for the targeted patterns). Sets status to 'validated' or 'failed'.
    """
    from db_pool import get_db_ctx
    from multica_client import update_multica_issue_on_proposal_status_change

    now = time.time()
    results: dict = {"evaluated": 0, "validated": 0, "failed": 0}

    async with get_db_ctx() as db:
        rows = await db.fetch(
            """SELECT id, type, title, target_patterns, deployed_at
               FROM evolution_proposals
               WHERE status='deployed'
                 AND validation_result IS NULL
                 AND deployed_at IS NOT NULL
                 AND deployed_at < $1""",
            now - _MEASURE_WINDOW_S,
        )

        for row in rows:
            proposal_id = row["id"]
            target_patterns = _load_target_patterns(row["target_patterns"])

            # Heuristic: count intent misses for the target patterns in the 48h
            # BEFORE deploy vs the 48h AFTER deploy. If miss count dropped by
            # at least 20% or is 0 after deploy, mark as validated.
            deployed_at = row["deployed_at"]
            miss_before = 0
            miss_after = 0

            if target_patterns:
                for pattern in target_patterns[:5]:  # limit to 5 patterns
                    like = f"%{pattern[:30]}%"
                    before_rows = await db.fetch(
                        """SELECT COUNT(*) as cnt FROM chat_messages
                           WHERE role='user'
                             AND content ILIKE $1
                             AND created_at BETWEEN $2 AND $3""",
                        like,
                        deployed_at - _MEASURE_WINDOW_S,
                        deployed_at,
                    )
                    after_rows = await db.fetch(
                        """SELECT COUNT(*) as cnt FROM chat_messages
                           WHERE role='user'
                             AND content ILIKE $1
                             AND created_at BETWEEN $2 AND $3""",
                        like,
                        deployed_at,
                        deployed_at + _MEASURE_WINDOW_S,
                    )
                    miss_before += (before_rows[0]["cnt"] if before_rows else 0)
                    miss_after += (after_rows[0]["cnt"] if after_rows else 0)

            # Default: if no target patterns to measure, validate by time elapsed
            if not target_patterns:
                verdict = "validated"
            elif miss_before == 0:
                # No data to compare — validate cautiously
                verdict = "validated"
            elif miss_after <= miss_before * 0.8:
                verdict = "validated"  # ≥20% improvement
            else:
                verdict = "failed"

            validation_result = {
                "miss_before_48h": miss_before,
                "miss_after_48h": miss_after,
                "verdict": verdict,
                "measured_at": now,
            }

            new_status = verdict  # 'validated' or 'failed'
            await db.execute(
                """UPDATE evolution_proposals
                   SET status=$1, validation_result=$2
                   WHERE id=$3""",
                new_status,
                json.dumps(validation_result),
                proposal_id,
            )
            results["evaluated"] += 1
            results[verdict] += 1
            logger.info(
                "evolution_measure: proposal %s → %s (before=%d after=%d)",
                proposal_id, new_status, miss_before, miss_after,
            )

            # Sync status change to Multica board if issue is linked
            multica_id_rows = await db.fetch(
                "SELECT multica_issue_id FROM evolution_proposals WHERE id=$1",
                proposal_id,
            )
            multica_id = multica_id_rows[0]["multica_issue_id"] if multica_id_rows else None
            if multica_id:
                await update_multica_issue_on_proposal_status_change(multica_id, new_status)

    return results
