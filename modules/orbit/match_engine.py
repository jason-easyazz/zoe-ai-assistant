"""Orbit matching algorithm — weighted multi-factor scoring."""
from __future__ import annotations
from typing import Optional

WEIGHTS = {
    "intent":      0.30,
    "interests":   0.15,
    "activity":    0.10,
    "values":      0.15,
    "personality": 0.10,
    "proximity":   0.10,
    "mutual":      0.10,  # placeholder — always 0 in MVP
}

INTENT_COMPAT = {
    ("social",   "social"):   1.0,
    ("activity", "activity"): 1.0,
    ("romantic", "romantic"): 1.0,
    ("social",   "activity"): 0.5,
    ("activity", "social"):   0.5,
}


def intents_compatible(a: str, b: str) -> bool:
    return INTENT_COMPAT.get((a, b), 0.0) > 0


def intent_score(a: str, b: str) -> float:
    return INTENT_COMPAT.get((a, b), 0.0)


def jaccard(set_a: list, set_b: list) -> float:
    if not set_a or not set_b:
        return 0.0
    sa, sb = set(set_a), set(set_b)
    union = sa | sb
    if not union:
        return 0.0
    return len(sa & sb) / len(union)


def weighted_jaccard(
    interests_a: list[str],
    interests_b: list[str],
    intensity_a: dict[str, int],
    intensity_b: dict[str, int],
) -> float:
    """Jaccard with intensity weighting — shared high-intensity items score higher."""
    if not interests_a or not interests_b:
        return 0.0
    sa, sb = set(interests_a), set(interests_b)
    shared = sa & sb
    all_items = sa | sb
    if not all_items:
        return 0.0
    # weight shared items by average intensity of both parties
    shared_weight = sum(
        ((intensity_a.get(item, 1) + intensity_b.get(item, 1)) / 2)
        for item in shared
    )
    total_weight = sum(
        ((intensity_a.get(item, 1) + intensity_b.get(item, 1)) / 2)
        for item in all_items
    )
    return shared_weight / total_weight if total_weight else 0.0


def personality_score(
    pa: Optional[dict],
    pb: Optional[dict],
) -> float:
    """Score personality compatibility using per-trait similarity/complementarity.

    Returns 0.0 if either party has no personality data.
    """
    if not pa or not pb:
        return 0.0

    def sim(x: float, y: float) -> float:
        """Similarity: 1 when equal, 0 when max distance (4 points apart)."""
        return 1.0 - abs(x - y) / 4.0

    def comp(x: float, y: float) -> float:
        """Complementarity: 1 when maximally different, scaled."""
        return abs(x - y) / 4.0

    def mid(x: float, y: float) -> float:
        """Soft complementarity: best when one is mid-high (3–4) and other is mid."""
        diff = abs(x - y)
        avg = (x + y) / 2
        # Reward moderate difference with reasonable averages
        return (1.0 - diff / 4.0) * 0.5 + (avg / 5.0) * 0.5

    O_score = sim(pa.get("O", 3), pb.get("O", 3))
    C_score = sim(pa.get("C", 3), pb.get("C", 3))
    E_score = mid(pa.get("E", 3), pb.get("E", 3))
    A_score = sim(pa.get("A", 3), pb.get("A", 3))
    N_score = comp(pa.get("N", 3), pb.get("N", 3))

    return (O_score * 0.25 + C_score * 0.20 + E_score * 0.20 + A_score * 0.25 + N_score * 0.10)


def zone_score(zone_a: Optional[str], zone_b: Optional[str]) -> float:
    if not zone_a or not zone_b:
        return 0.5  # unknown = neutral
    return 1.0 if zone_a == zone_b else 0.2


def score_pair(a: dict, b: dict) -> Optional[float]:
    """Compute match score between two checkin dicts. Returns None if incompatible."""
    if not intents_compatible(a["intent"], b["intent"]):
        return None

    # Blocked/reported — skip
    if b["id"] in a.get("blocked", []) or a["id"] in b.get("blocked", []):
        return None

    has_personality = bool(a.get("personality")) and bool(b.get("personality"))
    w = dict(WEIGHTS)
    if not has_personality:
        # Redistribute personality weight proportionally
        extra = w.pop("personality")
        distributable = ["intent", "interests", "activity", "values", "proximity"]
        per = extra / len(distributable)
        for k in distributable:
            w[k] += per

    s = 0.0
    s += w["intent"]    * intent_score(a["intent"], b["intent"])
    s += w["interests"] * weighted_jaccard(
        a.get("interests", []), b.get("interests", []),
        a.get("interest_intensity", {}), b.get("interest_intensity", {}),
    )
    s += w["activity"]  * jaccard(a.get("activities", []), b.get("activities", []))
    s += w["values"]    * jaccard(a.get("values", []), b.get("values", []))
    s += w["proximity"] * zone_score(a.get("zone"), b.get("zone"))
    s += w.get("mutual", 0) * 0.0

    if has_personality:
        s += w["personality"] * personality_score(a.get("personality"), b.get("personality"))

    return round(s, 4)


def top_matches(checkin: dict, pool: list[dict], n: int = 3) -> list[tuple[dict, float]]:
    """Return up to n best matches for a checkin from the active pool."""
    scored = []
    for candidate in pool:
        if candidate["id"] == checkin["id"]:
            continue
        if candidate.get("checked_out"):
            continue
        # Visibility: low-key users only appear to high-score matches
        if candidate.get("visibility") == "low-key":
            raw = score_pair(checkin, candidate)
            if raw is None or raw < 0.5:
                continue
            scored.append((candidate, raw))
        else:
            raw = score_pair(checkin, candidate)
            if raw is not None:
                scored.append((candidate, raw))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:n]
