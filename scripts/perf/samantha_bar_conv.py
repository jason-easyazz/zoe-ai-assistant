#!/usr/bin/env python3
"""Conversation-quality scenario pack for the Samantha bar (S13-S15) — FIXTURES + PURE SCORERS.

Three classes observed live on 2026-10-04 ("it doesn't work like a human
assistant"), each with a flag-dark fix, expressed in the bar's own shape
(synthetic seed turn + ask + deterministic scorer) so they can be wired into
``samantha_bar.py`` as the next scenario ids:

  S13  own-fact question never answered by the clock     ZOE_OWN_FACT_PRECEDENCE
  S14  "are you sure" backs the claim up, never doubles   ZOE_VERIFY_ON_CHALLENGE
       down
  S15  recall is answered, not narrated                   ZOE_STRIP_NARRATION

NOT wired into ``samantha_bar.SCENARIO_IDS`` on purpose: adding ids changes the
plan, the baseline contract ("a scenario is red only when it passed before") and
the run time, and each one needs its flag flipped on the live service first. This
module is import-safe (stdlib only, no network, no service) and pinned by
``tests/unit/test_samantha_bar_conv.py``. Wiring recipe: docs/knowledge/
conversation-quality-classes.md. Facts are synthetic and distinctive.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]

# ── S13: "when is my birthday" -> "It's 7:50 AM." ────────────────────────────
SAY_BIRTHDAY = "Just so you know, my birthday is the 12th of March."
ASK_BIRTHDAY = "When is my birthday?"
S13_NEEDLES = ("12|twelfth", "march")  # the day AND the month must come back
_CLOCK_RE = re.compile(r"\b\d{1,2}:\d{2}\b|\b\d{1,2}\s?(?:am|pm)\b|\bit['’]?s\s+(?:currently\s+)?\d", re.IGNORECASE)

# ── S14: "are you sure" -> "I'm pretty sure" ─────────────────────────────────
ASK_TRIVIA = "Who won the 1987 grand final?"
ASK_CHALLENGE = "Are you sure?"
_DOUBLE_DOWN_RE = re.compile(
    r"\b(?:i['’]?m\s+(?:pretty\s+|quite\s+|very\s+)?sure|yes,?\s+i['’]?m\s+sure|"
    r"i\s+am\s+(?:pretty\s+|quite\s+|very\s+)?sure|definitely|absolutely|without\s+a\s+doubt)\b",
    re.IGNORECASE,
)
_DOMAIN_RE = re.compile(r"\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:com|org|net|edu|gov|io|co|au|uk|nz)\b",
                        re.IGNORECASE)
_CANT_CHECK_RE = re.compile(r"\b(?:can['’]?t|cannot|couldn['’]?t|unable\s+to)\s+(?:check|confirm|verify)\b",
                            re.IGNORECASE)

# ── S15: "Who am I" -> "I'll check what I've got on file about you." ─────────
SAY_FACT_A = "I swim every morning before work."
SAY_FACT_B = "My favourite tea is lapsang souchong."
ASK_WHO = "Who am I?"
S15_NEEDLES = ("swim", "lapsang")  # at least one must come back

ASKS = {
    "S13": {"seed": SAY_BIRTHDAY, "ask": ASK_BIRTHDAY, "flag": "ZOE_OWN_FACT_PRECEDENCE"},
    "S14": {"seed": None, "ask": ASK_TRIVIA, "challenge": ASK_CHALLENGE, "flag": "ZOE_VERIFY_ON_CHALLENGE"},
    "S15": {"seed": (SAY_FACT_A, SAY_FACT_B), "ask": ASK_WHO, "flag": "ZOE_STRIP_NARRATION"},
}


def _contains_all(text: str, needles: tuple[str, ...]) -> bool:
    low = (text or "").lower()
    return all(n in low for n in needles)


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    low = (text or "").lower()
    return any(n in low for n in needles)


def score_s13(reply: str) -> tuple[str, dict]:
    """PASS: the stored date comes back and the clock was not read out.
    FAIL: a clock reading, or no stored fact."""
    ev: dict[str, Any] = {"method": "deterministic", "clock": bool(_CLOCK_RE.search(reply or "")),
                          "has_fact": _contains_all(reply, ("march",))
                          and _contains_any(reply, ("12", "twelf"))}
    if ev["clock"] and not ev["has_fact"]:
        return "FAIL", {**ev, "why": "answered with the time of day, not the stored birthday"}
    if not ev["has_fact"]:
        return "FAIL", {**ev, "why": "the stored birthday is not in the reply"}
    return "PASS", {**ev, "why": "stored date returned, no clock reading"}


def score_s14(reply: str) -> tuple[str, dict]:
    """PASS: the reply cites a source domain or honestly says it cannot check.
    FAIL: it doubles down ("I'm pretty sure") with neither."""
    ev: dict[str, Any] = {"method": "deterministic", "domain": bool(_DOMAIN_RE.search(reply or "")),
                          "cant_check": bool(_CANT_CHECK_RE.search(reply or "")),
                          "double_down": bool(_DOUBLE_DOWN_RE.search(reply or ""))}
    if ev["domain"] or ev["cant_check"]:
        return "PASS", {**ev, "why": "cited a source or said it cannot check"}
    if ev["double_down"]:
        return "FAIL", {**ev, "why": "doubled down without a check"}
    return "FAIL", {**ev, "why": "neither cited a source nor said it cannot check"}


def _narration():
    spec = importlib.util.spec_from_file_location("conv_narration_filter",
                                                  REPO / "services/zoe-data/narration_filter.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # stdlib-only module
    return mod


def score_s15(reply: str) -> tuple[str, dict]:
    """PASS: a stored fact comes back and the reply does not open by announcing
    the lookup. FAIL: a leading "I'll check…/Let me look…" sentence."""
    nf = _narration()
    stripped = nf.strip_leading_narration(reply or "")
    narrated = stripped != (reply or "")
    ev: dict[str, Any] = {"method": "deterministic", "narrated": narrated,
                          "has_fact": _contains_any(reply, S15_NEEDLES)}
    if narrated:
        return "FAIL", {**ev, "why": "opened by narrating the lookup"}
    if not ev["has_fact"]:
        return "FAIL", {**ev, "why": "no stored fact came back"}
    return "PASS", {**ev, "why": "answered directly with a stored fact"}
