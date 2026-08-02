"""TRIAGE-JUDGE (TT1) — board-native admission triage for Multica backlog tickets.

This module is the *judgment* half of the ``backlog -> todo`` admission gate. It
sits BEFORE the execution chain (scout -> implement -> verify -> review ->
closeout -> retro): given a single backlog ticket's structured fields it emits a
**machine-readable verdict** (admit / reject with a reason_code from a fixed
vocab, structured evidence, the carried-through ``zoe_kind`` and the
``ref@sha`` it judged against). It also computes — but never applies — the board
disposition parameters (target status + ``Triage:`` note + label) for a reject,
so the actual board mutation stays TT2's job.

Design rules that hold this safe:

* **Structured, never prose.** The verdict is built from a *typed* judgment. A
  raw classifier's output is parsed into the typed shape and VALIDATED; an
  unparseable, inconsistent, or low-confidence result **fails closed** to a
  non-admit ``needs_info`` reject — never ``admit``.
* **Injectable classifier.** The model call is a plain callable passed in, so
  the module is unit-testable deterministically with a fake. No import-time or
  hidden global model wiring lives here.
* **Default-OFF.** Nothing here is wired into the runtime; the feature is gated
  on ``ZOE_MULTICA_TRIAGE_JUDGE`` (default false) via :func:`triage_judge_enabled`.
  Importing or merging this module changes NO runtime behavior.
* **Disposition mapping matches the existing maintenance script EXACTLY** for
  the three lanes it already handles (``duplicate``/``wont_fix``/``monitor`` ->
  status ``done``, label ``<code with _ -> ->``, note ``Triage: <code> - <reason>``);
  see ``scripts/maintenance/multica_apply_triage_dispositions.py``.
"""

from __future__ import annotations

import dataclasses
import math
import re
from typing import Any, Callable, Mapping

from typed_env import env_bool

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: Feature flag — default OFF. Read at call time via typed_env.
TRIAGE_JUDGE_FLAG = "ZOE_MULTICA_TRIAGE_JUDGE"

#: reason_code used for an admit verdict.
ADMIT_REASON_CODE = "relevant"

#: reason_code the module falls back to when it must NOT admit but also must not
#: destructively close a ticket (parse failure / low confidence / disabled).
FAIL_CLOSED_REASON_CODE = "needs_info"

#: Fixed reject reason_code vocabulary.
REJECT_REASON_CODES: frozenset[str] = frozenset(
    {
        "duplicate",
        "wont_fix",
        "monitor",
        "stale",
        "already_shipped",
        "not_reproducible",
        "not_a_bug",
        "out_of_scope",
        "needs_info",
    }
)

#: Every reason_code the verdict may carry.
ALL_REASON_CODES: frozenset[str] = REJECT_REASON_CODES | {ADMIT_REASON_CODE}

#: A raw classifier result below this confidence fails closed.
MIN_CONFIDENCE = 0.5

#: Reject reason_code -> board close status. ``duplicate``/``wont_fix``/``monitor``
#: map to ``done`` to match multica_apply_triage_dispositions.py EXACTLY. Codes
#: that mean "this will not be worked" cancel. ``needs_info`` is the safe HOLD
#: outcome: no auto board mutation, ticket stays in backlog for a human.
_CLOSE_STATUS: dict[str, str | None] = {
    "duplicate": "done",
    "wont_fix": "done",
    "monitor": "done",
    "already_shipped": "done",
    "stale": "canceled",
    "not_reproducible": "canceled",
    "not_a_bug": "canceled",
    "out_of_scope": "canceled",
    "needs_info": None,
}

_DEFAULT_ZOE_KIND = "operator_task"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

#: A classifier is any callable that maps a ticket mapping to a raw judgment
#: dict. It may raise; :func:`judge_ticket` treats any exception as fail-closed.
Classifier = Callable[[Mapping[str, Any]], Any]


@dataclasses.dataclass(frozen=True)
class TriageVerdict:
    """Machine-readable admission verdict for one backlog ticket.

    Structural invariant (enforced in :meth:`__post_init__`): an ``admit``
    verdict MUST carry a valid ``confidence`` (a finite number in
    ``[MIN_CONFIDENCE, 1.0]``) AND a concrete ``reviewed_ref`` of the form
    ``<ticket-ref>@<valid-sha>`` (no ``unknown`` sentinel). A caller therefore
    cannot construct a false admit directly — a reject/fail-closed verdict has
    no such requirement.
    """

    disposition: str  # "admit" | "reject"
    reason: str
    reason_code: str
    evidence: list[Any]
    zoe_kind: str
    reviewed_ref: str
    confidence: float | None = None

    def __post_init__(self) -> None:
        if self.disposition == "admit":
            if not _confidence_ok(self.confidence):
                raise ValueError(
                    "admit verdict requires a finite confidence in "
                    f"[{MIN_CONFIDENCE}, 1.0]; got {self.confidence!r}"
                )
            if not _admit_reviewed_ref_ok(self.reviewed_ref):
                raise ValueError(
                    "admit verdict requires a concrete reviewed_ref "
                    f"(<ticket-ref>@<sha>); got {self.reviewed_ref!r}"
                )

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @property
    def is_admit(self) -> bool:
        return self.disposition == "admit"


@dataclasses.dataclass(frozen=True)
class DispositionAction:
    """Board parameters for a reject verdict — computed here, applied by TT2.

    ``target_status`` is ``None`` for the ``needs_info`` HOLD outcome, meaning
    "do not auto-close; leave in backlog". ``note`` and ``label`` mirror
    multica_apply_triage_dispositions.py exactly.
    """

    reason_code: str
    target_status: str | None  # "done" | "canceled" | None (hold)
    note: str
    label: str

    @property
    def closes(self) -> bool:
        return self.target_status is not None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Flag
# ---------------------------------------------------------------------------


def triage_judge_enabled() -> bool:
    """Whether the board-native triage judge is enabled. Default OFF.

    The flag name is spelled as a literal (not via ``TRIAGE_JUDGE_FLAG``) so the
    static ``tools/audit/flag_inventory.py`` scanner records it; the constant
    mirrors it for callers/tests.
    """
    return env_bool("ZOE_MULTICA_TRIAGE_JUDGE", default=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


#: A commit SHA is a 7-40 char hex string (short or full git SHA).
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)

#: An admit's reviewed_ref must be ``<concrete-ref>@<valid-sha>`` (no sentinel).
_ADMIT_REVIEWED_REF_RE = re.compile(r"^(?P<ref>.+)@(?P<sha>[0-9a-f]{7,40})$", re.IGNORECASE)


def _concrete_ticket_ref(ticket: Mapping[str, Any]) -> str | None:
    """The ticket's concrete reference, or ``None`` if it has no usable id."""
    for key in ("reference", "identifier", "id"):
        val = ticket.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def _ticket_ref(ticket: Mapping[str, Any]) -> str:
    """Best-effort ticket ref for a reject verdict; ``"unknown"`` if none present."""
    return _concrete_ticket_ref(ticket) or "unknown"


def _valid_sha(commit_sha: Any) -> str | None:
    """Normalized commit SHA if it is a valid 7-40 char hex string, else ``None``."""
    if not isinstance(commit_sha, str):
        return None
    sha = commit_sha.strip()
    return sha if _SHA_RE.match(sha) else None


def _confidence_ok(value: Any) -> bool:
    """True iff ``value`` is a real, finite number in ``[0.0, 1.0]`` and at least
    :data:`MIN_CONFIDENCE`.

    Booleans are rejected (``bool`` subclasses ``int``); NaN and ±inf are
    rejected via :func:`math.isfinite`; strings and other non-numerics are
    rejected. This is the single admit-confidence predicate shared by the
    :class:`TriageVerdict` structural guard and :func:`judge_ticket`.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    f = float(value)
    return math.isfinite(f) and 0.0 <= f <= 1.0 and f >= MIN_CONFIDENCE


def _admit_reviewed_ref_ok(reviewed_ref: Any) -> bool:
    """True iff ``reviewed_ref`` is ``<concrete-ref>@<valid-sha>`` (no ``unknown``)."""
    if not isinstance(reviewed_ref, str):
        return False
    m = _ADMIT_REVIEWED_REF_RE.match(reviewed_ref)
    if m is None:
        return False
    ref = m.group("ref").strip()
    return bool(ref) and ref != "unknown"


def _carried_zoe_kind(ticket: Mapping[str, Any], raw: Any) -> str:
    """Prefer a classifier-provided kind, else carry the ticket's through."""
    if isinstance(raw, Mapping):
        candidate = raw.get("zoe_kind")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    ticket_kind = ticket.get("zoe_kind")
    if isinstance(ticket_kind, str) and ticket_kind.strip():
        return ticket_kind.strip()
    return _DEFAULT_ZOE_KIND


def _reviewed_ref(ticket: Mapping[str, Any], commit_sha: str) -> str:
    sha = (commit_sha or "unknown").strip() or "unknown"
    return f"{_ticket_ref(ticket)}@{sha}"


def _coerce_evidence(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _fail_closed(
    ticket: Mapping[str, Any],
    commit_sha: str,
    raw: Any,
    reason: str,
) -> TriageVerdict:
    """Safe non-admit verdict. Never closes the ticket (needs_info -> hold)."""
    return TriageVerdict(
        disposition="reject",
        reason=reason,
        reason_code=FAIL_CLOSED_REASON_CODE,
        evidence=[],
        zoe_kind=_carried_zoe_kind(ticket, raw),
        reviewed_ref=_reviewed_ref(ticket, commit_sha),
    )


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def judge_ticket(
    ticket: Mapping[str, Any],
    *,
    classifier: Classifier,
    commit_sha: str,
) -> TriageVerdict:
    """Judge one backlog ticket into a typed :class:`TriageVerdict`.

    The classifier is invoked with the ticket mapping and expected to return a
    dict shaped like::

        {"disposition": "admit"|"reject", "reason_code": <vocab>,
         "reason": str, "evidence": list, "confidence": float(0..1),
         "zoe_kind": <optional override>}

    Any exception, non-dict result, unknown/inconsistent reason_code, or missing
    reason FAILS CLOSED to a ``needs_info`` reject — never admit. An ADMIT
    additionally requires a present, finite, in-range confidence at least
    :data:`MIN_CONFIDENCE`, a concrete ticket reference, and a valid hex
    ``commit_sha``; any shortfall on the admit path fails closed (never admit).
    """
    try:
        raw = classifier(ticket)
    except Exception as exc:  # noqa: BLE001 — any failure is fail-closed by design
        return _fail_closed(ticket, commit_sha, None, f"classifier raised: {exc!r}")

    if not isinstance(raw, Mapping):
        return _fail_closed(ticket, commit_sha, None, "classifier returned non-mapping result")

    disposition = raw.get("disposition")
    reason_code = raw.get("reason_code")
    reason = raw.get("reason")

    if disposition not in ("admit", "reject"):
        return _fail_closed(ticket, commit_sha, raw, f"invalid disposition: {disposition!r}")
    if not isinstance(reason_code, str) or reason_code not in ALL_REASON_CODES:
        return _fail_closed(ticket, commit_sha, raw, f"invalid reason_code: {reason_code!r}")
    if not isinstance(reason, str) or not reason.strip():
        return _fail_closed(ticket, commit_sha, raw, "missing reason")

    # disposition <-> reason_code must be consistent.
    if disposition == "admit" and reason_code != ADMIT_REASON_CODE:
        return _fail_closed(
            ticket, commit_sha, raw, f"admit with non-admit reason_code: {reason_code!r}"
        )
    if disposition == "reject" and reason_code not in REJECT_REASON_CODES:
        return _fail_closed(
            ticket, commit_sha, raw, f"reject with non-reject reason_code: {reason_code!r}"
        )

    # Confidence gate + reviewed_ref binding. An ADMIT must carry a present,
    # finite, in-range confidence >= MIN_CONFIDENCE AND a concrete ticket ref +
    # a valid hex commit SHA — otherwise it would feed autonomous work toward a
    # live-deploy merge under a bogus 'unknown@unknown' provenance. Any shortfall
    # fails closed to needs_info (never admit). A reject need not carry
    # confidence, but a present-but-invalid one still fails closed (safe: hold).
    confidence = raw.get("confidence")
    if disposition == "admit":
        if confidence is None:
            return _fail_closed(ticket, commit_sha, raw, "admit missing confidence")
        if not _confidence_ok(confidence):
            return _fail_closed(
                ticket,
                commit_sha,
                raw,
                f"admit confidence not a finite number in "
                f"[{MIN_CONFIDENCE}, 1.0]: {confidence!r}",
            )
        if _concrete_ticket_ref(ticket) is None:
            return _fail_closed(ticket, commit_sha, raw, "admit missing ticket reference")
        if _valid_sha(commit_sha) is None:
            return _fail_closed(
                ticket, commit_sha, raw, f"admit with invalid commit SHA: {commit_sha!r}"
            )
        admit_confidence: float | None = float(confidence)
    else:
        if confidence is not None and not _confidence_ok(confidence):
            return _fail_closed(
                ticket, commit_sha, raw, f"low or invalid confidence: {confidence!r}"
            )
        admit_confidence = None

    return TriageVerdict(
        disposition=disposition,
        reason=reason.strip(),
        reason_code=reason_code,
        evidence=_coerce_evidence(raw.get("evidence")),
        zoe_kind=_carried_zoe_kind(ticket, raw),
        reviewed_ref=_reviewed_ref(ticket, commit_sha),
        confidence=admit_confidence,
    )


def disposition_action(verdict: TriageVerdict) -> DispositionAction | None:
    """Board params for a REJECT verdict; ``None`` for an admit (no mutation).

    Mirrors ``multica_apply_triage_dispositions.py``: label is the reason_code
    with underscores hyphenated, note is ``Triage: <reason_code> - <reason>``,
    and status comes from :data:`_CLOSE_STATUS`. ``needs_info`` returns a HOLD
    (``target_status=None``) so a fail-closed verdict never auto-closes a ticket.
    """
    if verdict.disposition != "reject":
        return None
    code = verdict.reason_code
    return DispositionAction(
        reason_code=code,
        target_status=_CLOSE_STATUS.get(code),
        note=f"Triage: {code} - {verdict.reason}",
        label=code.replace("_", "-"),
    )


def run_triage(
    ticket: Mapping[str, Any],
    *,
    classifier: Classifier,
    commit_sha: str,
) -> TriageVerdict | None:
    """Flag-gated entrypoint: returns ``None`` when the feature is OFF (default),
    else the :class:`TriageVerdict`. This is the single place the flag is read,
    so the module stays inert until ``ZOE_MULTICA_TRIAGE_JUDGE`` is enabled."""
    if not triage_judge_enabled():
        return None
    return judge_ticket(ticket, classifier=classifier, commit_sha=commit_sha)
