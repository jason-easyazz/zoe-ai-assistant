"""Capability-gap clustering: turn Zoe's "I can't do that" moments into a ranked backlog of what to build next.

Samantha initiative roadmap item 14 (lane B E3, docs/research/samantha-initiative-2026-11-03.md). Pure functions, stdlib only,
no I/O beyond what the caller passes in: ``scripts/maintenance/capability_gap_backlog.py`` owns the file reading and writing.

The signals already exist in the service log and two JSONL logs (never the database):

* ``struggle_detector: STRUGGLE ...``      CANT_DO / FALLTHROUGH / RE_ASK / REPHRASE ... (fix class decides if it counts)
* ``write_claim_guard: WRITE_CLAIM_BLOCKED`` the brain claimed a write that never happened
* ``expert_dispatch: EXPERT_MISS|EXPERT_DEFER_WRITE|EXPERT_NAMED_MISMATCH``  a fast expert had no plan and deferred to the brain
* ``nlu_extractor: no extractor for intent X`` an intent matched that nothing can fill
* ``intent_router: intent_miss: ...``      no intent matched (legacy lines hold the words; since 2026-11-03 only ``len= utt=<hash>``)
* the router shadow log (``actual_routed=chat`` with a low-confidence reason) - hash-keyed, no words
* ``intent-misses.jsonl`` (``{"text","ts"}``)

Clustering is deterministic: a fixed lexicon maps an utterance to a capability AREA and an ACTION (``classify``); events with no
words (a struggle line carries only a domain) take the area from the domain. A cluster is ``(area, action)``. Leftover words that
match no area are grouped by token overlap. Ranking = sum over events of (source weight x recency decay) x (1 + ln distinct
members). Events are de-duplicated per (utterance hash, day) so a replayed harness run counts once.

Privacy: ``Cluster.examples`` and member ids are PRIVATE (the raw report under ``~/.zoe``). ``public_summary`` emits only the
fixed lexicon label, counts and a date band - no words, no ids - and drops any cluster under ``PUBLIC_MIN_COUNT`` events.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional

#: half-life of an event's weight, days
HALF_LIFE_DAYS = 7.0
#: a public-summary row needs at least this many events (small counts can be re-identified in a household of a few people)
PUBLIC_MIN_COUNT = 3
JACCARD_MERGE = 0.5
#: hash-less events of one (source, member, detail) closer together than this are one incident
BURST_S = 120.0

# source -> weight: how strongly the event says "Zoe lacked a capability" (not merely "the brain was used")
SRC_STRUGGLE_CANT_DO = "struggle_cant_do"
SRC_STRUGGLE_FALLTHROUGH = "struggle_fallthrough"
SRC_STRUGGLE_REPEAT = "struggle_repeat"
SRC_STRUGGLE_QUALITY = "struggle_quality"
SRC_WRITE_CLAIM = "write_claim_blocked"
SRC_EXPERT_MISS = "expert_miss"
SRC_EXPERT_DEFER = "expert_defer_write"
SRC_NLU_NO_EXTRACTOR = "nlu_no_extractor"
SRC_INTENT_MISS = "intent_miss"
SRC_ROUTER_UNSURE = "router_unsure"

SOURCE_WEIGHTS = {
    SRC_STRUGGLE_CANT_DO: 1.0, SRC_WRITE_CLAIM: 0.9, SRC_STRUGGLE_FALLTHROUGH: 0.8, SRC_STRUGGLE_REPEAT: 0.8,
    SRC_NLU_NO_EXTRACTOR: 0.7, SRC_EXPERT_MISS: 0.5, SRC_EXPERT_DEFER: 0.4, SRC_STRUGGLE_QUALITY: 0.3,
    SRC_INTENT_MISS: 0.3, SRC_ROUTER_UNSURE: 0.1,
}
SOURCE_LABELS = {
    SRC_STRUGGLE_CANT_DO: "Zoe said she could not", SRC_WRITE_CLAIM: "claimed a write that did not happen",
    SRC_STRUGGLE_FALLTHROUGH: "fast path missed, brain answered", SRC_STRUGGLE_REPEAT: "had to ask again",
    SRC_NLU_NO_EXTRACTOR: "intent matched but nothing could fill it", SRC_EXPERT_MISS: "expert had no plan",
    SRC_EXPERT_DEFER: "write deferred to the brain", SRC_STRUGGLE_QUALITY: "answer judged wrong",
    SRC_INTENT_MISS: "no intent matched", SRC_ROUTER_UNSURE: "router unsure",
}
#: open-ended chat reaching the brain is how it should work, not a gap: its events count for a fraction
CONVERSATION_FACTOR = 0.3

# ── lexicon (fixed vocabulary: every public label comes from here, never from an utterance) ─────────────────────────────────

#: (area, regex) in priority order - the first match wins
_AREA_RULES = (
    ("timers", r"\b(?:timers?|countdown|stopwatch)\b"),
    ("reminders", r"\b(?:remind\w*|alarms?|wake me)\b"),
    ("lists", r"\b(?:lists?|shopping|groceries|grocery|to-?do|todos?)\b"),
    ("calendar", r"\b(?:calendar|meetings?|appointments?|events?|schedule[ds]?|bookings?|diary|agenda)\b"),
    ("weather", r"\b(?:weather|forecast|rain\w*|umbrella|sunrise|sunset|humidity|storm\w*)\b"),
    ("smart_home", r"\b(?:lights?|lamps?|switch\w*|plugs?|blinds?|curtains?|thermostat|heater|heating|aircon|air con|fans?|"
                   r"tv|television|doors?|locks?|garage|sprinklers?|vacuum|cameras?|dim|brighter|temperature)\b|"
                   r"\bturn (?:it|them|that|this|the \w+)(?: back)? (?:on|off)\b|\bturn (?:on|off)\b|\bswitch (?:on|off)\b"),
    ("music", r"\b(?:play\w*|songs?|music|playlists?|albums?|artists?|spotify|volume|skip|pause|radio|podcasts?|tracks?)\b"),
    ("time", r"\b(?:what time|the time|what day|the date|today's date|what's the date|time is it|time now)\b"),
    ("memory", r"\b(?:remember\w*|forget|do you know my|my favou?rite|birthday|what did i (?:say|tell))\b"),
    ("communications", r"\b(?:call|phone|text|texts|message\w*|e-?mail\w*|send|dial|whatsapp|telegram)\b"),
    ("navigation", r"\b(?:directions?|route|traffic|how far|nearby|near me|map|maps|restaurants?|drive to)\b"),
    ("cooking", r"\b(?:recipes?|cook\w*|ingredients?|oven|bake|baking|meal)\b"),
    ("web_lookup", r"\b(?:search|look up|google|news|latest|price of|prices?|score|scores|stock|how much is|who won|"
                   r"headlines?|online|website)\b"),
    ("knowledge_tools", r"\b(?:calculate|convert|translate|spell|define|definition|meaning of (?!life)|what does .{1,30} mean|"
                        r"how many .{1,30} in|plus|minus|times|divided)\b"),
    ("media", r"\b(?:movies?|shows?|netflix|youtube|channel|watch\w*|episode|series)\b"),
    ("home_admin", r"\b(?:bills?|budget|expenses?|pay|invoice|rent|insurance)\b"),
    ("health_routine", r"\b(?:sleep|steps|heart rate|medication|medicine|pills?|exercise|workout)\b"),
    ("capability_request", r"\b(?:learn|extend|new skill|a skill|capabilit\w+|update yourself|can you do|can you learn|"
                           r"teach yourself|improve yourself)\b"),
    ("conversation", r"\b(?:tell me more|what do you think|why|how are you|joke|story|opinion|meaning of life|do you|are you|"
                     r"what are you|who are you|who am i|who (?:is|was|built|invented|wrote)|what is|what's|what will|how do|how does|"
                     r"what about|what else|thank\w*|good night|goodnight|good morning|hello|yes|zoe)\b"),
)
_AREA_RES = tuple((a, re.compile(p)) for a, p in _AREA_RULES)

AREA_LABELS = {
    "timers": "Timers", "reminders": "Reminders and alarms", "lists": "Lists", "calendar": "Calendar and events",
    "weather": "Weather", "smart_home": "Smart home control", "music": "Music and playback", "time": "Time and date",
    "memory": "Remembering personal facts", "communications": "Calls, messages and email", "navigation": "Directions and places",
    "cooking": "Cooking and recipes", "web_lookup": "Live web lookups (news, prices, scores)",
    "knowledge_tools": "Calculation, conversion, translation, definitions", "media": "Video and TV content",
    "home_admin": "Household bills and money", "health_routine": "Health and routine tracking",
    "capability_request": "Asking Zoe to learn something new", "conversation": "Open-ended conversation (brain-served)",
    "uncategorised": "Uncategorised requests",
}

_ACTION_RULES = (
    ("remove", r"\b(?:remove|delete|clear|cancel|erase|drop)\b"),
    ("add", r"\b(?:add|put|append|include)\b"),
    ("create", r"\b(?:set|create|make|schedule|book|start|new|order)\b"),
    ("modify", r"\b(?:change|move|rename|update|edit|reschedule|replace)\b"),
    ("playback", r"\b(?:play\w*|pause|resume|skip|stop|next|previous|louder|quieter)\b"),
    ("control", r"\b(?:turn|switch|dim|brighten|open|close|lock|unlock|toggle)\b"),
    ("send", r"\b(?:call|phone|text|send|message|email|dial)\b"),
    ("find", r"\b(?:find|search|look|locate|where)\b"),
    ("ask", r"\b(?:what|when|who|how|which|is|are|show|tell|read|list|do)\b"),
)
_ACTION_RES = tuple((a, re.compile(p)) for a, p in _ACTION_RULES)
ACTION_LABELS = {
    "remove": "remove or cancel", "add": "add", "create": "create or set", "modify": "change", "playback": "play and control",
    "control": "switch and control", "send": "send or call", "find": "find", "ask": "ask or look up", "do": "other request",
    "unspecified": "unspecified",
}

_WORD = re.compile(r"[a-z0-9']+")
_STOP = frozenset("""a an the to of in on at for and or is are was be it its my me i you your please can could would will do does
did this that these those with from by as so if then than just now about up out""".split())

#: struggle domains / write-claim kinds -> area
_DOMAIN_AREA = {"weather": "weather", "time": "time", "timers": "timers", "lists": "lists", "list": "lists",
                "calendar": "calendar", "smart_home": "smart_home", "music": "music", "reminders": "reminders",
                "memory": "memory", "people": "memory", "notes": "memory", "journal": "memory", "chat": "conversation"}


def utterance_hash(text: str) -> str:
    """The router/log equality key (sha256 first 12 hex), identical to ``log_redaction.utterance_tag``."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def classify(text: str) -> tuple[str, str]:
    """``(area, action)`` for one utterance, from the fixed lexicon. Unmatched words -> ``("uncategorised", action)``."""
    t = " ".join(_WORD.findall((text or "").lower()))
    area = "uncategorised"
    for name, rx in _AREA_RES:
        if rx.search(t):
            area = name
            break
    action = "do"
    for name, rx in _ACTION_RES:
        if rx.search(t):
            action = name
            break
    return area, action


def _tokens(text: str) -> frozenset:
    return frozenset(w for w in _WORD.findall((text or "").lower()) if w not in _STOP and len(w) > 2)


# ── events ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class Event:
    ts: float
    source: str
    member: Optional[str] = None      # a member id, or None when the source carries none
    text: Optional[str] = None        # the words, when known (PRIVATE)
    utt: Optional[str] = None         # utterance hash, when the source is hash-keyed
    area: Optional[str] = None        # area when the source names one (a domain) instead of words
    note: str = ""                    # short private detail (signal, intent name)
    origin: str = ""                  # "jsonl" for an intent-misses.jsonl record (the only source that holds PII-REDACTED words)
    alt_text: Optional[str] = None    # the redacted wording of a hash-only event, kept when its redacted twin is merged into it


_TS_FMT = "%Y-%m-%dT%H:%M:%S%z"
_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4}) +\w+ +(.*)$")
_STRUGGLE = re.compile(r"struggle_detector: STRUGGLE signal=(\w+) conf=[\d.]+ fix=(\w+) domain=(\S+) path=\S+ channel=\S+ user=(\S+)")
_WRITE = re.compile(r"write_claim_guard: WRITE_CLAIM_BLOCKED action=\w+ lane=\S+ user=(\S+) lang=\S+ kinds=(\S+)")
_EXPERT = re.compile(r"expert_dispatch: (EXPERT_MISS|EXPERT_DEFER_WRITE|EXPERT_NAMED_MISMATCH) domain=(\w+)")
_NLU = re.compile(r"nlu_extractor: no extractor for intent (\w+)")
_MISS = re.compile(r"intent_router: intent_miss: (.*)$")
_TAG = re.compile(r"^len=\d+ utt=([0-9a-f]{12})$")


def _struggle_source(signal: str, fix: str) -> Optional[str]:
    if signal == "CANT_DO":
        return SRC_STRUGGLE_CANT_DO
    if fix == "capability_gap":
        return SRC_STRUGGLE_REPEAT
    if fix == "router_training":
        return SRC_STRUGGLE_FALLTHROUGH if signal == "FALLTHROUGH" else SRC_STRUGGLE_REPEAT
    if fix == "answer_quality":
        return SRC_STRUGGLE_QUALITY
    return None          # stt / verbosity / router_latency / stt_snapped are not capability gaps


def _area_for_name(name: str) -> str:
    return _DOMAIN_AREA.get(name) or classify(name.replace("_", " "))[0]


_NON_HOUSEHOLD = ("harness", "probe", "ci")         # traffic_class.NON_HOUSEHOLD (kept literal: this module is also run as a script)
_TRAFFIC_TOKEN = re.compile(r"\btraffic=(\w+)")


def _non_household(value) -> bool:
    return str(value or "").strip().lower() in _NON_HOUSEHOLD


def parse_log_line(line: str) -> Optional[Event]:
    """One service-log line -> an ``Event``, or ``None`` when it is not a capability signal (or is harness traffic)."""
    m = _LINE.match(line.rstrip("\n"))
    if not m:
        return None
    body = m.group(2)
    if any(_non_household(t) for t in _TRAFFIC_TOKEN.findall(body)):
        return None                                  # harness / probe / ci traffic is never household demand
    try:
        ts = datetime.strptime(m.group(1), _TS_FMT).timestamp()
    except ValueError:
        return None
    if (s := _STRUGGLE.search(body)):
        src = _struggle_source(s.group(1), s.group(2))
        if src is None:
            return None
        dom = s.group(3)
        return Event(ts, src, s.group(4), area=_DOMAIN_AREA.get(dom, "uncategorised"), note=f"{s.group(1)}/{dom}")
    if (w := _WRITE.search(body)):
        kind = w.group(2).split(",")[0]
        return Event(ts, SRC_WRITE_CLAIM, w.group(1), area=_DOMAIN_AREA.get(kind, "uncategorised"), note=f"kinds={w.group(2)}")
    if (e := _EXPERT.search(body)):
        src = SRC_EXPERT_DEFER if e.group(1) == "EXPERT_DEFER_WRITE" else SRC_EXPERT_MISS
        return Event(ts, src, None, area=_DOMAIN_AREA.get(e.group(2), "uncategorised"), note=f"{e.group(1)}/{e.group(2)}")
    if (n := _NLU.search(body)):
        return Event(ts, SRC_NLU_NO_EXTRACTOR, None, area=_area_for_name(n.group(1)), note=f"intent={n.group(1)}")
    if (k := _MISS.search(body)):
        raw = k.group(1).strip()
        tag = _TAG.match(raw)
        if tag:
            return Event(ts, SRC_INTENT_MISS, None, utt=tag.group(1))
        return Event(ts, SRC_INTENT_MISS, None, text=raw, utt=utterance_hash(raw))
    return None


def parse_shadow_record(rec: dict) -> Optional[Event]:
    """One router-shadow JSONL record -> an ``Event`` only for a chat turn the router was unsure about (hash-keyed)."""
    if not isinstance(rec, dict) or _non_household(rec.get("traffic") or rec.get("traffic_class")) or rec.get("actual_routed") != "chat":
        return None
    if rec.get("reason") not in ("low_conf", "below_gate"):
        return None
    ts, utt = rec.get("ts"), rec.get("utt")
    if not isinstance(ts, (int, float)) or not isinstance(utt, str):
        return None
    near = rec.get("head_top") or ((rec.get("shortlist") or [None])[0])
    return Event(float(ts), SRC_ROUTER_UNSURE, None, utt=utt, area=_DOMAIN_AREA.get(near or "", None), note=f"near={near}")


def parse_miss_record(rec: dict) -> Optional[Event]:
    """One ``intent-misses.jsonl`` record (``{"text","ts"}``)."""
    if not isinstance(rec, dict) or not isinstance(rec.get("text"), str) or not isinstance(rec.get("ts"), (int, float)):
        return None
    if _non_household(rec.get("traffic_class") or rec.get("traffic")):
        return None                                  # intent_router writes traffic_class into every miss record
    return Event(float(rec["ts"]), SRC_INTENT_MISS, None, text=rec["text"], utt=utterance_hash(rec["text"]), origin="jsonl")


def build_resolver(transcripts: Iterable[str]) -> dict:
    """hash -> words from transcripts the caller already holds (a local STT log): lets hash-only log lines be read PRIVATELY.

    Indexes the raw text and a trimmed-punctuation variant, since the router may have seen either."""
    out: dict = {}
    for t in transcripts:
        if not isinstance(t, str) or not t.strip():
            continue
        for v in (t, t.strip(), t.strip().rstrip(".!?, ")):
            out.setdefault(utterance_hash(v), t.strip())
    return out


# ── clustering and ranking ───────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class Cluster:
    key: str
    area: str
    action: str
    label: str
    events: list = field(default_factory=list)
    count: int = 0
    members: set = field(default_factory=set)
    sources: Counter = field(default_factory=Counter)
    examples: list = field(default_factory=list)      # PRIVATE
    first_ts: float = 0.0
    last_ts: float = 0.0
    last7: int = 0
    score: float = 0.0


_REDACTED = re.compile(r"\[(?:NAME|NUMBER|EMAIL|URL)\]")
REDACT_TWIN_S = 5.0          # the service-log line and the intent-misses.jsonl record of one miss are written back to back


def _drop_redacted_twins(events: Iterable[Event]) -> list:
    """``intent_router`` logs an utterance's hash but writes a PII-REDACTED copy ("[NAME]", "[EMAIL]") to intent-misses.jsonl, so
    hashing the redacted text gives the same request a second identity and it would count twice. A redacted record that has an
    un-redacted intent-miss twin written within ``REDACT_TWIN_S`` of it (one-to-one) is that same request: drop the redacted copy
    and keep the twin (its hash can still be resolved to words from the local STT log). A redacted record with no twin is kept."""
    evs = list(events)
    # a twin is a record of ANOTHER source (the service log): two records of the same file are two requests, however close they are
    twins = sorted((e for e in evs if e.source == SRC_INTENT_MISS and e.origin != "jsonl" and not (e.text and _REDACTED.search(e.text))),
                   key=lambda e: e.ts)
    used: set = set()
    drop: set = set()
    for r in sorted((e for e in evs if e.source == SRC_INTENT_MISS and e.text and _REDACTED.search(e.text)), key=lambda e: e.ts):
        best = None
        for i, t in enumerate(twins):
            if i in used or abs(t.ts - r.ts) > REDACT_TWIN_S:
                continue
            if best is None or abs(t.ts - r.ts) < abs(twins[best].ts - r.ts):
                best = i
        if best is not None:
            used.add(best)
            drop.add(id(r))
            if twins[best].text is None and twins[best].alt_text is None:
                twins[best].alt_text = r.text      # the only wording a typed PII request has: kept for clustering, never for display of the hash
    return [e for e in evs if id(e) not in drop]


def dedupe(events: Iterable[Event]) -> list:
    """One event per (utterance hash, member, day): a replayed or repeated utterance is one request. Hash-less events collapse per burst.

    Among duplicates the one carrying words wins, then the heavier source."""
    best: dict = {}
    out = []
    last: dict = {}
    for ev in sorted(_drop_redacted_twins(events), key=lambda e: (e.ts, e.source, e.member or "", e.note)):
        if not ev.utt:
            k = (ev.source, ev.member, ev.area, ev.note)
            if ev.ts - last.get(k, -1e18) > BURST_S:      # a burst of the same signal is one incident (probe / replay runs)
                out.append(ev)
            last[k] = ev.ts
            continue
        k = (ev.utt, ev.member, datetime.fromtimestamp(ev.ts).strftime("%Y-%m-%d"))
        cur = best.get(k)
        if cur is None or (bool(ev.text), SOURCE_WEIGHTS[ev.source]) > (bool(cur.text), SOURCE_WEIGHTS[cur.source]):
            best[k] = ev
    return out + list(best.values())


def _resolve(ev: Event, resolver: dict) -> Event:
    if ev.text is None and ev.utt and ev.utt in resolver:
        ev.text = resolver[ev.utt]            # the original words from the local STT log beat the redacted copy
    elif ev.text is None and ev.alt_text:
        ev.text = ev.alt_text                 # else the redacted wording ("send a message to [NAME]") still classifies the request
    return ev


def cluster_events(events: Iterable[Event], *, now: float, days: float = 14.0, resolver: Optional[dict] = None) -> list:
    """Cluster the events of the last ``days`` days and return them ranked, highest score first."""
    resolver = resolver or {}
    cutoff = now - days * 86400.0
    evs = [_resolve(e, resolver) for e in dedupe(e for e in events if cutoff <= e.ts <= now + 60)]
    evs.sort(key=lambda e: e.ts)
    clusters: dict = {}
    seeds: list = []            # (token set, key) for leftover words

    for ev in evs:
        if ev.text:
            area, action = classify(ev.text)
            if area == "uncategorised" and ev.area and ev.area != "conversation":
                area = ev.area
            elif area == "uncategorised":
                toks = _tokens(ev.text)
                hit = next((k for ts_, k in seeds if toks and len(toks | ts_) and len(toks & ts_) / len(toks | ts_) >= JACCARD_MERGE), None)
                if hit is None and toks:
                    hit = f"uncategorised~{len(seeds)}"
                    seeds.append((toks, hit))
                ck = hit or "uncategorised~empty"
                _add(clusters, ck, "uncategorised", action, ev)
                continue
        elif ev.area:
            area, action = ev.area, "unspecified"
        else:
            area, action = "uncategorised", "unspecified"
        _add(clusters, f"{area}:{action}:{ev.source if action == 'unspecified' else ''}", area, action, ev)

    ranked = []
    for c in clusters.values():
        wsum = 0.0
        for ev in c.events:
            w = SOURCE_WEIGHTS[ev.source] * (CONVERSATION_FACTOR if c.area == "conversation" else 1.0)
            wsum += w * 0.5 ** (max(0.0, now - ev.ts) / 86400.0 / HALF_LIFE_DAYS)
            if ev.member:
                c.members.add(ev.member)
            c.sources[ev.source] += 1
            if now - ev.ts <= 7 * 86400:
                c.last7 += 1
        c.count = len(c.events)
        c.first_ts, c.last_ts = c.events[0].ts, c.events[-1].ts
        c.score = round(wsum * (1.0 + math.log(max(1, len(c.members)))), 4)
        seen = set()
        for ev in reversed(c.events):         # newest first, distinct words
            if ev.text and ev.text.lower() not in seen and len(c.examples) < 3:
                seen.add(ev.text.lower())
                c.examples.append(ev.text[:140])
        ranked.append(c)
    ranked.sort(key=lambda c: (-c.score, -c.count, c.key))
    return ranked


def _add(clusters: dict, key: str, area: str, action: str, ev: Event) -> None:
    c = clusters.get(key)
    if c is None:
        if action == "unspecified":
            label = f"{AREA_LABELS[area]} - {SOURCE_LABELS[ev.source]}"
        elif area == "uncategorised":
            label = AREA_LABELS[area]
        else:
            label = f"{AREA_LABELS[area]} - {ACTION_LABELS[action]}"
        c = clusters[key] = Cluster(key=key, area=area, action=action, label=label)
    c.events.append(ev)


# ── output ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def private_report(clusters: list, *, now: float, days: float, top: int = 30, notes: Iterable[str] = ()) -> str:
    """The raw ranked backlog for the owner: examples and member ids included. Never commit or publish this."""
    lines = ["# Capability-gap backlog (PRIVATE - raw examples and member ids, do not publish)", "",
             f"Generated {datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M')} - window {days:g} days - "
             f"{sum(c.count for c in clusters)} events in {len(clusters)} clusters", ""]
    lines += [f"- {n}" for n in notes]
    lines += ["", "Rank = sum(source weight x 7-day half-life decay) x (1 + ln distinct members). Events de-duplicated per utterance per day.", ""]
    for i, c in enumerate(clusters[:top], 1):
        src = ", ".join(f"{k} {v}" for k, v in c.sources.most_common())
        lines.append(f"## {i}. {c.label}")
        lines.append(f"score {c.score} - {c.count} events ({c.last7} in last 7d) - members {sorted(c.members) or 'unattributed'} - "
                     f"{_day(c.first_ts)} .. {_day(c.last_ts)}")
        lines.append(f"sources: {src}")
        notes_ = Counter(e.note for e in c.events if e.note).most_common(3)
        if notes_:
            lines.append("detail: " + "; ".join(f"{n} x{k}" for n, k in notes_))
        for ex in c.examples:
            lines.append(f"  e.g. \"{ex}\"")
        lines.append("")
    return "\n".join(lines)


def public_summary(clusters: list, *, now: float, days: float, top: int = 15) -> str:
    """AGGREGATE-ONLY markdown safe for the public repo: fixed lexicon labels and counts. No words, no member ids.

    Clusters under ``PUBLIC_MIN_COUNT`` events are dropped; every uncategorised leftover is folded into one row."""
    merged: dict = {}
    for c in clusters:
        key = "uncategorised" if c.area == "uncategorised" and c.action != "unspecified" else c.key
        label = AREA_LABELS["uncategorised"] if key == "uncategorised" else c.label
        m = merged.setdefault(key, {"label": label, "count": 0, "members": set(), "last7": 0, "score": 0.0, "first": c.first_ts, "last": c.last_ts})
        m["count"] += c.count
        m["members"] |= c.members
        m["last7"] += c.last7
        m["score"] += c.score
        m["first"], m["last"] = min(m["first"], c.first_ts), max(m["last"], c.last_ts)
    rows = sorted((m for m in merged.values() if m["count"] >= PUBLIC_MIN_COUNT),
                  key=lambda m: (m["label"] == AREA_LABELS["uncategorised"], -m["score"], -m["count"], m["label"]))   # residual last
    out = [f"# Capability-gap backlog - aggregate summary ({datetime.fromtimestamp(now).strftime('%Y-%m-%d')}, last {days:g} days)", "",
           f"Aggregate only: cluster labels come from a fixed vocabulary; counts only (rows are ordered by an internal rank); no utterances, names or ids. Rows under {PUBLIC_MIN_COUNT} events are omitted. The uncategorised row is the unranked residual and is always last.", "",
           "| # | Capability cluster | Events | Last 7d |", "|---|---|---|---|"]
    for i, m in enumerate(rows[:top], 1):
        out.append(f"| {i} | {m['label']} | {m['count']} | {m['last7']} |")
    return "\n".join(out) + "\n"
