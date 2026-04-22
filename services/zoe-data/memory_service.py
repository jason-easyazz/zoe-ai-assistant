"""MemoryService - the sole read/write surface for Zoe memory.

This module is the backend-pluggable facade every caller should use. The
first implementation wraps MemPalace (Chroma under the hood) but callers
see only opaque `MemoryRef` objects - `wing`, `drawer`, `room`, and Chroma
collection names never leak above this module. Swapping to raw `chromadb`
or to a different vector store becomes a one-file change.

Contract (`memory_and_self-learning_audit` plan, Phase 1):

  * ingest()          - THE only way facts enter the store.
  * load_for_prompt() - the only path the agent system prompt uses.
  * search()          - the only path any semantic query uses.
  * review()          - UI approve/reject/edit.
  * tick_access()     - bumps access_count + last_accessed; called on every hit.
  * delete_user()     - admin-only `/api/users/{id}/forget`.
  * export_user()     - admin-only full JSON dump.

Safety rails enforced here, not in callers:

  * per-user `asyncio.Lock`    -> serialises concurrent writes for a single user.
  * idempotency keys           -> (user_id, user_turn_id) same call twice = one row.
  * fail-closed on missing     -> no anonymous writes. user_id is mandatory.
  * PII scrubber               -> Luhn CC, SSN-shape, 2FA, password-adjacent.
  * immutable audit log        -> every mutation appends to `mempalace_audit`.
  * metrics                    -> every path instruments `zoe_memory_*` counters.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

try:
    from memory_metrics import (
        memory_write_count,
        memory_search_latency_ms,
        memory_search_hit_count,
        memory_dedup_skip_count,
        memory_pii_reject_count,
    )
    _METRICS_OK = True
except Exception:  # pragma: no cover
    _METRICS_OK = False

logger = logging.getLogger(__name__)

_MEMPALACE_DATA = os.environ.get(
    "MEMPALACE_DATA_DIR", os.path.expanduser("~/.mempalace")
)

_AUDIT_COLLECTION = os.environ.get("ZOE_MEMORY_AUDIT_COLLECTION", "mempalace_audit")


@dataclass(frozen=True)
class MemoryRef:
    """Opaque reference returned by MemoryService."""
    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0


# PII scrubber
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_TOTP_RE = re.compile(r"(?:\b|:\s*)(\d{6})\b")
_CC_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_SECRET_LABEL_RE = re.compile(
    r"(?i)(?:^|\W)(password|passcode|passphrase|pin|api[\s_-]?key|token|secret|auth[\s_-]?token)\b",
)
_AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_PEM_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)?PRIVATE KEY-----")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_BANKING_CONTEXT_RE = re.compile(r"(?i)\b(iban|bank|account|swift|bic|transfer)\b")


def _luhn_valid(digits: str) -> bool:
    digits = re.sub(r"\D", "", digits)
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        n = int(d)
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def scrub_pii(text: str) -> tuple[str, Optional[str]]:
    """Return (possibly-redacted-text, reject_reason_or_None)."""
    if not text:
        return text, None

    for match in _CC_RE.finditer(text):
        if _luhn_valid(match.group(0)):
            return text, "luhn_cc"

    if _SSN_RE.search(text):
        return text, "ssn"

    if _SECRET_LABEL_RE.search(text):
        if _TOTP_RE.search(text):
            return text, "totp"

    if _AWS_KEY_RE.search(text):
        return text, "aws_key"

    if _PEM_RE.search(text):
        return text, "pem"

    if _JWT_RE.search(text):
        return text, "jwt"

    if _IBAN_RE.search(text) and _BANKING_CONTEXT_RE.search(text):
        return text, "iban"

    redacted = re.sub(
        r"(?i)\b(password|passcode|passphrase|pin|api[\s_-]?key|token|secret|auth[\s_-]?token)\b(\s*(?:is|=|:)\s*)\S+",
        r"\1\2[REDACTED]",
        text,
    )
    return redacted, None


class MemoryServiceError(Exception):
    """Raised for operational failures."""


class MemoryService:
    """The sole read/write surface for Zoe memory."""

    def __init__(self, data_dir: str = _MEMPALACE_DATA):
        self._data_dir = data_dir
        self._user_locks: dict[str, asyncio.Lock] = {}
        self._seen_keys: set[str] = set()

    async def ingest(
        self,
        text: str,
        *,
        user_id: str,
        source: str,
        session_id: Optional[str] = None,
        user_turn_id: Optional[str] = None,
        memory_type: str = "fact",
        confidence: float = 0.7,
        status: str = "approved",
        tags: Optional[list[str]] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        expires_at: Optional[str] = None,
        opt_out: bool = False,
    ) -> Optional[MemoryRef]:
        """Store a fact. Returns None when silently dropped."""
        self._require(user_id, "user_id is required")
        if not text or not text.strip():
            raise MemoryServiceError("empty text")

        if opt_out and source in {"chat_regex", "ambient", "digest", "consolidation"}:
            self._bump("opt_out", source)
            return None

        scrubbed, reject = scrub_pii(text)
        if reject:
            self._bump("pii_reject", source)
            if _METRICS_OK:
                memory_pii_reject_count.labels(pattern=reject).inc()
            logger.info(
                "memory_service: ingest rejected by PII scrubber user=%s source=%s pattern=%s",
                user_id, source, reject,
            )
            return None

        idem_key = self._idempotency_key(user_id, user_turn_id, scrubbed)
        if idem_key in self._seen_keys:
            self._bump("dedup", source)
            if _METRICS_OK:
                memory_dedup_skip_count.inc()
            return None

        lock = self._user_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            if idem_key in self._seen_keys:
                self._bump("dedup", source)
                return None

            metadata = self._build_metadata(
                user_id=user_id,
                source=source,
                session_id=session_id,
                user_turn_id=user_turn_id,
                memory_type=memory_type,
                confidence=confidence,
                status=status,
                tags=tags or [],
                entity_type=entity_type,
                entity_id=entity_id,
                expires_at=expires_at,
                idem_key=idem_key,
            )

            mem_id = f"zoe_{user_id}_{hashlib.md5(scrubbed.encode()).hexdigest()[:16]}"
            try:
                await self._run_sync(
                    self._write_row, mem_id, scrubbed, metadata
                )
            except Exception as exc:
                self._bump("error", source)
                logger.warning("memory_service: write failed user=%s source=%s: %s",
                               user_id, source, exc)
                raise MemoryServiceError(f"write failed: {exc}") from exc

            self._seen_keys.add(idem_key)
            await self._append_audit(
                mem_id=mem_id,
                user_id=user_id,
                actor=source,
                action="ingest",
                before=None,
                after={"text": scrubbed, **metadata},
            )
            self._bump("ok", source)
            return MemoryRef(id=mem_id, text=scrubbed, metadata=metadata)

    async def load_for_prompt(
        self, user_id: str, *, limit: int = 20
    ) -> list[MemoryRef]:
        """Fast metadata-filter read for system prompt injection."""
        self._require(user_id, "user_id is required")
        try:
            rows = await self._run_sync(self._metadata_read, user_id, limit)
        except Exception as exc:
            logger.warning("memory_service: load_for_prompt failed user=%s: %s",
                           user_id, exc)
            return []
        ids = [r.id for r in rows]
        if ids:
            asyncio.create_task(self._tick_access(user_id, ids))
        return rows

    async def search(
        self,
        query: str,
        *,
        user_id: str,
        limit: int = 10,
        timeout_s: float = 2.0,
    ) -> list[MemoryRef]:
        self._require(user_id, "user_id is required")
        if not query or not query.strip():
            return []
        t0 = time.monotonic()
        try:
            rows = await asyncio.wait_for(
                self._run_sync(self._semantic_search, query, user_id, limit),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            logger.debug("memory_service: search timed out after %.1fs", timeout_s)
            return []
        except Exception as exc:
            logger.warning("memory_service: search failed user=%s: %s", user_id, exc)
            return []
        finally:
            if _METRICS_OK:
                memory_search_latency_ms.observe((time.monotonic() - t0) * 1000)

        if _METRICS_OK:
            memory_search_hit_count.observe(len(rows))
        ids = [r.id for r in rows]
        if ids:
            asyncio.create_task(self._tick_access(user_id, ids))
        return rows

    async def delete_user(self, user_id: str, *, actor: str) -> int:
        """Right-to-be-forgotten. Returns number of rows removed."""
        self._require(user_id, "user_id is required")
        lock = self._user_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            try:
                ids = await self._run_sync(self._list_ids_for_user, user_id)
                if not ids:
                    return 0
                await self._run_sync(self._delete_ids, ids)
            except Exception as exc:
                raise MemoryServiceError(f"delete_user failed: {exc}") from exc
            await self._append_audit(
                mem_id="*",
                user_id=user_id,
                actor=actor,
                action="delete_user",
                before={"count": len(ids)},
                after=None,
            )
            return len(ids)

    async def list_by_status(
        self,
        *,
        user_id: str,
        status: str = "pending",
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryRef]:
        """List rows for a user by status, newest first."""
        self._require(user_id, "user_id is required")
        try:
            rows = await self._run_sync(self._list_by_status_sync, user_id, status)
        except Exception as exc:
            logger.warning("memory_service: list_by_status failed: %s", exc)
            return []
        return rows[offset: offset + limit]

    async def forget_last(
        self,
        *,
        user_id: str,
        actor: Optional[str] = None,
        window_s: int = 600,
        note: Optional[str] = None,
    ) -> Optional[MemoryRef]:
        """Soft-delete the most recently ingested memory for a user."""
        self._require(user_id, "user_id is required")
        approved = await self.list_by_status(user_id=user_id, status="approved", limit=5)
        pending = await self.list_by_status(user_id=user_id, status="pending", limit=5)
        candidates = approved + pending
        if not candidates:
            return None
        candidates.sort(key=lambda r: r.metadata.get("added_at", ""), reverse=True)
        newest = candidates[0]
        added_at = newest.metadata.get("added_at", "")
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(added_at.replace("Z", "+00:00")) if added_at else None
            if dt is not None:
                now = datetime.now(timezone.utc)
                if (now - dt).total_seconds() > window_s:
                    return None
        except Exception:
            return None
        return await self.review(
            newest.id,
            decision="reject",
            actor=actor or user_id,
            note=note or "forget_last",
        )

    async def sweep_soft_archive(
        self,
        *,
        user_id: str,
        actor: str = "system",
        min_age_days: int = 30,
        score_threshold: float = 0.02,
    ) -> list[str]:
        """Soft-archive low-score approved memories."""
        self._require(user_id, "user_id is required")
        approved = await self.list_by_status(
            user_id=user_id, status="approved", limit=10_000
        )
        if not approved:
            return []

        import math
        now = datetime.datetime.utcnow()
        HALF_LIFE_DAYS = 70.0
        LAMBDA = math.log(2) / HALF_LIFE_DAYS

        to_archive: list[str] = []
        for ref in approved:
            md = ref.metadata
            added_at = md.get("added_at") or ""
            try:
                dt = datetime.datetime.fromisoformat(added_at.replace("Z", ""))
                age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
            except Exception:
                continue
            if age_days < min_age_days:
                continue
            try:
                conf = float(md.get("confidence", 0.7) or 0.7)
            except (TypeError, ValueError):
                conf = 0.7
            try:
                access_count = int(md.get("access_count", 0) or 0)
            except (TypeError, ValueError):
                access_count = 0
            score = conf * math.exp(-LAMBDA * age_days) + 0.1 * math.log1p(access_count)
            if score < score_threshold:
                to_archive.append(ref.id)

        archived: list[str] = []
        for mid in to_archive:
            try:
                await self.review(
                    mid,
                    decision="archive",
                    actor=actor,
                    note=f"soft-archive: score<{score_threshold}, age>={min_age_days}d",
                )
                archived.append(mid)
            except Exception as exc:
                logger.warning(
                    "memory_service: soft-archive failed id=%s: %s", mid, exc
                )
        if archived:
            logger.info(
                "memory_service: soft-archived %d rows user=%s",
                len(archived), user_id,
            )
        return archived

    async def get(self, mem_id: str) -> Optional[MemoryRef]:
        """Fetch a single row by id."""
        try:
            row = await self._run_sync(self._get_sync, mem_id)
        except Exception as exc:
            logger.warning("memory_service: get failed id=%s: %s", mem_id, exc)
            return None
        return row

    async def review(
        self,
        mem_id: str,
        *,
        decision: str,
        actor: str,
        edits: Optional[str] = None,
        note: Optional[str] = None,
    ) -> MemoryRef:
        """Approve / reject / edit a pending memory."""
        decision = decision.lower().strip()
        if decision not in {"approve", "reject", "archive", "edit"}:
            raise MemoryServiceError(
                f"decision must be approve|reject|archive|edit, got {decision!r}"
            )
        current = await self.get(mem_id)
        if current is None:
            raise MemoryServiceError(f"memory {mem_id} not found")
        user_id = current.metadata.get("user_id") or current.metadata.get("wing")
        self._require(user_id, "reviewed row is missing user_id metadata")

        lock = self._user_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            if decision in {"approve", "reject", "archive"}:
                new_status = {
                    "approve": "approved",
                    "reject": "rejected",
                    "archive": "archived",
                }[decision]
                new_meta = dict(current.metadata)
                new_meta["status"] = new_status
                new_meta["reviewed_by"] = actor
                new_meta["reviewed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
                if note:
                    new_meta["review_note"] = note[:1024]
                await self._run_sync(
                    self._write_row, mem_id, current.text, new_meta
                )
                await self._append_audit(
                    mem_id=mem_id,
                    user_id=user_id,
                    actor=actor,
                    action=decision,
                    before={"status": current.metadata.get("status"), "text": current.text},
                    after={"status": new_status, "text": current.text},
                    reason=note or "",
                )
                return MemoryRef(id=mem_id, text=current.text, metadata=new_meta)

            # decision == "edit"
            new_text = (edits or current.text).strip()
            if not new_text:
                raise MemoryServiceError("edit decision requires non-empty edits")
            scrubbed, reject = scrub_pii(new_text)
            if reject:
                raise MemoryServiceError(f"edit rejected by PII scrubber: {reject}")
            new_id = f"zoe_{user_id}_{hashlib.md5(scrubbed.encode()).hexdigest()[:16]}"
            new_meta = self._build_metadata(
                user_id=user_id,
                source=current.metadata.get("source", "review_ui"),
                session_id=current.metadata.get("session_id"),
                user_turn_id=current.metadata.get("user_turn_id"),
                memory_type=current.metadata.get("memory_type", "fact"),
                confidence=float(current.metadata.get("confidence", 0.7)),
                status="approved",
                tags=self._unpack_tags(current.metadata.get("tags", "")),
                entity_type=current.metadata.get("entity_type"),
                entity_id=current.metadata.get("entity_id"),
                expires_at=current.metadata.get("expires_at"),
                idem_key=self._idempotency_key(user_id, mem_id, scrubbed),
            )
            new_meta["supersedes_id"] = mem_id
            new_meta["reviewed_by"] = actor
            new_meta["reviewed_at"] = new_meta["added_at"]
            if note:
                new_meta["review_note"] = note[:1024]
            old_meta = dict(current.metadata)
            old_meta["status"] = "superseded"
            old_meta["superseded_by_id"] = new_id
            await self._run_sync(
                self._write_row, mem_id, current.text, old_meta
            )
            await self._run_sync(
                self._write_row, new_id, scrubbed, new_meta
            )
            await self._append_audit(
                mem_id=new_id,
                user_id=user_id,
                actor=actor,
                action="edit",
                before={"id": mem_id, "text": current.text},
                after={"id": new_id, "text": scrubbed},
                reason=note or "",
            )
            return MemoryRef(id=new_id, text=scrubbed, metadata=new_meta)

    async def export_user(self, user_id: str) -> dict[str, Any]:
        """Full JSON dump for GDPR-style export."""
        self._require(user_id, "user_id is required")
        try:
            items = await self._run_sync(self._export_rows, user_id)
        except Exception as exc:
            raise MemoryServiceError(f"export_user failed: {exc}") from exc
        return {
            "user_id": user_id,
            "exported_at": datetime.datetime.utcnow().isoformat() + "Z",
            "count": len(items),
            "items": items,
        }

    async def collection_sizes_by_user(self) -> dict[str, int]:
        """Return {user_id: record_count} across the whole MemPalace."""
        try:
            return await self._run_sync(self._collection_sizes_sync)
        except Exception:
            return {}

    def _collection_sizes_sync(self) -> dict[str, int]:
        from collections import Counter as _Counter
        col = self._collection()
        result = col.get(include=["metadatas"])
        metas = result.get("metadatas") or []
        counts: _Counter = _Counter()
        for m in metas:
            if not isinstance(m, dict):
                continue
            uid = m.get("user_id") or m.get("wing") or "unknown"
            counts[uid] += 1
        return dict(counts)

    # Internal helpers

    @staticmethod
    def _require(value: Any, msg: str) -> None:
        if not value:
            raise MemoryServiceError(msg)

    @staticmethod
    def _idempotency_key(user_id: str, turn_id: Optional[str], text: str) -> str:
        basis = f"{user_id}|{turn_id or ''}|{text}".encode()
        return hashlib.sha256(basis).hexdigest()

    @staticmethod
    def _build_metadata(
        *,
        user_id: str,
        source: str,
        session_id: Optional[str],
        user_turn_id: Optional[str],
        memory_type: str,
        confidence: float,
        status: str,
        tags: list[str],
        entity_type: Optional[str],
        entity_id: Optional[str],
        expires_at: Optional[str],
        idem_key: str,
    ) -> dict[str, Any]:
        now = datetime.datetime.utcnow().isoformat() + "Z"
        md: dict[str, Any] = {
            "user_id": user_id,
            "wing": user_id,
            "room": "conversations",
            "memory_type": memory_type,
            "confidence": float(confidence),
            "source": source,
            "status": status,
            "added_by": source,
            "added_at": now,
            "last_accessed": now,
            "access_count": 0,
            "embedding_model_version": os.environ.get(
                "ZOE_EMBEDDING_MODEL_VERSION", "minilm-v1"
            ),
            "idempotency_key": idem_key,
            "tags": ",".join(tags),
        }
        if session_id:
            md["session_id"] = session_id
        if user_turn_id:
            md["user_turn_id"] = user_turn_id
        if entity_type:
            md["entity_type"] = entity_type
        if entity_id:
            md["entity_id"] = entity_id
        if expires_at:
            md["expires_at"] = expires_at
        return md

    def _bump(self, status: str, source: str) -> None:
        if _METRICS_OK:
            memory_write_count.labels(source=source, status=status).inc()

    @staticmethod
    async def _run_sync(fn, *args):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, fn, *args)

    def _collection(self):
        from mempalace.palace import get_collection  # type: ignore[import]
        return get_collection(self._data_dir)

    def _audit_collection(self):
        import chromadb
        client = chromadb.PersistentClient(path=self._data_dir)
        return client.get_or_create_collection(_AUDIT_COLLECTION)

    def _write_row(self, mem_id: str, text: str, metadata: dict[str, Any]) -> None:
        col = self._collection()
        col.upsert(ids=[mem_id], documents=[text], metadatas=[metadata])

    def _metadata_read(self, user_id: str, limit: int) -> list[MemoryRef]:
        col = self._collection()
        result = col.get(
            where={"$or": [{"user_id": user_id}, {"wing": user_id}]},
            include=["documents", "metadatas"],
        )
        docs = result.get("documents") or []
        metas = result.get("metadatas") or []
        ids = result.get("ids") or []
        now = datetime.datetime.utcnow()
        now_iso = now.isoformat() + "Z"
        filtered: list[MemoryRef] = []
        for rid, doc, meta in zip(ids, docs, metas):
            if not isinstance(meta, dict):
                meta = {}
            expires = meta.get("expires_at")
            if expires and expires <= now_iso:
                continue
            st = meta.get("status", "approved")
            if st in {"archived", "rejected", "superseded"}:
                continue
            filtered.append(MemoryRef(id=rid, text=doc or "", metadata=dict(meta)))

        import math
        HALF_LIFE_DAYS = 70.0
        LAMBDA = math.log(2) / HALF_LIFE_DAYS

        def _score(ref: MemoryRef) -> tuple[float, str]:
            md = ref.metadata
            try:
                conf = float(md.get("confidence", 0.7) or 0.7)
            except (TypeError, ValueError):
                conf = 0.7
            try:
                access_count = int(md.get("access_count", 0) or 0)
            except (TypeError, ValueError):
                access_count = 0
            added_at = md.get("added_at") or ""
            try:
                dt = datetime.datetime.fromisoformat(added_at.replace("Z", ""))
                age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
            except Exception:
                age_days = 0.0
            score = conf * math.exp(-LAMBDA * age_days) + 0.1 * math.log1p(access_count)
            return (score, added_at)

        filtered.sort(key=_score, reverse=True)
        return filtered[:limit]

    def _semantic_search(self, query: str, user_id: str, limit: int) -> list[MemoryRef]:
        col = self._collection()
        result = col.query(
            query_texts=[query],
            n_results=max(limit * 3, limit),
            where={"$or": [{"user_id": user_id}, {"wing": user_id}]},
            include=["documents", "metadatas", "distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        now_iso = datetime.datetime.utcnow().isoformat() + "Z"
        hits: list[MemoryRef] = []
        for rid, doc, meta, dist in zip(ids, docs, metas, distances):
            md = dict(meta) if isinstance(meta, dict) else {}
            expires = md.get("expires_at")
            if expires and expires <= now_iso:
                continue
            st = md.get("status", "approved")
            if st in {"archived", "rejected", "superseded"}:
                continue
            hits.append(
                MemoryRef(
                    id=rid,
                    text=doc or "",
                    metadata=md,
                    score=float(dist or 0.0),
                )
            )
            if len(hits) >= limit:
                break
        return hits

    def _list_ids_for_user(self, user_id: str) -> list[str]:
        col = self._collection()
        result = col.get(
            where={"$or": [{"user_id": user_id}, {"wing": user_id}]},
            include=[],
        )
        return list(result.get("ids") or [])

    def _delete_ids(self, ids: list[str]) -> None:
        col = self._collection()
        col.delete(ids=ids)

    def _list_by_status_sync(self, user_id: str, status: str) -> list[MemoryRef]:
        col = self._collection()
        result = col.get(
            where={
                "$and": [
                    {"$or": [{"user_id": user_id}, {"wing": user_id}]},
                    {"status": status},
                ]
            },
            include=["documents", "metadatas"],
        )
        ids = result.get("ids") or []
        docs = result.get("documents") or []
        metas = result.get("metadatas") or []
        now_iso = datetime.datetime.utcnow().isoformat() + "Z"
        keep = []
        for rid, doc, meta in zip(ids, docs, metas):
            md = dict(meta) if isinstance(meta, dict) else {}
            expires = md.get("expires_at")
            if expires and expires <= now_iso:
                continue
            keep.append((rid, doc, md))
        ids = [r[0] for r in keep]
        docs = [r[1] for r in keep]
        metas = [r[2] for r in keep]
        rows = [
            MemoryRef(id=rid, text=doc or "", metadata=dict(meta) if isinstance(meta, dict) else {})
            for rid, doc, meta in zip(ids, docs, metas)
        ]
        rows.sort(key=lambda r: r.metadata.get("added_at", ""), reverse=True)
        return rows

    def _get_sync(self, mem_id: str) -> Optional[MemoryRef]:
        col = self._collection()
        result = col.get(ids=[mem_id], include=["documents", "metadatas"])
        ids = result.get("ids") or []
        docs = result.get("documents") or []
        metas = result.get("metadatas") or []
        if not ids:
            return None
        meta = metas[0] if isinstance(metas[0], dict) else {}
        return MemoryRef(id=ids[0], text=docs[0] or "", metadata=dict(meta))

    @staticmethod
    def _unpack_tags(raw: Any) -> list[str]:
        if not raw:
            return []
        if isinstance(raw, list):
            return [str(t) for t in raw if t]
        return [t for t in str(raw).split(",") if t]

    def _export_rows(self, user_id: str) -> list[dict[str, Any]]:
        col = self._collection()
        result = col.get(
            where={"$or": [{"user_id": user_id}, {"wing": user_id}]},
            include=["documents", "metadatas"],
        )
        ids = result.get("ids") or []
        docs = result.get("documents") or []
        metas = result.get("metadatas") or []
        return [
            {"id": rid, "text": doc, "metadata": meta}
            for rid, doc, meta in zip(ids, docs, metas)
        ]

    async def _tick_access(self, user_id: str, ids: Iterable[str]) -> None:
        """Best-effort metadata update - never raises."""
        ids_list = list(ids)
        if not ids_list:
            return
        lock = self._user_locks.setdefault(user_id, asyncio.Lock())
        try:
            async with lock:
                await self._run_sync(self._tick_access_sync, user_id, ids_list)
        except Exception:
            pass

    def _tick_access_sync(self, user_id: str, ids: list[str]) -> None:
        if not ids:
            return
        col = self._collection()
        result = col.get(ids=ids, include=["metadatas", "documents"])
        now_iso = datetime.datetime.utcnow().isoformat() + "Z"
        got_ids = result.get("ids") or []
        got_docs = result.get("documents") or []
        got_metas = result.get("metadatas") or []
        new_metas = []
        for meta in got_metas:
            m = dict(meta) if isinstance(meta, dict) else {}
            m["access_count"] = int(m.get("access_count", 0) or 0) + 1
            m["last_accessed"] = now_iso
            new_metas.append(m)
        if got_ids:
            col.upsert(ids=got_ids, documents=got_docs, metadatas=new_metas)

    async def _append_audit(
        self,
        *,
        mem_id: str,
        user_id: str,
        actor: str,
        action: str,
        before: Optional[dict[str, Any]],
        after: Optional[dict[str, Any]],
        reason: str = "",
    ) -> None:
        try:
            await self._run_sync(
                self._append_audit_sync,
                mem_id, user_id, actor, action, before, after, reason,
            )
        except Exception as exc:
            logger.warning("memory_service: audit append failed: %s", exc)

    def _append_audit_sync(
        self,
        mem_id: str,
        user_id: str,
        actor: str,
        action: str,
        before: Optional[dict[str, Any]],
        after: Optional[dict[str, Any]],
        reason: str,
    ) -> None:
        import json as _json
        col = self._audit_collection()
        audit_id = str(uuid.uuid4())
        summary = f"{action} {mem_id} by {actor} for {user_id}"
        metadata = {
            "mempalace_id": mem_id,
            "user_id": user_id,
            "actor": actor,
            "action": action,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "before": _json.dumps(before or {}, default=str)[:4000],
            "after": _json.dumps(after or {}, default=str)[:4000],
            "reason": reason,
        }
        col.upsert(ids=[audit_id], documents=[summary], metadatas=[metadata])


_service_singleton: Optional[MemoryService] = None


def get_memory_service() -> MemoryService:
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = MemoryService()
    return _service_singleton


__all__ = ["MemoryService", "MemoryRef", "MemoryServiceError", "get_memory_service", "scrub_pii"]
