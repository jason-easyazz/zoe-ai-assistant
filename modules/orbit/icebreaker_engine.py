"""Orbit icebreaker engine — rule cascade, strongest signal wins."""
from __future__ import annotations
from typing import Optional
import random


def generate(a: dict, b: dict) -> str:
    """Generate the best icebreaker line for a match pair."""

    interests_a = set(a.get("interests", []))
    interests_b = set(b.get("interests", []))
    intensity_a = a.get("interest_intensity", {})
    intensity_b = b.get("interest_intensity", {})
    values_a = set(a.get("values", []))
    values_b = set(b.get("values", []))
    activities_a = set(a.get("activities", []))
    activities_b = set(b.get("activities", []))
    pa = a.get("personality") or {}
    pb = b.get("personality") or {}

    # 1. Shared high-intensity interests (both double-tapped)
    high_shared = [
        i for i in interests_a & interests_b
        if intensity_a.get(i, 1) == 2 and intensity_b.get(i, 1) == 2
    ]
    if high_shared:
        interest = random.choice(high_shared)
        return f"You're both seriously into {interest}"

    # 2. Shared values (2+)
    shared_values = list(values_a & values_b)
    if len(shared_values) >= 2:
        v1, v2 = shared_values[0], shared_values[1]
        return f"You both put {v1.title()} and {v2.title()} in your top 3"
    if len(shared_values) == 1:
        return f"You both value {shared_values[0].title()}"

    # 3. Shared standard interests
    shared_interests = list(interests_a & interests_b)
    if shared_interests:
        interest = random.choice(shared_interests)
        return f"You both have {interest} in common"

    # 4. Same activity
    shared_activities = list(activities_a & activities_b)
    if shared_activities:
        activity = random.choice(shared_activities)
        return f"You're both up for {activity} — say hey!"

    # 5. Personality resonance
    if pa and pb:
        o_a, o_b = pa.get("O", 3), pb.get("O", 3)
        a_a, a_b = pa.get("A", 3), pb.get("A", 3)
        n_a, n_b = pa.get("N", 3), pb.get("N", 3)

        if o_a >= 4 and o_b >= 4:
            return "You'd both rather have a real conversation than small talk"
        if a_a >= 4 and a_b >= 4:
            return "You're both the kind of person people enjoy being around"
        if abs(n_a - n_b) >= 2:
            return "You'd probably balance each other out nicely"

    # 6. Same zone
    zone_a = a.get("zone")
    zone_b = b.get("zone")
    if zone_a and zone_b and zone_a == zone_b:
        zone_label = zone_a.replace("-", " ").title()
        return f"You're both in the {zone_label} area"

    # 7. Fallback pool
    fallbacks = [
        "Sometimes the best nights are the ones you didn't plan",
        "Two people who don't know what they're missing yet",
        "Every great story starts with a hello",
        "You never know until you say hey",
    ]
    return random.choice(fallbacks)


def get_connection_icebreaker(scanner: dict, scanned: dict) -> str:
    """Shorter icebreaker for the QR scan connect page."""
    shared_interests = set(scanner.get("interests", [])) & set(scanned.get("interests", []))
    shared_values = set(scanner.get("values", [])) & set(scanned.get("values", []))

    if shared_interests:
        interest = next(iter(shared_interests))
        return f"You both have {interest} in common"
    if shared_values:
        value = next(iter(shared_values))
        return f"You both value {value.title()}"
    return "You're both in the same orbit tonight"
