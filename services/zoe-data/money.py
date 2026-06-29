"""money.py — exact money handling for transactions.

Transaction amounts are stored EXACTLY as integer cents (the ``amount_cents``
column) instead of being summed as floats. Float dollars/REAL drift under
``SUM(amount)`` (e.g. 0.10 + 0.20 != 0.30 exactly), so weekly summaries could be
off by a cent or more. Integer cents sum exactly.

Representation split:
  - **Dollars (float)** is the external/boundary form — API requests/responses
    and the voice command strings. Callers keep seeing dollars.
  - **Cents (int)** is the internal store + aggregation form.

Conversions use ``Decimal`` with ``ROUND_HALF_UP`` so a clean two-decimal dollar
value maps to exactly its cents (19.99 -> 1999) with no float-repr noise, and
round-trips back to the same dollars.
"""
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

_ONE = Decimal(1)
_CENT = Decimal("0.01")


def _to_decimal(amount) -> Decimal:
    """Coerce int/float/str/Decimal dollars to an exact, finite Decimal.

    Floats are routed through ``str()`` so we capture the shortest decimal that
    round-trips the float (0.30000000000000004 -> "0.30000000000000004" then a
    clean quantize) rather than ``Decimal(float)``'s full binary expansion.
    """
    try:
        d = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"invalid money amount: {amount!r}") from exc
    if not d.is_finite():
        raise ValueError(f"non-finite money amount: {amount!r}")
    return d


def to_cents(amount) -> int:
    """Convert a dollar amount to an exact integer number of cents.

    Rounds half-up at the cent. Raises ``ValueError`` on NaN/inf or unparseable
    input.
    """
    return int((_to_decimal(amount) * 100).quantize(_ONE, rounding=ROUND_HALF_UP))


def to_dollars(cents) -> float:
    """Convert integer cents back to a dollar float rounded to two places.

    ``None`` (e.g. a NULL SUM over zero rows) maps to ``0.0``.
    """
    if cents is None:
        return 0.0
    return float((Decimal(int(cents)) / 100).quantize(_CENT, rounding=ROUND_HALF_UP))


def normalize_dollars(amount) -> float:
    """Snap a dollar amount to its canonical two-decimal value via cents.

    Guarantees the returned dollars equal ``to_cents(amount) / 100`` exactly, so
    the boundary value and the stored cents never disagree.
    """
    return to_dollars(to_cents(amount))
