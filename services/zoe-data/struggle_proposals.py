"""STRUGGLE PROPOSALS (Phase 2 of the self-correction loop): the night pass turns a week of ``struggle_events`` into a short list of
recurring problems, each with a candidate FIX CLASS, for the admin to accept or reject. Contract:
``docs/architecture/struggle-detector.md`` -> *Phase 2*. Detects nothing itself (``struggle_detector`` does) and APPLIES nothing.

    struggle_events --(code: group, count, threshold)--> clusters --> struggle_proposals (+ evidence ids)
                                                                       |-- summary: a template, or the NIGHT MODEL's rewording of the same counts
                                                                       |-- morning brief line (mode ``on``) / admin API / CLI
                                                                       `-- accept: router_example -> router self-train QUEUE (Phase 3 reads it); others -> a task stub

``ZOE_STRUGGLE_PROPOSALS`` = off | **shadow (default)** | on, read per call.
  * off    - the night job is a no-op; stored rows stay readable and decidable through the admin API;
  * shadow - the night job clusters and stores proposals; the admin API / CLI list them; NOTHING user-facing;
  * on     - shadow + one line in the admin's morning brief.

The four kinds (from the events' suggested fix type; ``router_latency`` is a sidecar fault, not a household problem, so it is counted
and skipped):
  * ``router_example`` - a candidate training pair for the router self-train loop. LABELLED BY WHAT HAPPENED NEXT: a failed turn followed,
                         in the same conversation, by a re-ask / rephrase / correction that a FAST domain then answered (and that
                         follow-up was not itself a struggle) takes that domain as its label (``label_source`` = followup, strong).
                         A router-training event with no such follow-up is a weaker proposal labelled with the router's own belief
                         (``label_source`` = router). The detector records the follow-up's domain in the event's text-free detail;
  * ``intent_gap``     - fix ``capability_gap``: a handler is missing or answers the wrong thing;
  * ``answer_quality`` - fix ``answer_quality`` / ``verbosity``: a prompt or reply-format issue;
  * ``stt_term``       - fix ``stt`` / ``stt_snapped``: a keyterm / homophone candidate.

CODE DOES THE GROUPING. A cluster is a (kind, text-free key) pair; it becomes a proposal only when it recurred (``min_episodes``
separate episodes - events of one user within ``EPISODE_GAP_S`` are ONE episode - on at least ``MIN_USER_DAYS`` distinct user-days,
mean event confidence >= ``MIN_MEAN_CONFIDENCE``). The model never groups, scores, labels or decides: it may reword the counts as one
sentence (``summarise``), validated against the facts it was given, and a sentence that does not validate is replaced by the template.

PRIVACY - the point of the design: a proposal row carries COUNTS and a text-free shape only (``<domain>|<lead>:<time>:<anchor>``,
a reason code). NEVER a household utterance, and the night model sees only those counts. A ``router_example`` proposal's actual
training text is NOT copied anywhere: ``queued_router_examples`` (the Phase 3 seam) resolves it at READ time from the member's own
``struggle_events.summary`` (already PII-scrubbed, member-only, forget-gated), so every erase that reaches ``struggle_events`` reaches
the example with it. Evidence ids link a proposal to its events; ``forget_events`` / ``forget_user`` (called by
``struggle_detector.erase_entity`` / ``delete_user`` BEFORE the events go, fail closed) drop the links, recount, reset the sentence to
the template, and delete a proposal left with no evidence; ``reconcile`` and every read join back to ``struggle_events`` so an orphaned
link can never show a proposal or an example.

NOTHING APPLIES ITSELF. ``accept`` records the decision and the follow-up (a queue marker or a task stub); only ``mark_applied`` (the
Phase 3 seam, an ``accepted`` proposal only) moves a row to ``applied``. No code path in the night job decides.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Optional

import struggle_signals as sig
from struggle_detector import is_guest

logger = logging.getLogger("struggle_proposals")

ENV = "ZOE_STRUGGLE_PROPOSALS"
MODES = ("off", "shadow", "on")
_OFF = frozenset({"off", "0", "false", "no", "disabled"})
_ON = frozenset({"on", "1", "true", "yes", "enforce"})

KIND_ROUTER_EXAMPLE = "router_example"
KIND_INTENT_GAP = "intent_gap"
KIND_ANSWER_QUALITY = "answer_quality"
KIND_STT_TERM = "stt_term"
KINDS = (KIND_ROUTER_EXAMPLE, KIND_INTENT_GAP, KIND_ANSWER_QUALITY, KIND_STT_TERM)

PROPOSED, ACCEPTED, REJECTED, APPLIED = "proposed", "accepted", "rejected", "applied"
STATUSES = (PROPOSED, ACCEPTED, REJECTED, APPLIED)

#: event fix type -> proposal kind. ``router_latency`` is deliberately absent (an ops fault of the router sidecar).
KIND_OF_FIX = {
    sig.FIX_ROUTER_TRAINING: KIND_ROUTER_EXAMPLE,
    sig.FIX_CAPABILITY_GAP: KIND_INTENT_GAP,
    sig.FIX_ANSWER_QUALITY: KIND_ANSWER_QUALITY,
    sig.FIX_VERBOSITY: KIND_ANSWER_QUALITY,
    sig.FIX_STT: KIND_STT_TERM,
    sig.FIX_STT_SNAPPED: KIND_STT_TERM,
}

DAY_S = 86400.0
#: events of ONE user closer than this are one episode (a re-ask chain is one struggle, not five)
EPISODE_GAP_S = 600.0
MIN_USER_DAYS = 2
MIN_MEAN_CONFIDENCE = 0.6
MAX_PER_NIGHT = 20
REJECT_SUPPRESS_DAYS = 30.0
ROW_LIMIT = 50000
STORE_TIMEOUT_S = 15.0
FOLLOWUP_ROUTER_QUEUE = "router_selftrain_queue"
FOLLOWUP_TASK_STUB = "task_stub"
#: the follow-up marker a router_example accept writes; Phase 3 (the router self-train loop) is its only consumer
ROUTER_QUEUE_CONSUMER = "ZOE_ROUTER_SELFTRAIN"
_IN_CHUNK = 400


# ── configuration (per-call env reads) ───────────────────────────────────────

def mode() -> str:
    """``off`` | ``shadow`` (default: unset or unrecognised) | ``on``."""
    import os

    raw = (os.environ.get("ZOE_STRUGGLE_PROPOSALS") or "").strip().lower()
    if raw in _OFF:
        return "off"
    if raw in _ON:
        return "on"
    return "shadow"


def lookback_days() -> float:
    from typed_env import env_float

    return max(1.0, min(30.0, env_float("ZOE_STRUGGLE_PROPOSAL_DAYS", 7.0)))


def min_episodes() -> int:
    from typed_env import env_int

    return max(2, min(50, env_int("ZOE_STRUGGLE_PROPOSAL_MIN_EPISODES", 3)))


# ── pure: events -> clusters ─────────────────────────────────────────────────

_TAG_RE = re.compile(r"^[a-z0-9_.:-]{1,40}$")


def parse_detail(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def _tag(value: Any) -> str:
    """A controlled-vocabulary tag (a reason code, an intent tag) or ''. Anything else - free text especially - is dropped."""
    s = str(value or "").strip().lower()
    return s if _TAG_RE.match(s) else ""


def is_generic(pattern: str) -> bool:
    """An unrouted turn with no time orientation and no anchor: its shape says nothing about what was asked."""
    dom, _, shape = (pattern or "").partition("|")
    return dom == "unrouted" and shape.endswith(":none:none")


def kind_of(row: dict) -> Optional[str]:
    """The kind a row's suggested FIX TYPE maps to (a failed->success follow-up can override it: ``classify``)."""
    return KIND_OF_FIX.get(str(row.get("fix_type") or ""))


SOURCE_FOLLOWUP = "followup"      # the label is the fast domain that SUCCESSFULLY answered the follow-up turn (strong)
SOURCE_ROUTER = "router"          # the label is the router's own belief about the failed turn (weak: no follow-up proved it)


def followup_label(row: dict, detail: Optional[dict] = None) -> str:
    """The fast domain a FAILED turn should have gone to, read from what happened next: a pair event (the failed turn + the turn that
    revealed it) whose revealing turn was answered by a fast domain, where the failed turn went to the brain or to a different domain.
    '' when there is no such proof. The detector records ``next_domain`` only for a pair by one identity."""
    if not row.get("prev_turn_id"):
        return ""
    d = detail if detail is not None else parse_detail(row.get("detail"))
    label = str(d.get("next_domain") or "")
    if label not in sig.FAST_DOMAINS:
        return ""
    brain = str(row.get("path_served") or "").startswith("brain")
    # compare with what actually SERVED the failed turn (the router's prediction can differ from the handler that answered)
    return label if (brain or str(row.get("domain_served") or "") != label) else ""


def struck_turns(rows: Iterable[dict]) -> frozenset:
    """Turn ids that were THEMSELVES a struggle: re-asked / corrected later (some event's failed turn), or flagged on their own."""
    out = set()
    for r in rows:
        if r.get("prev_turn_id"):
            out.add(str(r["prev_turn_id"]))
        elif r.get("turn_id"):
            out.add(str(r["turn_id"]))
    return frozenset(out)


def classify(row: dict, struck: frozenset = frozenset()) -> tuple:
    """``(kind, key, label, source, skip_reason)`` for one event row. ``kind`` None = not clusterable, ``skip_reason`` says why.

    A failed turn whose follow-up WORKED (answered by a fast domain, and that follow-up was not itself a struggle) is a ``router_example``
    labelled with that domain, whatever the event's own fix type said. Without such proof, a router-training event is a weaker
    router_example labelled with the router's own belief; every other fix type maps as before."""
    pattern = str(row.get("pattern") or "")
    words_ok = not row.get("is_guest") and bool(str(row.get("summary") or "").strip())
    label = followup_label(row)
    if label and str(row.get("turn_id") or "") not in struck:
        if not words_ok:
            return None, None, "", "", "unkeyed"       # a guest's / wordless pair: nothing for a training pair to carry
        return KIND_ROUTER_EXAMPLE, f"{pattern}=>{label}/{SOURCE_FOLLOWUP}", label, SOURCE_FOLLOWUP, ""
    kind = kind_of(row)
    if kind is None:
        return None, None, "", "", "unmapped"
    if kind == KIND_ROUTER_EXAMPLE:
        dom = str(row.get("domain_predicted") or "")
        if dom not in sig.FAST_DOMAINS or not words_ok:
            return None, None, "", "", "unkeyed"
        return kind, f"{pattern}=>{dom}/{SOURCE_ROUTER}", dom, SOURCE_ROUTER, ""
    key = key_of(row, kind)
    if key is None:
        return None, None, "", "", "unkeyed"
    return kind, key, "", "", ""


def key_of(row: dict, kind: str) -> Optional[str]:
    """The TEXT-FREE key a non-router row clusters under, or None when the row cannot support a proposal of ``kind``."""
    pattern = str(row.get("pattern") or "")
    if kind == KIND_INTENT_GAP:
        return None if (not pattern or is_generic(pattern)) else pattern
    if kind == KIND_ANSWER_QUALITY:
        return pattern or None
    if kind == KIND_STT_TERM:
        d = parse_detail(row.get("detail"))
        reasons = d.get("reasons") if isinstance(d.get("reasons"), list) else []
        reason = _tag(reasons[0]) if reasons else ""
        intent = _tag(d.get("intent"))
        return "stt:" + (reason or "misheard") + (f":{intent}" if intent else "")
    return None


def fingerprint(kind: str, key: str) -> str:
    return hashlib.sha1(f"{kind}|{key}".encode("utf-8")).hexdigest()[:16]


def episodes_of(pairs: Iterable[tuple[str, float]]) -> int:
    """Separate episodes among (user, ts) pairs: per user, a gap over ``EPISODE_GAP_S`` starts a new one."""
    by_user: dict[str, list[float]] = {}
    for u, ts in pairs:
        by_user.setdefault(u, []).append(float(ts))
    n = 0
    for ts_list in by_user.values():
        ts_list.sort()
        n += 1 + sum(1 for a, b in zip(ts_list, ts_list[1:]) if b - a > EPISODE_GAP_S)
    return n


def stats_of(links: Iterable[tuple[str, float, str]]) -> dict[str, Any]:
    """Counts of (user, ts, signal) links: episodes, events, users, user_days, first/last seen, per-signal counts."""
    links = list(links)
    signals: dict[str, int] = {}
    for _, _, s in links:
        signals[s] = signals.get(s, 0) + 1
    return {
        "episodes": episodes_of((u, ts) for u, ts, _ in links),
        "events": len(links),
        "users": len({u for u, _, _ in links}),
        "user_days": len({(u, int(ts // DAY_S)) for u, ts, _ in links}),
        "first_seen": min((ts for _, ts, _ in links), default=0.0),
        "last_seen": max((ts for _, ts, _ in links), default=0.0),
        "signals": dict(sorted(signals.items())),
    }


def recurs(stats: dict, *, need_episodes: Optional[int] = None) -> bool:
    """Did it happen often enough, on enough separate days / by enough people, to be a pattern rather than one bad moment?"""
    return stats["episodes"] >= (need_episodes or min_episodes()) and stats["user_days"] >= MIN_USER_DAYS


def eligible(stats: dict, mean_conf: float, *, need_episodes: Optional[int] = None) -> bool:
    return recurs(stats, need_episodes=need_episodes) and mean_conf >= MIN_MEAN_CONFIDENCE


def confidence_of(mean_conf: float, stats: dict) -> float:
    """0..0.99: the events' own mean confidence, scaled up by how often (saturating at 8 episodes) and over how many user-days (saturating at 4)."""
    often = min(1.0, stats["episodes"] / 8.0)
    spread = min(1.0, max(0, stats["user_days"] - 1) / 3.0)
    return round(min(0.99, mean_conf * (0.55 + 0.45 * often) * (0.85 + 0.15 * spread)), 3)


@dataclass
class Cluster:
    fingerprint: str
    kind: str
    key: str
    domain: str
    label: str
    mean_conf: float
    confidence: float
    stats: dict
    links: list = field(default_factory=list)       # (event_id, user_id, ts, signal)
    reasons: dict = field(default_factory=dict)
    label_source: str = ""

    @property
    def pattern(self) -> str:
        """The text-free shape (a router key carries its label after ``=>``)."""
        return self.key.split("=>", 1)[0]


def cluster(rows: Iterable[dict], *, min_ep: Optional[int] = None) -> tuple[list[Cluster], dict[str, int]]:
    """Group event rows into clusters that clear the recurrence thresholds. Pure. Returns (clusters sorted best first, skipped counts).

    ``skipped``: ``unmapped`` (a fix type with no proposal kind, e.g. router_latency), ``unkeyed`` (the row cannot support its kind: a
    guest router row, a generic intent gap), ``synthetic`` (harness / test users), ``below_threshold`` (clusters that did not recur enough)."""
    skipped = {"unmapped": 0, "unkeyed": 0, "synthetic": 0, "below_threshold": 0}
    groups: dict[str, dict] = {}
    rows = list(rows)
    struck = struck_turns(rows)
    for r in rows:
        uid = str(r.get("user_id") or "")
        if not r.get("is_guest") and _synthetic(uid):
            skipped["synthetic"] += 1
            continue
        kind, key, label, source, why = classify(r, struck)
        if kind is None:
            skipped[why] += 1
            continue
        fp = fingerprint(kind, key)
        g = groups.get(fp)
        if g is None:
            g = groups[fp] = {"kind": kind, "key": key, "rows": [], "domain": str(r.get("domain_predicted") or ""), "label": label, "source": source}
        g["rows"].append(r)
    out: list[Cluster] = []
    for fp, g in groups.items():
        rs = g["rows"]
        links = [(str(r["id"]), "guest" if r.get("is_guest") else str(r.get("user_id") or ""), float(r["ts"]), str(r["signal"])) for r in rs]
        st = stats_of((u, ts, s) for _, u, ts, s in links)
        mean = sum(float(r.get("confidence") or 0.0) for r in rs) / len(rs)
        if not eligible(st, mean, need_episodes=min_ep):
            skipped["below_threshold"] += 1
            continue
        reasons: dict[str, int] = {}
        for r in rs:
            rl = parse_detail(r.get("detail")).get("reasons")
            for x in (rl if isinstance(rl, list) else [])[:3]:
                if _tag(x):
                    reasons[_tag(x)] = reasons.get(_tag(x), 0) + 1
        dom = g["domain"] if (g["domain"] not in ("", "chat") and g["kind"] != KIND_STT_TERM) else ""
        out.append(Cluster(fp, g["kind"], g["key"], dom, g["label"], round(mean, 3), confidence_of(mean, st), st, links,
                           dict(sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:5]), g["source"]))
    out.sort(key=lambda c: (-c.confidence, -c.stats["episodes"], c.fingerprint))
    return out, skipped


def _synthetic(user_id: str) -> bool:
    if is_guest(user_id):
        return False
    try:
        from user_filters import is_synthetic_user

        return bool(is_synthetic_user(user_id))
    except Exception:  # noqa: BLE001
        return False


# ── the sentence: template, or the night model's rewording of the same counts ─

_SIGNAL_PHRASE = {
    sig.RE_ASK: "asked again", sig.REPHRASE: "reworded", sig.CORRECTION: "corrected", sig.CANT_DO: "told it cannot be done",
    sig.FALLTHROUGH: "fell through to the brain", sig.VERBOSE: "answered too long", sig.MISHEAR: "misheard",
    sig.ABANDON: "cut off", sig.NEGATIVE_FEEDBACK: "marked wrong",
}
_LEAD_PHRASE = {"what": "what-questions", "how": "how-questions", "when": "when-questions", "where": "where-questions",
                "who": "who-questions", "why": "why-questions", "which": "which-questions", "is": "yes/no questions", "are": "yes/no questions",
                "can": "can-questions", "could": "could-questions", "do": "do-questions", "does": "do-questions", "did": "did-questions",
                "will": "will-questions", "ask": "requests", "stmt": "statements", "empty": "empty turns"}
_ORIENT_PHRASE = {"future": "about the future", "now": "about right now", "past": "about the past"}
_SUGGESTION = {
    KIND_ROUTER_EXAMPLE: "Candidate: add these phrasings as router training examples labelled {label}{why}.",
    KIND_INTENT_GAP: "Candidate: a missing or incomplete handler for this request.",
    KIND_ANSWER_QUALITY: "Candidate: review the prompt or the reply format for this shape.",
    KIND_STT_TERM: "Candidate: a keyterm or homophone fix for what was misheard.",
}


def shape_words(key: str) -> str:
    """A fixed-vocabulary description of a ``<domain>|<lead>:<time>:<anchor>`` key (or an ``stt:<reason>[:<intent>]`` key). No free text."""
    if key.startswith("stt:"):
        bits = key.split(":")[1:]
        return " / ".join(b.replace("_", " ") for b in bits if b)
    _, _, shape = key.partition("|")
    parts = shape.split(":")
    if len(parts) != 3:
        return "requests"
    lead, orient, anchor = parts
    base = _LEAD_PHRASE.get(lead) or ("commands" if lead.startswith("cmd_") else "requests")
    words = [base]
    if orient in _ORIENT_PHRASE:
        words.append(_ORIENT_PHRASE[orient])
    if anchor not in ("", "none"):
        words.append(f"anchored on '{anchor}'")
    return " ".join(words)


def signal_words(signals: dict) -> str:
    return ", ".join(f"{_SIGNAL_PHRASE.get(s, s.lower())} {n}" for s, n in sorted(signals.items(), key=lambda kv: (-kv[1], kv[0]))) or "no signals"


def facts_of(kind: str, key: str, domain: str, label: str, stats: dict, source: str = "") -> dict:
    """The ONLY thing the night model is shown: counts and fixed-vocabulary words. No utterance, no name, no id."""
    return {"kind": kind, "domain": (label if kind == KIND_ROUTER_EXAMPLE else domain) or "general", "shape": shape_words(key), "signals": dict(stats["signals"]),
            "episodes": stats["episodes"], "days": stats["user_days"], "people": stats["users"],
            "suggestion": _SUGGESTION[kind].format(
                label=label or domain or "the right domain",
                why=(" (a re-ask or correction was then answered by the fast path)" if source == SOURCE_FOLLOWUP
                     else " (the router's own belief, no follow-up proved it)" if source == SOURCE_ROUTER else ""))}


def template_summary(kind: str, key: str, domain: str, label: str, stats: dict, source: str = "") -> str:
    f = facts_of(kind, key, domain, label, stats, source)
    who = f"{f['domain']} " if f["domain"] != "general" else ""
    if kind == KIND_STT_TERM:
        head = f"Speech was misheard ({f['shape']})"
    else:
        head = f"{who.capitalize()}{f['shape']}".strip()
    return (f"{head}: {signal_words(stats['signals'])} over {stats['episodes']} episode{'s' if stats['episodes'] != 1 else ''} "
            f"on {stats['user_days']} user-day{'s' if stats['user_days'] != 1 else ''}. {f['suggestion']}")[:400]


SUMMARY_MAX = 240


def messages_for(facts: dict) -> list[dict]:
    return [
        {"role": "system", "content": (
            "You write ONE short plain-English sentence (at most 35 words) for a household assistant's admin, describing a recurring "
            "problem the assistant had. Use ONLY the facts in the JSON the user sends. Do not quote anyone, and do not add any detail, "
            "name or cause that is not in the facts. NEVER write a digit: write every count as a placeholder that the program fills in - "
            "{episodes}, {days}, {people}, and {n:NAME} for the count of the signal NAME listed in \"signals\" (for example: "
            "'Weather questions were asked again {n:RE_ASK} times over {episodes} episodes.'). Reply with the sentence only.")},
        {"role": "user", "content": json.dumps(facts, sort_keys=True)},
    ]


_WORD_RE = re.compile(r"[a-z]+(?:'[a-z]+)?")
#: the only words a reworded sentence may use besides the ones in its own facts: grammar, plus generic words for "a recurring problem"
#: (no name, place, thing or cause a household could have supplied is in this list)
_FUNCTION_WORDS = frozenset("""
a an the and or but so then than that this these those it its it's they them their there here is are was were be been being has have had do does did
not no never of in on at to for from by with without over under about around after before again also still just only more most many few some
several each every all any one two three four five six seven eight nine ten once twice often repeatedly recurring recurrently regularly
keeps kept keep time times episode episodes day days user users person people household member members assistant zoe
problem problems issue issues pattern patterns struggle struggles struggled struggling trouble difficulty
request requests question questions command commands statement statements phrasing phrasings word words shape shapes turn turns
answer answers answered answering reply replies replied response responses wrong right correct corrected correction long longer short
fast path paths handler handlers missing incomplete ability abilities support supported unsupported fall fell fallen through brain
candidate suggestion suggested possible possibly likely may might could should would can cannot needs need needed add added adding
router routing routed example examples training label labelled labeled domain domains speech heard hear misheard mishear recognised
recognition keyterm keyterms homophone homophones fix fixes review reviewing prompt prompts format formats quality
asked ask asking rephrased reworded rephrase reword marked cut off later next first when while because where which who what how
""".split())


def allowed_words(facts: dict) -> frozenset:
    """Every word a reworded sentence may use: the words of its own facts (kind, domain, shape, signal phrases, suggestion) plus ``_FUNCTION_WORDS``."""
    bag = [str(facts.get("kind", "")).replace("_", " "), str(facts.get("domain", "")), str(facts.get("shape", "")), str(facts.get("suggestion", ""))]
    for name in (facts.get("signals") or {}):
        bag.append(_SIGNAL_PHRASE.get(name, str(name)))
        bag.append(str(name).replace("_", " "))
    return frozenset(w for text in bag for w in _WORD_RE.findall(text.lower())) | _FUNCTION_WORDS


#: the noun a count placeholder may sit in front of, per field. A count whose next word names a DIFFERENT field's unit is a mis-bound
#: number ("over {days} episodes") and the sentence is refused.
_UNIT_WORDS = {
    "episodes": frozenset({"episode", "episodes"}),
    "days": frozenset({"day", "days", "user-day", "user-days"}),
    "people": frozenset({"person", "persons", "people", "user", "users", "member", "members"}),
}
_ALL_UNITS = frozenset().union(*_UNIT_WORDS.values())
_PLACEHOLDER_RE = re.compile(r"\{(episodes|days|people|n:[A-Z_]+)\}(?:\s+([A-Za-z-]+))?")


def _render_counts(s: str, facts: dict) -> Optional[str]:
    """Fill the model's count placeholders from the facts' OWN fields; None if the sentence is not renderable. The model never writes a
    digit (a digit is refused), so every number in the result comes from the field its placeholder names, and a placeholder followed by
    another field's unit noun is refused. An unknown signal name, or a stray brace, is refused."""
    if re.search(r"\d", s):
        return None
    signals = facts.get("signals") or {}

    def fill(m: "re.Match[str]") -> str:
        name, nxt = m.group(1), (m.group(2) or "").lower()
        if name.startswith("n:"):
            sig_name = name[2:]
            if sig_name not in signals:
                raise ValueError(name)
            value, own = signals[sig_name], frozenset()
        else:
            value, own = facts[name], _UNIT_WORDS[name]
        if nxt in _ALL_UNITS and nxt not in own:
            raise ValueError(name)
        return f"{value}" + (f" {m.group(2)}" if m.group(2) else "")

    try:
        out = _PLACEHOLDER_RE.sub(fill, s)
    except (ValueError, KeyError):
        return None
    return out


def valid_summary(text: Any, facts: dict) -> Optional[str]:
    """The model's sentence if it is ONE plain line made only of words from its facts and a fixed list of grammar / generic words; else None
    (the template is used). Counts are written as PLACEHOLDERS and rendered from the facts' own fields (``_render_counts``): a digit typed
    by the model, a number bound to the wrong field or an unknown signal is refused, so a sentence can never state a count that is not
    the count of the thing it names. The model may rearrange the counts; it cannot introduce a name, a place, a cause or a claim,
    because it has no word to say one with."""
    if not isinstance(text, str):
        return None
    s = text.strip().splitlines()[0].strip() if text.strip() else ""
    s = s.strip("\"'` ")
    if not s or re.search(r"https?:|www\.|@", s, re.I):
        return None
    s = _render_counts(s, facts) or ""
    if not (20 <= len(s) <= SUMMARY_MAX) or any(c in s for c in "{}[]<>"):
        return None
    vocab = allowed_words(facts)
    if any(w.removesuffix("'s") not in vocab and w not in vocab for w in _WORD_RE.findall(s.lower())):
        return None
    return s


LLM = Callable[[list, int], Awaitable[str]]


async def summarise(items: list[tuple[str, dict]], llm: LLM, *, budget_s: float = 240.0, per_call_s: float = 90.0,
                    max_tokens: int = 90) -> dict[str, str]:
    """``items`` = [(proposal id, facts)]. Returns {id: validated model sentence} for those that validated within ``budget_s``. A model that
    is unreachable, slow or wrong yields nothing for that item; the caller keeps the template. Never raises."""
    out: dict[str, str] = {}
    t0 = time.monotonic()
    for pid, facts in items:
        if time.monotonic() - t0 > budget_s:
            break
        try:
            raw = await asyncio.wait_for(llm(messages_for(facts), max_tokens), per_call_s)
        except Exception as exc:  # noqa: BLE001 - the template stands
            logger.info("STRUGGLE_PROPOSALS summary skipped (%s)", type(exc).__name__)
            if isinstance(exc, (ConnectionError, OSError)):
                break        # the model is unreachable: do not wait out a timeout per proposal
            continue
        ok = valid_summary(raw, facts)
        if ok:
            out[pid] = ok
    return out


# ── the store (struggle_proposals / struggle_proposal_events, alembic 0057) ───

_PCOLS = ("id", "fingerprint", "kind", "status", "pattern", "domain", "label", "label_source", "signals", "episode_count", "event_count", "user_count",
          "day_count", "first_seen", "last_seen", "confidence", "summary", "summary_source", "followup", "decision_note", "created_at",
          "updated_at", "decided_at", "decided_by", "mode")


def _ctx():
    from db_pool import get_db_ctx  # type: ignore[import]

    return get_db_ctx()


def _marks(n: int) -> str:
    return ", ".join("?" for _ in range(n))


def _chunks(seq: list, n: int = _IN_CHUNK):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _missing_table(exc: BaseException) -> bool:
    msg = f"{type(exc).__name__} {exc}".lower()
    return "undefinedtable" in msg or "no such table" in msg or (("struggle_proposal" in msg or "struggle_events" in msg) and "does not exist" in msg)


def _row(vals: Iterable[Any]) -> dict:
    return dict(zip(_PCOLS, tuple(vals)))


class ProposalError(Exception):
    """Base of the API-visible failures."""


class NotFound(ProposalError):
    pass


class Conflict(ProposalError):
    pass


def public(row: dict, evidence_live: Optional[int] = None, evidence_ids: Optional[list[str]] = None) -> dict:
    """The API / CLI view of a proposal row. Counts, the text-free pattern, the sentence and the follow-up - never a household word."""
    out = {
        "id": row["id"], "kind": row["kind"], "status": row["status"], "pattern": row["pattern"], "domain": row["domain"],
        "label": row["label"], "label_source": row["label_source"], "signals": _json(row["signals"]), "episodes": row["episode_count"], "events": row["event_count"],
        "users": row["user_count"], "user_days": row["day_count"], "first_seen": row["first_seen"], "last_seen": row["last_seen"],
        "confidence": row["confidence"], "summary": row["summary"], "summary_source": row["summary_source"],
        "followup": _json(row["followup"]), "decision_note": row["decision_note"], "created_at": row["created_at"],
        "updated_at": row["updated_at"], "decided_at": row["decided_at"], "decided_by": row["decided_by"], "mode": row["mode"],
    }
    if evidence_live is not None:
        out["evidence_live"] = evidence_live
    if evidence_ids is not None:
        out["evidence_ids"] = evidence_ids
    return out


def _json(raw: Any) -> dict:
    return parse_detail(raw)


async def _live_counts(db, pids: list[str]) -> dict[str, int]:
    """Evidence links that still join to a ``struggle_events`` row, per proposal."""
    out: dict[str, int] = {}
    for part in _chunks(pids):
        cur = await db.execute(
            "SELECT pe.proposal_id, COUNT(*) FROM struggle_proposal_events pe JOIN struggle_events se ON se.id = pe.event_id "
            f"WHERE pe.proposal_id IN ({_marks(len(part))}) GROUP BY pe.proposal_id", tuple(part))
        for r in await cur.fetchall():
            out[str(r[0])] = int(r[1])
    return out


async def list_proposals(*, status: Optional[str] = None, kind: Optional[str] = None, limit: int = 50) -> list[dict]:
    """Proposals with live evidence only (one whose events are all gone is not shown), best first. The live-evidence test is part of the
    query, BEFORE ``ORDER BY`` / ``LIMIT``: filtering after the limit let evidence-less proposals (events forgotten or retired, not yet
    reconciled) use up the page and hide live ones that ranked below them."""
    sql = (f"SELECT {', '.join(_PCOLS)} FROM struggle_proposals WHERE 1 = 1 AND EXISTS ("
           "SELECT 1 FROM struggle_proposal_events pe JOIN struggle_events se ON se.id = pe.event_id WHERE pe.proposal_id = struggle_proposals.id)")
    params: list[Any] = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY confidence DESC, last_seen DESC LIMIT ?"
    params.append(int(max(1, min(500, limit))))
    async with _ctx() as db:
        cur = await db.execute(sql, tuple(params))
        rows = [_row(r) for r in await cur.fetchall()]
        live = await _live_counts(db, [r["id"] for r in rows])
    return [public(r, live[r["id"]]) for r in rows if live.get(r["id"], 0) > 0]


async def get_proposal(pid: str, *, with_evidence: bool = True) -> dict:
    async with _ctx() as db:
        row = await _fetch_one(db, pid)
        live = await _live_counts(db, [pid])
        if row is None or live.get(pid, 0) <= 0:
            raise NotFound(pid)
        ids = None
        if with_evidence:
            cur = await db.execute(
                "SELECT pe.event_id FROM struggle_proposal_events pe JOIN struggle_events se ON se.id = pe.event_id "
                "WHERE pe.proposal_id = ? ORDER BY pe.ts DESC LIMIT 200", (pid,))
            ids = [str(r[0]) for r in await cur.fetchall()]
    return public(row, live[pid], ids)


async def _fetch_one(db, pid: str) -> Optional[dict]:
    cur = await db.execute(f"SELECT {', '.join(_PCOLS)} FROM struggle_proposals WHERE id = ?", (pid,))
    r = await cur.fetchone()
    return _row(r) if r else None


async def counts() -> dict[str, dict[str, int]]:
    """{status: {kind: n}} over proposals with live evidence (the morning line, the CLI, the PR numbers)."""
    out: dict[str, dict[str, int]] = {}
    for p in await list_proposals(limit=500):
        out.setdefault(p["status"], {})
        out[p["status"]][p["kind"]] = out[p["status"]].get(p["kind"], 0) + 1
    return out


async def pending_maintenance(since: float, *, now: Optional[float] = None) -> int:
    """How much proposal work is waiting for the next night pass, as a COUNT (the quiet-night precheck asks this so that a night with no
    member turns is not skipped while proposals still need tending). Two kinds, both of which the pass resolves, so the count falls back
    to 0 after the window has run:

    * struggle events logged after ``since`` (epoch seconds; the start of the last window that did its work) - this includes a guest's,
      which the member-turn check cannot see;
    * ``proposed`` proposals still linked to evidence older than the lookback: the pass recounts them and withdraws any that no longer
      recur, which without this only ever happened on a night that already had other work.

    0 while ``ZOE_STRUGGLE_PROPOSALS`` is off (the pass is a no-op then) and when the tables are not migrated. RAISES on any other
    failure so the window fails OPEN (runs as before)."""
    if mode() == "off":
        return 0
    cutoff = (time.time() if now is None else now) - lookback_days() * DAY_S
    try:
        async with _ctx() as db:
            cur = await db.execute("SELECT COUNT(*) FROM struggle_events WHERE ts > ?", (float(since),))
            new_events = int((await cur.fetchone())[0] or 0)
            cur = await db.execute(
                "SELECT COUNT(DISTINCT pe.proposal_id) FROM struggle_proposal_events pe JOIN struggle_proposals p ON p.id = pe.proposal_id "
                "WHERE p.status = ? AND pe.ts < ?", (PROPOSED, float(cutoff)))
            aged = int((await cur.fetchone())[0] or 0)
    except Exception as exc:
        if _missing_table(exc):
            return 0
        raise
    return new_events + aged


# ── the decision (admin) ─────────────────────────────────────────────────────

def build_followup(p: dict, now: float) -> dict:
    """What an ACCEPT produces. Nothing here changes the router, the code or a prompt."""
    if p["kind"] == KIND_ROUTER_EXAMPLE:
        return {"type": FOLLOWUP_ROUTER_QUEUE, "consumer": ROUTER_QUEUE_CONSUMER, "label": p["label"], "queued_at": now,
                "note": "candidate training pairs, resolved from the members' own rows at read time (queued_router_examples); applied only by the Phase 3 loop behind its replay ratchet"}
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    short = p["id"][:8]
    title = f"[{p['kind']}] {p['summary'][:100]}"
    body = (f"{p['summary']}\n\nEvidence: {p['episodes']} episodes / {p['events']} events on {p['user_days']} user-days "
            f"(struggle proposal {p['id']}; the event ids are on the proposal). Suggested fix class: {p['kind']}. "
            "Verify the fix with the voice replay gate and `struggle_replay_eval.py`; the cluster should shrink and no other signal should grow.")
    ledger = (f"- **{day}** · Struggle proposal · {p['summary'][:220]} · struggle proposal {short} ({p['kind']}, "
              f"{p['episodes']} episodes) · unassigned · the cluster stops recurring in `scripts/maintenance/struggle_report.py`")
    return {"type": FOLLOWUP_TASK_STUB, "title": title, "body": body, "labels": ["struggle-proposal", p["kind"]], "ledger_line": ledger,
            "created_at": now}


#: the only reasons a decision may carry. A free-text note could hold a household utterance, and a proposal / the API never does
REASONS = ("", "not_a_problem", "wrong_label", "already_fixed", "not_now", "other")
_REF_RE = re.compile(r"^[A-Za-z0-9._:-]{0,60}$")


async def decide(pid: str, action: str, by: str, reason: str = "") -> dict:
    """accept | reject a ``proposed`` proposal. Records the decision (and a ``reason`` from ``REASONS``) and the follow-up; applies NOTHING."""
    if action not in ("accept", "reject"):
        raise ValueError(action)
    if (reason or "") not in REASONS:
        raise ValueError(f"reason must be one of {', '.join(r for r in REASONS if r)}")
    now = time.time()
    async with _ctx() as db:
        row = await _fetch_one(db, pid)
        live = await _live_counts(db, [pid])
        if row is None or live.get(pid, 0) <= 0:
            raise NotFound(pid)
        if row["status"] != PROPOSED:
            raise Conflict(f"already {row['status']}")
        pub = public(row, live[pid])
        new_status = ACCEPTED if action == "accept" else REJECTED
        followup = build_followup(pub, now) if action == "accept" else {}
        cur = await db.execute(
            "UPDATE struggle_proposals SET status = ?, followup = ?, decision_note = ?, decided_at = ?, decided_by = ?, updated_at = ? "
            "WHERE id = ? AND status = ?",
            (new_status, json.dumps(followup, sort_keys=True), reason or "", now, str(by or "")[:80], now, pid, PROPOSED))
        changed = int(getattr(cur, "rowcount", 0) or 0)
        await db.commit()
        if changed <= 0:
            raise Conflict("already decided")
    return await get_proposal(pid)


async def mark_applied(pid: str, ref: str = "") -> dict:
    """The Phase 3 seam: an ``accepted`` proposal whose fix landed becomes ``applied`` (``ref`` = the PR / generation). Nothing in
    the night job calls this."""
    if not _REF_RE.match(ref or ""):
        raise ValueError("ref must be a short id (letters, digits, . _ : -)")
    now = time.time()
    async with _ctx() as db:
        row = await _fetch_one(db, pid)
        if row is None:
            raise NotFound(pid)
        cur = await db.execute(
            "UPDATE struggle_proposals SET status = ?, decision_note = ?, updated_at = ? WHERE id = ? AND status = ?",
            (APPLIED, (((row["decision_note"] + " ") if row["decision_note"] else "") + f"applied: {ref}")[:120], now, pid, ACCEPTED))
        changed = int(getattr(cur, "rowcount", 0) or 0)
        await db.commit()
        if changed <= 0:
            raise Conflict(f"only an accepted proposal can be applied (this one is {row['status']})")
    return await get_proposal(pid, with_evidence=False)


#: a fifth of the labelled failures are held out for the Phase 3 evaluation; the split is a HASH of the normalised failed text, so an example
#: never moves between train and holdout from one night to the next and duplicate phrasings never straddle the two (a model that trained on a
#: wording cannot be scored on the same wording)
HOLDOUT_MOD = 5
#: the saved utterance of a voice turn is looked for this long before the turn finished (STT, then the brain's time to answer)
AUDIO_LOOKBACK_S = 90.0
AUDIO_AMBIGUOUS_S = 5.0


def split_of(text: str) -> str:
    """``holdout`` | ``train``: deterministic per NORMALISED failed text (wake word, contractions, number words, plurals, fillers folded), so a
    re-asked phrasing - which has a different event id every time - and its near-duplicates always land on the same side."""
    key = sig.normalise(text) or str(text or "").strip().lower()
    return "holdout" if int(hashlib.sha1(f"router-holdout|{key}".encode("utf-8")).hexdigest()[:8], 16) % HOLDOUT_MOD == 0 else "train"


def audio_ref(about_ts: Any, *, sample_dir: Optional[str] = None) -> Optional[dict]:
    """The saved audio of the FAILED turn, as a bare file name in the operator's voice-sample corpus (``ZOE_VOICE_SAVE_AUDIO``), or None.

    The corpus keeps real utterances as ``HHMMSS_mmm.wav`` with no turn id, so the file is found by TIME: the latest sample written in
    the ``AUDIO_LOOKBACK_S`` before the failed turn finished. Two samples within ``AUDIO_AMBIGUOUS_S`` of each other make it ambiguous and
    no reference is given (a wrong recording is worse than none). Nothing is read, copied or moved; the corpus is never pruned."""
    import os

    try:
        ts = float(about_ts)
    except (TypeError, ValueError):
        return None
    d = sample_dir or os.environ.get("ZOE_VOICE_SAMPLE_DIR") or "/home/zoe/.zoe-voice-samples"
    try:
        found = sorted((os.stat(os.path.join(d, n)).st_mtime, n) for n in os.listdir(d) if n.endswith(".wav"))
    except OSError:
        return None
    near = [(m, n) for m, n in found if ts - AUDIO_LOOKBACK_S <= m <= ts + 3.0]
    if not near:
        return None
    if len(near) > 1 and near[-1][0] - near[-2][0] < AUDIO_AMBIGUOUS_S:
        return None
    return {"file": near[-1][1], "age_s": round(ts - near[-1][0], 1)}


async def queued_router_examples(limit: int = 500, sources: tuple = (SOURCE_FOLLOWUP,)) -> list[dict]:
    """THE PHASE 3 SEAM. Candidate router training pairs from ACCEPTED ``router_example`` proposals, newest decision first:
    ``[{proposal_id, event_id, user_id, label, label_source, split, text, audio_ref}]``.

    * ``label`` is the fast domain that answered the follow-up that worked (``label_source`` = ``followup``); only those are handed out
      unless the caller asks for ``SOURCE_ROUTER`` too (the router's own belief, no proof).
    * ``text`` is resolved NOW from the member's own ``struggle_events.summary`` (the FAILED turn's words only; member rows only; never a
      guest's; not a word that names a thing the member has forgotten), so a forget that erased the row has already removed the example and
      nothing outside ``struggle_events`` holds the words. Any doubt drops the example (a forget-ledger lookup that fails drops it).
    * ``split`` is the stable train / holdout assignment (by normalised text); ``audio_ref`` the saved recording's file name when one can be found unambiguously.
    Consumers re-label with their own independent oracle before training."""
    if not sources:
        return []
    out: list[dict] = []
    async with _ctx() as db:
        # Only proposals that can yield an example are candidates, decided BEFORE the LIMIT: a recent accepted proposal whose evidence is
        # gone (forgotten / retired), or whose label_source the caller does not want, must not use up the page.
        cur = await db.execute(
            "SELECT id, label, label_source, followup FROM struggle_proposals WHERE kind = ? AND status = ? "
            f"AND label_source IN ({_marks(len(sources))}) AND EXISTS (SELECT 1 FROM struggle_proposal_events pe JOIN struggle_events se "
            "ON se.id = pe.event_id WHERE pe.proposal_id = struggle_proposals.id AND se.is_guest = 0 AND se.summary <> '') "
            "ORDER BY decided_at DESC LIMIT ?",
            (KIND_ROUTER_EXAMPLE, ACCEPTED, *sources, int(max(1, min(200, limit)))))
        props = [(str(r[0]), str(r[1]), str(r[2]), _json(r[3])) for r in await cur.fetchall()]
        for pid, label, source, fu in props:
            if fu.get("type") != FOLLOWUP_ROUTER_QUEUE or label not in sig.FAST_DOMAINS or source not in sources:
                continue
            cur = await db.execute(
                "SELECT se.id, se.user_id, se.summary, se.prev_turn_id, se.channel, se.detail FROM struggle_proposal_events pe "
                "JOIN struggle_events se ON se.id = pe.event_id WHERE pe.proposal_id = ? AND se.is_guest = 0 AND se.summary <> '' "
                "ORDER BY se.ts DESC", (pid,))
            for eid, uid, summary, prev, channel, detail in await cur.fetchall():
                text = str(summary).split(" -> ")[0].strip() if prev else str(summary).strip()
                if not text or is_guest(str(uid)):
                    continue
                out.append({"proposal_id": pid, "event_id": str(eid), "user_id": str(uid), "label": label, "label_source": source,
                            "split": split_of(text), "text": text,
                            "audio_ref": audio_ref(parse_detail(detail).get("about_ts")) if str(channel) == "voice" else None})
    kept: list[dict] = []
    try:
        import memory_forgotten

        for ex in out:
            try:
                if await memory_forgotten.matches(ex["user_id"], ex["text"]):
                    continue
            except Exception:  # noqa: BLE001 - fail closed: an example we cannot vet is not handed out
                continue
            kept.append(ex)
    except Exception:  # noqa: BLE001
        return []
    return kept[:limit]


# ── forgetting (called by struggle_detector BEFORE the events go; fail closed) ──

async def _refresh(db, pids: Iterable[str]) -> int:
    """Recount ``pids`` from their remaining links; a proposal with none (or a still-``proposed`` one that no longer recurs) is deleted; a
    recounted one gets the template sentence back (the old one named counts that are no longer true). Returns proposals deleted."""
    deleted = 0
    for pid in sorted(set(pids)):
        row = await _fetch_one(db, pid)
        if row is None:
            await db.execute("DELETE FROM struggle_proposal_events WHERE proposal_id = ?", (pid,))
            continue
        # the remaining LIVE evidence only (joined back to the events), with each event's own confidence
        cur = await db.execute(
            "SELECT pe.user_id, pe.ts, pe.signal, se.confidence FROM struggle_proposal_events pe JOIN struggle_events se ON se.id = pe.event_id "
            "WHERE pe.proposal_id = ?", (pid,))
        live = [(str(r[0]), float(r[1]), str(r[2]), float(r[3] or 0.0)) for r in await cur.fetchall()]
        links = [(u, ts, s) for u, ts, s, _ in live]
        st = stats_of(links)
        mean = sum(c for *_, c in live) / len(live) if live else 0.0
        if not links or (row["status"] == PROPOSED and not eligible(st, mean)):
            await db.execute("DELETE FROM struggle_proposal_events WHERE proposal_id = ?", (pid,))
            await db.execute("DELETE FROM struggle_proposals WHERE id = ?", (pid,))
            deleted += 1
            continue
        if (st["events"], st["episodes"], st["users"], st["user_days"]) != (row["event_count"], row["episode_count"], row["user_count"], row["day_count"]):
            await db.execute(
                "UPDATE struggle_proposals SET signals = ?, episode_count = ?, event_count = ?, user_count = ?, day_count = ?, first_seen = ?, "
                "last_seen = ?, confidence = ?, summary = ?, summary_source = 'template', updated_at = ? WHERE id = ?",
                (json.dumps(st["signals"], sort_keys=True), st["episodes"], st["events"], st["users"], st["user_days"], st["first_seen"],
                 st["last_seen"], confidence_of(mean, st),
                 template_summary(row["kind"], row["pattern"], row["domain"], row["label"], st, row["label_source"]), time.time(), pid))
    return deleted


async def _proposals_of(db, where: str, params: tuple) -> list[str]:
    cur = await db.execute(f"SELECT DISTINCT proposal_id FROM struggle_proposal_events WHERE {where}", params)
    return [str(r[0]) for r in await cur.fetchall()]


def _sql_events() -> bool:
    """Proposals live in SQL beside ``struggle_events``; when the events are held in-process (tests, the offline replay) there are none."""
    import struggle_detector as sd

    return isinstance(sd.get_backend(), sd.SqlBackend)


async def forget_events(event_ids: list[str]) -> int:
    """Take these events out of every proposal (the forget path). RAISES on a store failure so the forget is not confirmed; only a missing
    table is "nothing stored". Returns proposals deleted."""
    ids = [str(i) for i in event_ids if i]
    if not ids or not _sql_events():
        return 0
    try:
        async with _ctx() as db:
            pids: set[str] = set()
            for part in _chunks(ids):
                pids.update(await _proposals_of(db, f"event_id IN ({_marks(len(part))})", tuple(part)))
                await db.execute(f"DELETE FROM struggle_proposal_events WHERE event_id IN ({_marks(len(part))})", tuple(part))
            gone = await _refresh(db, pids)
            await db.commit()
            return gone
    except Exception as exc:
        if _missing_table(exc):
            return 0
        raise


async def forget_user(user_id: str) -> int:
    """Take every event of ``user_id`` out of every proposal (the audited user delete). RAISES on a store failure."""
    if not (user_id or "").strip() or not _sql_events():
        return 0
    try:
        async with _ctx() as db:
            pids = set(await _proposals_of(db, "user_id = ?", (user_id,)))
            await db.execute("DELETE FROM struggle_proposal_events WHERE user_id = ?", (user_id,))
            gone = await _refresh(db, pids)
            await db.commit()
            return gone
    except Exception as exc:
        if _missing_table(exc):
            return 0
        raise


async def reconcile() -> int:
    """Drop evidence links whose event no longer exists (retention sweep, a forget that ran between a read and a write), recount, delete
    what is left empty. Returns proposals deleted."""
    async with _ctx() as db:
        cur = await db.execute(
            "SELECT DISTINCT proposal_id FROM struggle_proposal_events WHERE event_id NOT IN (SELECT id FROM struggle_events)")
        pids = [str(r[0]) for r in await cur.fetchall()]
        if not pids:
            return 0
        await db.execute("DELETE FROM struggle_proposal_events WHERE event_id NOT IN (SELECT id FROM struggle_events)")
        gone = await _refresh(db, pids)
        await db.commit()
        return gone


# ── the nightly pass ─────────────────────────────────────────────────────────

async def run_pass(*, now: Optional[float] = None, llm: Optional[LLM] = None, dry_run: bool = False,
                   days: Optional[float] = None, min_ep: Optional[int] = None, budget_s: float = 240.0) -> dict[str, Any]:
    """Cluster the last ``days`` of ``struggle_events`` into proposals and (unless ``dry_run``) store them. COUNTS ONLY in the result.

    Idempotent: a rerun updates the same proposals by fingerprint. A rejected proposal stays suppressed for ``REJECT_SUPPRESS_DAYS`` and
    then returns only if it recurred since the rejection; accepted / applied ones are never reopened, only re-counted; a ``proposed`` one
    that no longer recurs is withdrawn. ``MAX_PER_NIGHT`` caps the NEW proposals created in one pass (best first; the rest are counted as
    ``deferred`` and picked up on a later night); a ``proposed`` row whose cluster is still eligible is refreshed and KEPT even when it ranks
    below the cap - ranking orders the queue, it never withdraws an eligible pattern. Nothing here accepts, rejects or applies anything."""
    import struggle_detector as sd

    m = mode()
    if m == "off" and not dry_run:            # off gates the WRITING pass; a dry run only counts, so it may preview before the flag is enabled
        return {"status": "off", "mode": m}
    t = time.time() if now is None else now
    span = lookback_days() if days is None else days
    rows = await asyncio.wait_for(sd.get_backend().rows(t - span * DAY_S, None, limit=ROW_LIMIT), 60.0)
    clusters, skipped = cluster(rows, min_ep=min_ep)         # EVERY eligible cluster, best first: the cap below limits NEW proposals, never what is kept
    res: dict[str, Any] = {
        "status": "ok", "mode": m, "dry_run": dry_run, "days": span, "events": len(rows), "truncated": len(rows) >= ROW_LIMIT,
        "events_by_kind": _by_kind(kind_of(r) for r in rows), "skipped": skipped,
        "clusters_by_kind": _by_kind(c.kind for c in clusters), "created": 0, "updated": 0, "withdrawn": 0, "suppressed": 0,
        "reopened": 0, "deferred": 0, "summaries_model": 0, "summaries_template": 0, "reconciled": 0}
    if dry_run:
        res["would_propose"] = res["clusters_by_kind"]
        return res
    async with _ctx() as db:
        res["reconciled"] = await _reconcile_in(db)
        existing = {r["fingerprint"]: r for r in await _all(db)}
        keep: set[str] = set()
        fresh = 0
        for c in clusters:
            old = existing.get(c.fingerprint)
            if old is None:
                if fresh >= MAX_PER_NIGHT:
                    res["deferred"] += 1       # RANKED, not dropped: a cluster beyond the nightly cap of NEW proposals waits for a later night
                    continue
                fresh += 1
            action = await _upsert(db, c, old, t, m)
            res[action[0]] = res.get(action[0], 0) + 1
            if action[1]:
                keep.add(action[1])
            elif old is not None:
                keep.add(old["id"])
        for fp, old in existing.items():
            if old["status"] == PROPOSED and old["id"] not in keep:
                await db.execute("DELETE FROM struggle_proposal_events WHERE proposal_id = ?", (old["id"],))
                await db.execute("DELETE FROM struggle_proposals WHERE id = ?", (old["id"],))
                res["withdrawn"] += 1
        await db.commit()
    # evidence that vanished between the read and the write (a forget racing this pass) is taken back out before anything is summarised
    res["reconciled"] += await reconcile()
    await _write_summaries(res, llm, budget_s)
    return res


def _by_kind(kinds: Iterable[Optional[str]]) -> dict[str, int]:
    out = {k: 0 for k in KINDS}
    for k in kinds:
        if k in out:
            out[k] += 1
    return out


async def _all(db) -> list[dict]:
    cur = await db.execute(f"SELECT {', '.join(_PCOLS)} FROM struggle_proposals")
    return [_row(r) for r in await cur.fetchall()]


async def _reconcile_in(db) -> int:
    cur = await db.execute("SELECT DISTINCT proposal_id FROM struggle_proposal_events WHERE event_id NOT IN (SELECT id FROM struggle_events)")
    pids = [str(r[0]) for r in await cur.fetchall()]
    if not pids:
        return 0
    await db.execute("DELETE FROM struggle_proposal_events WHERE event_id NOT IN (SELECT id FROM struggle_events)")
    return await _refresh(db, pids)


async def _write_links(db, pid: str, c: Cluster) -> None:
    await db.execute("DELETE FROM struggle_proposal_events WHERE proposal_id = ?", (pid,))
    for eid, uid, ts, s in c.links:
        await db.execute("INSERT INTO struggle_proposal_events (proposal_id, event_id, user_id, ts, signal) VALUES (?, ?, ?, ?, ?)",
                         (pid, eid, uid, ts, s))


async def _upsert(db, c: Cluster, old: Optional[dict], now: float, m: str) -> tuple[str, Optional[str]]:
    """Insert / refresh one cluster's proposal. Returns (counter name, proposal id or None when it was suppressed)."""
    st = c.stats
    sentence = template_summary(c.kind, c.pattern, c.domain, c.label, st, c.label_source)
    if old is None:
        pid = uuid.uuid4().hex
        await db.execute(
            f"INSERT INTO struggle_proposals ({', '.join(_PCOLS)}) VALUES ({_marks(len(_PCOLS))})",
            (pid, c.fingerprint, c.kind, PROPOSED, c.pattern, c.domain, c.label, c.label_source, json.dumps(st["signals"], sort_keys=True), st["episodes"],
             st["events"], st["users"], st["user_days"], st["first_seen"], st["last_seen"], c.confidence, sentence, "template", "{}", "",
             now, now, None, "", m))
        await _write_links(db, pid, c)
        return "created", pid
    status = old["status"]
    if status == REJECTED:
        decided = float(old["decided_at"] or 0.0)
        since = [(u, ts) for _, u, ts, _ in c.links if ts > decided]
        if now - decided < REJECT_SUPPRESS_DAYS * DAY_S or episodes_of(since) < min_episodes():
            return "suppressed", None
        await db.execute("UPDATE struggle_proposals SET status = ?, followup = '{}', decision_note = '', decided_at = NULL, decided_by = '' WHERE id = ?",
                         (PROPOSED, old["id"]))
        counter = "reopened"
    else:
        counter = "updated"
    changed = (old["event_count"], old["episode_count"]) != (st["events"], st["episodes"])
    keep_sentence = (not changed) and old["summary_source"] == "model" and counter == "updated"
    await db.execute(
        "UPDATE struggle_proposals SET signals = ?, episode_count = ?, event_count = ?, user_count = ?, day_count = ?, first_seen = ?, "
        "last_seen = ?, confidence = ?, summary = ?, summary_source = ?, updated_at = ? WHERE id = ?",
        (json.dumps(st["signals"], sort_keys=True), st["episodes"], st["events"], st["users"], st["user_days"], st["first_seen"], st["last_seen"],
         c.confidence, old["summary"] if keep_sentence else sentence, "model" if keep_sentence else "template", now, old["id"]))
    await _write_links(db, old["id"], c)
    return counter, old["id"]


async def _write_summaries(res: dict, llm: Optional[LLM], budget_s: float) -> None:
    """Ask the night model for a one-sentence rewording of each proposal still on its template (live ones only), validate, store."""
    async with _ctx() as db:
        cur = await db.execute(
            f"SELECT {', '.join(_PCOLS)} FROM struggle_proposals WHERE status = ? AND summary_source = 'template' ORDER BY confidence DESC", (PROPOSED,))
        rows = [_row(r) for r in await cur.fetchall()]
    res["summaries_template"] = len(rows)
    if llm is None or not rows:
        return
    items = []
    for r in rows[:12]:
        st = {"signals": _json(r["signals"]), "episodes": r["episode_count"], "user_days": r["day_count"], "users": r["user_count"]}
        items.append((r["id"], facts_of(r["kind"], r["pattern"], r["domain"], r["label"], st, r["label_source"])))
    got = await summarise(items, llm, budget_s=budget_s)
    if not got:
        return
    async with _ctx() as db:
        for pid, text in got.items():
            await db.execute("UPDATE struggle_proposals SET summary = ?, summary_source = 'model', updated_at = ? WHERE id = ? AND status = ?",
                             (text, time.time(), pid, PROPOSED))
        await db.commit()
    res["summaries_model"] = len(got)
    res["summaries_template"] -= len(got)


def summary_line(res: dict) -> str:
    """``STRUGGLE_PROPOSALS_SUMMARY ...`` - counts only (the night job's one report line and the log line)."""
    kinds = ",".join(f"{k}:{v}" for k, v in (res.get("clusters_by_kind") or {}).items() if v) or "-"
    return (f"STRUGGLE_PROPOSALS_SUMMARY status={res.get('status')} mode={res.get('mode')} events={res.get('events', 0)} clusters={kinds} "
            f"created={res.get('created', 0)} updated={res.get('updated', 0)} withdrawn={res.get('withdrawn', 0)} "
            f"suppressed={res.get('suppressed', 0)} reopened={res.get('reopened', 0)} deferred={res.get('deferred', 0)} model_summaries={res.get('summaries_model', 0)}")


async def brief_line(user_id: str) -> Optional[str]:
    """The admin's morning-brief mention (mode ``on`` only): how many proposals wait, by kind. Counts only. Never raises."""
    try:
        if mode() != "on":
            return None
        waiting = (await asyncio.wait_for(counts(), STORE_TIMEOUT_S)).get(PROPOSED) or {}
        n = sum(waiting.values())
        if n <= 0:
            return None
        names = {KIND_ROUTER_EXAMPLE: "router", KIND_INTENT_GAP: "missing ability", KIND_ANSWER_QUALITY: "answer quality", KIND_STT_TERM: "hearing"}
        parts = ", ".join(f"{v} {names[k]}" for k, v in sorted(waiting.items(), key=lambda kv: -kv[1]) if k in names)
        return f"I have {n} idea{'s' if n != 1 else ''} for getting better at things I struggled with ({parts}); the proposals list has details."
    except Exception:  # noqa: BLE001
        return None
