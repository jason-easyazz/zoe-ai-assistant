"""Closed diagnostic vocabulary: never promote an arbitrary string to a report code."""
import hashlib

STATUSES = frozenset({'done', 'unconfirmed', 'refused', 'invalid', 'unavailable', 'unknown'})


def code(value, allowed):
    return value if isinstance(value, str) and value in allowed else 'unknown'


def identifier(value):
    """Opaque stable ID for an untrusted label (including argument names and routine items)."""
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


def proposal_record(turn, tool, out, ms):
    return {'turn': turn, 'tool': identifier(tool), 'status': code(out.status, STATUSES),
            'receipt': out.receipt if type(out.receipt) is int else None, 'hop_ms': round(ms, 3)}
