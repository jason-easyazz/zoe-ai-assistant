"""Closed diagnostic vocabulary: never promote an arbitrary string to a report code."""
import hashlib

STATUSES = frozenset({'done', 'unconfirmed', 'refused', 'invalid', 'unavailable', 'unknown'})


def code(value, allowed):
    return value if isinstance(value, str) and value in allowed else 'unknown'


def identifier(value):
    """Opaque stable ID for an untrusted label (including argument names and routine items)."""
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


#: the bridge seam's own refusal codes that may appear in a log (kernel reason strings never do)
SEAM_CODES = frozenset({'stale_turn'})


def proposal_record(turn, tool, out, ms, *, turn_id=None):
    rec = {'turn': turn, 'turn_id': turn_id if isinstance(turn_id, str) else None, 'tool': identifier(tool),
           'status': code(out.status, STATUSES),
           'receipt': out.receipt if type(out.receipt) is int else None, 'hop_ms': round(ms, 3)}
    if out.reason in SEAM_CODES:
        rec['code'] = out.reason
    return rec
