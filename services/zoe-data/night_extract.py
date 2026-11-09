"""night_extract - a SHADOW night-window pre-pass: VAGO's GLiNEREL-128m reads the day's turns and PROPOSES facts; nothing is written to memory.

Flag ``ZOE_NIGHT_EXTRACT=off|shadow|on`` (default off). ``on`` is NOT built in phase 1 and behaves as ``shadow``. What phase 2 would do is in
docs/knowledge/night-extract.md. The record of how the config was tuned: docs/knowledge/night-extract.md (lab scores, measured run).

Shape (every piece is pure and testable without the model):

  prepare(text)            skip forget / never-mind turns, rewrite I/my -> SPEAKER (the lab's largest relation gain)
  postprocess(result)      the model's raw output -> trusted facts only (person, age, place, lives-in) at ONE global threshold pair (ent 0.9, rel 0.5)
  lexicon_facts(text)      deterministic rules beside the model for the labels it is dead on: pets and relation words (repo lexicons)
  compare(facts, stored)   against what the 4B digest stored that day: agree / extractor_only / contradicts (wrong person, different value)
  run_worker(texts)        the model call, a separate process in its own venv under ``systemd-run --user --scope`` with a memory cap
  run_night(...)           the orchestrator (injectable loaders + extractor); writes a private 0600 JSONL, never memory or the people graph

Privacy: the output holds the proposed facts (names, ages, places - words), never a turn's text; it lives in ``~/.zoe/night-extract/`` (0700/0600), is
erased by "forget X" (``erase_entity``, cascade step ``night_extract``) and by a member erase (``delete_user``), and is re-checked against the forget
ledger on every read (``load_facts``). The input fails closed: the day loader skips forgotten turns strictly and a ledger outage withholds the member.
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import functools
import json
import logging
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

MODES = ("off", "shadow", "on")
FLAG = "ZOE_NIGHT_EXTRACT"
FACTS_SUFFIX = ".night-extract.jsonl"
SUMMARY_SUFFIX = ".night-extract-summary.json"
TRUSTED_KINDS = ("person", "age", "place", "lives_in")      # the model's trustworthy outputs (lab round 2); the lexicon adds pet and kin
LEXICON_KINDS = ("pet", "kin")

# ── the tuned config (lab round 2, ~/.zoe/lab/glinerel/r2_final_cfg.json) ─────────────────────────────────────────────────────────────────
#: canonical -> the phrasing handed to the model ("date" is worded "day of week", relation "age" is worded "is aged")
ENT_LABELS = {"person": "person", "pet": "pet", "relation word": "relation word", "age": "age", "food": "food", "allergy": "allergy",
              "workplace": "workplace", "place": "place", "date": "day of week"}
REL_LABELS = {"child of": "child of", "parent of": "parent of", "spouse of": "spouse of", "sibling of": "sibling of", "age": "is aged",
              "dislikes": "dislikes", "allergic to": "allergic to", "works at": "works at", "lives in": "lives in"}
ENT_THR, REL_THR = 0.9, 0.5                                  # ONE global pair: per-label thresholds overfit the 66-turn tune split
LOW_E, LOW_R = 0.15, 0.03                                    # what the model is asked at; the global pair is applied afterwards (as the lab did)
SPEAKER = "SPEAKER"

SKIP_RE = re.compile(r"\b(forget|forgot|dont keep|don'?t keep|dont remember|don'?t remember|never ?mind|scrap that|ignore (that|what))\b", re.I)
STOP = frozenset({"zoe", "i", "so", "client", "apprentice", "technician", "canteen lady", "speaker", "me", "my", "you", "he", "she", "it",
                  "they", "we", "us", "boss", "manager", "nurse", "doctor", "patient", "teacher", "driver", "electrician", "sparky", "sparkie",
                  "worker", "customer", "colleague", "mate", "lady", "man", "guy", "kid", "baby", "ai", "someone", "everyone", "nobody"})
_PRON = frozenset({"i", "me", "my", "im", "i'm", "we", "us", "our", "mine", "myself", "ours", "speaker"})
_DROP = frozenset({"my", "the", "a", "an", "his", "her", "our", "their", "speaker"})


# ── settings ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def mode() -> str:
    try:
        from typed_env import flag

        m = str(flag(FLAG)).strip().lower()
    except Exception:  # noqa: BLE001 - the registry unavailable is off (fail dark)
        m = "off"
    return m if m in MODES else "off"


def _flag(name: str, default: Any) -> Any:
    try:
        from typed_env import flag

        return flag(name)
    except Exception:  # noqa: BLE001
        return default


def state_dir() -> Path:
    raw = str(_flag("ZOE_NIGHT_EXTRACT_DIR", "") or "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".zoe" / "night-extract"


def venv_python() -> Path:
    raw = str(_flag("ZOE_NIGHT_EXTRACT_VENV", "") or "").strip()
    return (Path(raw).expanduser() if raw else Path.home() / ".zoe" / "venvs" / "glinerel") / "bin" / "python"


# ── pure: prepare / postprocess ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def spk_rewrite(text: str, ph: str = SPEAKER) -> str:
    """First person -> a named speaker, so the model can attribute "my son is 16" to someone (the single biggest relation gain in the lab)."""
    t = re.sub(r"\b(i'm|im)\b", ph + " is", text, flags=re.I)
    t = re.sub(r"\bi am\b", ph + " is", t, flags=re.I)
    t = re.sub(r"\b(i've|ive)\b", ph + " has", t, flags=re.I)
    t = re.sub(r"\bmy\b", ph + "'s", t, flags=re.I)
    t = re.sub(r"\b(myself|me)\b", ph, t, flags=re.I)
    return re.sub(r"\bi\b", ph, t, flags=re.I)


def prepare(text: str) -> Optional[str]:
    """The text the model sees, or None when the turn is skipped (a forget / never-mind turn: it produced false facts in both lab rounds)."""
    t = (text or "").strip()
    if not t or SKIP_RE.search(t):
        return None
    return spk_rewrite(t)


def norm(t: str) -> str:
    t = re.sub(r"'s\b", "", (t or "").lower())
    w = [x for x in re.sub(r"[^\w\- ]|_", " ", t).split() if x]
    if w and " ".join(w) in _PRON:
        return "speaker"
    while len(w) > 1 and w[0] in _DROP:
        w = w[1:]
    return " ".join(w)


_NUM_WORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
                                         "eighteen nineteen".split())}
_NUM_WORDS.update({w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"})


def parse_age(text: str) -> Optional[int]:
    m = re.search(r"\d{1,3}", text or "")
    if m:
        n = int(m.group())
    else:
        parts = re.split(r"[\s-]+", (text or "").lower().strip())
        vals = [_NUM_WORDS[p] for p in parts if p in _NUM_WORDS]
        n = sum(vals) if vals else -1
    return n if 0 < n < 120 else None


def _subject(text: str) -> str:
    n = norm(text)
    return "@speaker" if n == "speaker" else n


def postprocess(res: dict) -> list[dict]:
    """One text's raw model result -> the trusted facts at the global thresholds, in the lab's order of rules: stop-list on names, speaker pronouns
    are the speaker, duplicates collapse, parent-of is not trusted (so its normalisation to child-of never needs to run)."""
    facts: list[dict] = []
    seen: set = set()

    def add(kind: str, subject: Optional[str], value: str, score: float, **extra: Any) -> None:
        key = (kind, subject, value)
        if key not in seen:
            seen.add(key)
            facts.append({"kind": kind, "subject": subject, "value": value, "src": "model", "score": round(float(score), 3), **extra})

    ent_by = {v: k for k, v in ENT_LABELS.items()}
    rel_by = {v: k for k, v in REL_LABELS.items()}
    for e in sorted(res.get("entities") or [], key=lambda x: -x.get("score", 0)):
        lab = ent_by.get(e.get("label"))
        n = norm(e.get("text", ""))
        if e.get("score", 0) < ENT_THR or not n or n == "speaker":
            continue
        if lab == "person" and n not in STOP:
            add("person", None, n, e["score"])
        elif lab == "place":
            add("place", None, n, e["score"])
    for r in sorted(res.get("relations") or [], key=lambda x: -x.get("score", 0)):
        lab = rel_by.get(r.get("label"))
        if lab not in ("age", "lives in") or r.get("score", 0) < REL_THR:
            continue
        h, t = norm(r["head"]["text"]), norm(r["tail"]["text"])
        if any(x in STOP and x != "speaker" for x in (h, t)) or not h or not t:
            continue
        if lab == "age":
            n = parse_age(r["tail"]["text"])
            if n is not None:
                add("age", _subject(r["head"]["text"]), str(n), r["score"])
        elif t != "speaker":
            add("lives_in", _subject(r["head"]["text"]), t, r["score"])
    return facts


# ── pure: the lexicon rules (pets and relation words; the model is dead on both) ────────────────────────────────────────────────────────────
_NAME = r"([^\W\d_][\w'\-]{1,24})"
_NOT_NAMES = frozenset({"a", "an", "the", "is", "was", "are", "and", "or", "but", "has", "had", "who", "that", "this", "it", "he", "she", "they", "my",
                        "our", "his", "her", "their", "very", "so", "too", "just", "not", "also", "still", "really", "now", "then", "there", "here",
                        "to", "of", "in", "on", "at", "for", "with", "from", "by", "as", "if", "when", "while", "named", "called", "name", "zoe",
                        "speaker", "i", "we", "you", "been", "be", "do", "does", "did", "will", "would", "can", "could", "got", "get", "gets", "loves",
                        "likes", "hates", "wants", "needs", "turns", "turned", "works", "lives", "plays", "goes", "comes", "sleeps", "eats", "barks"})


#: lowercase words that follow a kin / pet word without being a name ("my son is tall", "my mum rang"): only consulted for a name typed in lowercase
_NOT_NAMES_LOWER = frozenset({"tall", "sick", "ill", "well", "fine", "ok", "okay", "good", "bad", "new", "old", "young", "big", "small", "happy", "sad", "tired", "busy",
                              "away", "back", "out", "up", "down", "home", "here", "late", "early", "asleep", "awake", "rang", "calls", "called", "texted", "wants",
                              "said", "says", "asked", "loves", "likes", "hates", "needs", "makes", "took", "brought", "left", "coming", "gone", "free", "ready",
                              "nice", "kind", "sweet", "funny", "shy", "cute", "gets", "isnt", "dont", "doesnt", "wont", "cant", "had", "have", "went", "came"})


@functools.lru_cache(maxsize=1)
def _lex() -> tuple:
    import lexicons

    pets = sorted({w for w in lexicons.words("en", "pet_words") if w != "pet"}, key=len, reverse=True) + ["pet"]
    ix = dict(lexicons.kin_index())
    ix.update({"bf": "boyfriend", "gf": "girlfriend", "sis": "sister", "bro": "brother", "hubby": "husband"})
    kin_words = sorted((w for w in ix if w.isalpha() and w not in ("friend", "mate", "buddy", "bestie", "colleague", "coworker", "neighbour", "neighbor")), key=len, reverse=True)
    pet_alt, kin_alt = "|".join(re.escape(p) for p in pets), "|".join(re.escape(k) for k in kin_words)
    poss = r"(?:my|our)"
    pet_res = (re.compile(rf"\b(?:{poss}|a|the)\s+(?:\w+\s+)?({pet_alt})\s+(?:is\s+)?(?:called|named)\s+{_NAME}", re.I),
               re.compile(rf"\b{poss}\s+(?:\w+\s+)?({pet_alt}),?\s+(?!is\b|was\b|has\b|had\b)([A-Za-z][a-z'\-]{{1,24}})\b", re.I),
               re.compile(rf"\b{_NAME}\s+is\s+(?:my|our)\s+(?:\w+\s+)?({pet_alt})\b", re.I),
               re.compile(rf"\b{poss}\s+({pet_alt})\s+is\s+(?:called\s+|named\s+)?([A-Z][a-z'\-]{{1,24}})\b"))
    kin_res = (re.compile(rf"\b{poss}\s+({kin_alt})\s*(?P<sep>is\s+|was\s+|called\s+|named\s+|,\s*)?{_NAME}", re.I),
               re.compile(rf"\b{_NAME}\s+is\s+{poss}\s+({kin_alt})\b", re.I),
               re.compile(rf"\b{_NAME}\s*,\s*{poss}\s+({kin_alt})\b", re.I))
    mention_alt = "|".join(re.escape(w) for w in kin_words)
    mention = re.compile(rf"\b(?:(?P<own>my|our|your)|(?P<who>[A-Za-z]+)'s)\s+(?P<w>{mention_alt})s?\b", re.I)
    return pet_res, kin_res, set(pets), ix, mention


def lexicon_facts(text: str) -> list[dict]:
    """``pet`` (subject = the pet's name, value = its kind) and ``kin`` (subject = the person's name, value = their relation to the speaker, ``word`` =
    the surface word) facts by deterministic rules over the ORIGINAL text. Names that are common words are refused; a pet or a person is only
    named by an explicit "called/named", "<Name> is my <word>", "my <word> <Name>" or "my <word> is <Name>" shape."""
    pet_res, kin_res, _pets, kin_ix, mention = _lex()
    out: list[dict] = []
    seen: set = set()

    def add(kind: str, name: str, value: str, word: str, after: str, explicit: bool = False, via_is: bool = False) -> None:
        n = name.lower().strip("-'")
        if n in _NOT_NAMES or n in STOP or len(n) < 2 or (kind, n, value) in seen:
            return
        if not (explicit or name[0].isupper()):          # a lowercase "name" must look like one: not a word that ends in -ing/-ed/-ly, and the clause must end or go on with and/is/was
            nxt = re.match(r"\s*([A-Za-z']*)", after).group(1).lower()
            if n.endswith(("ing", "ed", "ly")) or n in _NOT_NAMES_LOWER or nxt not in (("",) if via_is else ("", "is", "was", "has", "and", "who", "turns", "turned", "lives")):
                return
        seen.add((kind, n, value))
        out.append({"kind": kind, "subject": n, "value": value, "word": word, "src": "rule", "score": 1.0})

    for i, rx in enumerate(pet_res):
        for m in rx.finditer(text or ""):
            kind, name = (m.group(2), m.group(1)) if i == 2 else (m.group(1), m.group(2))
            add("pet", name, kind.lower(), kind.lower(), text[m.end():], explicit=i in (0, 3))
    for i, rx in enumerate(kin_res):
        for m in rx.finditer(text or ""):
            word, name = (m.group(1), m.group(3)) if i == 0 else (m.group(2), m.group(1))
            if word.lower() in _pets:
                continue
            sep = (m.group("sep") or "").strip().lower() if i == 0 else ""
            add("kin", name, kin_ix.get(word.lower(), word.lower()), word.lower(), text[m.end():], explicit=sep in ("called", "named"), via_is=sep in ("is", "was"))
    for m in mention.finditer(text or ""):                  # an UNNAMED relative ("my sister", "Tom's mum"): the subject is empty, ``of`` says whose
        w = m.group("w").lower()
        if w in _pets:
            continue
        of = "@speaker" if m.group("own") else m.group("who").lower()
        if of in _NOT_NAMES or of in STOP or ("kin", "", w, of) in seen or any(f["word"] == w for f in out if f["kind"] == "kin" and f["subject"]):
            continue                                        # (a relative already named in this turn is not also listed unnamed)
        seen.add(("kin", "", w, of))
        out.append({"kind": "kin", "subject": "", "value": kin_ix.get(w, w), "word": w, "of": of, "src": "rule", "score": 1.0})
    return out


# ── pure: the comparison with what the 4B stored ──────────────────────────────────────────────────────────────────────────────────────────
_AGE_CUE = re.compile(r"\b(?:aged?|years?[ -]old|yo|turns?|turned|is\s+\d{1,3}|\d{1,3}\s*(?:years?|yrs?))\b", re.I)
_USER_SUBJ = re.compile(r"^\s*(?:the\s+)?user\b", re.I)
_CHILD_WORD = re.compile(r"\b(?:son|daughter|child|kid|baby)\b", re.I)


def _words(text: str) -> set:
    return set(re.findall(r"[\w']+", (text or "").lower()))


def _nums(text: str) -> set:
    return {int(x) for x in re.findall(r"\b\d{1,3}\b", text or "")}


def _subj_in(subj: str, words: set) -> bool:
    """Every token of the (possibly multi-word) normalised subject is among the text's tokens ("mary jane" in "Mary Jane is 16")."""
    toks = subj.split()
    return bool(toks) and all(t in words for t in toks)


def compare(facts: list[dict], stored: Iterable[str]) -> dict:
    """Classify each proposed fact against the texts the 4B digest stored that day: ``agree`` (the 4B has it), ``extractor_only`` (the 4B missed it),
    ``contradicts`` (the 4B has the same thing about the wrong person or with another value; ``reason`` says which). Mutates ``facts`` (adds ``cmp`` /
    ``reason``) and returns the counts. Heuristic by design - this is a shadow comparison, a human reads the flagged rows."""
    texts = [(t, _words(t)) for t in stored if t]
    counts = {"agree": 0, "extractor_only": 0, "contradicts": 0}
    for f in facts:
        subj, val, kind = f.get("subject") or "", str(f["value"]), f["kind"]
        cmp, reason = "extractor_only", ""
        named = [(t, w) for t, w in texts if subj and subj != "@speaker" and _subj_in(subj, w)]
        usered = [(t, w) for t, w in texts if _USER_SUBJ.match(t)]
        if kind in ("person", "place"):
            if any(all(p in w for p in val.split()) for _t, w in texts):
                cmp = "agree"
        elif kind == "age":
            n = int(val)
            pool = named if subj != "@speaker" else usered
            if any(n in _nums(t) and _AGE_CUE.search(t) for t, _w in pool):
                cmp = "agree"
            elif any(_AGE_CUE.search(t) and _nums(t) and n not in _nums(t) for t, _w in named):
                cmp, reason = "contradicts", "different_value"
            elif subj != "@speaker" and any(n in _nums(t) and _AGE_CUE.search(t) and not _subj_in(subj, w) for t, w in usered):
                cmp, reason = "contradicts", "wrong_person"
        elif kind == "lives_in":
            pool = named if subj != "@speaker" else usered
            if any(all(p in w for p in val.split()) for _t, w in pool):
                cmp = "agree"
            elif subj != "@speaker" and any(all(p in w for p in val.split()) and any(x.startswith("live") for x in w) and not _subj_in(subj, w) for t, w in usered):
                cmp, reason = "contradicts", "wrong_person"
        elif kind == "pet":
            if any(_CHILD_WORD.search(t) for t, _w in named):
                cmp, reason = "contradicts", "pet_stored_as_child"
            elif any(val in w for _t, w in named):
                cmp = "agree"
        elif kind == "kin":
            if any(f.get("word", "") in w for _t, w in (named if subj else texts)):
                cmp = "agree"
        f["cmp"], f["reason"] = cmp, reason
        counts[cmp] += 1
    return counts


# ── files (private, 0600 / 0700) ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def _private_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(p, 0o700)
    return p


def _write_private(path: Path, text: str) -> None:
    _private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


@contextlib.contextmanager
def _locked(d: Path):
    _private_dir(d)
    fd = os.open(d / ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def facts_path(night: str, d: Optional[Path] = None) -> Path:
    return (d or state_dir()) / f"{night}{FACTS_SUFFIX}"


def summary_path(night: str, d: Optional[Path] = None) -> Path:
    return (d or state_dir()) / f"{night}{SUMMARY_SUFFIX}"


def _read(p: Path) -> list[dict]:
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        with contextlib.suppress(ValueError):
            d = json.loads(ln)
            if isinstance(d, dict):
                out.append(d)
    return out


def fact_text(r: dict) -> str:
    """The words a proposed fact carries (what a forget must be able to match)."""
    return " ".join(str(r.get(k) or "") for k in ("subject", "value", "word", "of"))


def _rewrite(d: Path, keep: Callable[[dict], bool]) -> int:
    removed = 0
    for p in sorted(d.glob(f"*{FACTS_SUFFIX}")):
        recs = _read(p)
        kept = [r for r in recs if keep(r)]
        if len(kept) != len(recs):
            removed += len(recs) - len(kept)
            _write_private(p, "".join(json.dumps(r, sort_keys=True) + "\n" for r in kept))
    return removed


async def erase_entity(user_id: str, rx, *, strict: bool = False) -> int:
    """The forget cascade's ``night_extract`` step: every proposed fact of ``user_id`` naming the entity (``rx``: a ``re.Pattern`` or
    ``memory_forgotten.LedgerPattern``) is dropped. RAISES with ``strict`` (a forget that could not erase is not confirmed)."""
    d = state_dir()
    if not user_id or not d.exists():
        return 0
    try:
        def run() -> int:
            with _locked(d):
                return _rewrite(d, lambda r: not (r.get("user_id") == user_id and rx.search(fact_text(r))))
        return await asyncio.to_thread(run)
    except Exception as exc:  # noqa: BLE001
        if strict:
            raise
        logger.warning("night_extract: facts naming a forgotten entity NOT erased (%s)", type(exc).__name__)
        return 0


_ERASED_FILE = ".erased.json"


def _erased_at(d: Path) -> dict:
    try:
        raw = json.loads((d / _ERASED_FILE).read_text(encoding="utf-8"))
        return {str(k): float(v) for k, v in raw.items()}
    except (OSError, ValueError, AttributeError):
        return {}


def _note_erased(d: Path, user_id: str) -> None:
    """Record WHEN the member was erased (caller holds the output lock). Wall-clock seconds, per member, bounded."""
    m = _erased_at(d)
    m[user_id] = time.time()
    if len(m) > 256:
        m = dict(sorted(m.items(), key=lambda kv: kv[1])[-256:])
    _write_private(d / _ERASED_FILE, json.dumps(m, sort_keys=True))


async def delete_user(user_id: str) -> int:
    """Every proposed fact of the member and their line in each summary (``MemoryService.delete_user``). RAISES on a failure (fail closed)."""
    d = state_dir()
    if not (user_id or "").strip():
        return 0

    def run() -> int:
        with _locked(d):
            _note_erased(d, user_id)         # an in-flight run that loaded this member's turns BEFORE now must not publish them after
            n = _rewrite(d, lambda r: r.get("user_id") != user_id)
            for p in d.glob(f"*{SUMMARY_SUFFIX}"):
                try:
                    s = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if user_id in (s.get("members") or {}):
                    s["members"].pop(user_id, None)
                    _write_private(p, json.dumps(s, indent=1, sort_keys=True))
            return n
    return await asyncio.to_thread(run)


async def load_facts(night: str, *, user_id: Optional[str] = None) -> list[dict]:
    """A night's proposed facts for the owner's reader, re-checked against the forget ledger on EVERY read (a ledger outage withholds the member)."""
    import memory_forgotten as mf

    by_user: dict[str, list[dict]] = {}
    for r in _read(facts_path(night)):
        if user_id is None or r.get("user_id") == user_id:
            by_user.setdefault(str(r.get("user_id") or ""), []).append(r)
    out: list[dict] = []
    for uid, rs in by_user.items():
        try:
            kept, _ = await mf.keep_unforgotten(uid, rs, text_of=fact_text, near=False, strict=True)
        except Exception:  # noqa: BLE001
            kept = []
        out.extend(kept)
    return sorted(out, key=lambda r: float(r.get("ts") or 0))


# ── the model call: a separate process, capped ────────────────────────────────────────────────────────────────────────────────────────────
def worker_argv(py: Path, worker: Path, mem_max: str = "1600M") -> list[str]:
    """The whole command: a transient user scope (the cap and no swap) around ``nice -n 10`` around the venv python. The scope kills only itself."""
    return ["systemd-run", "--user", "--scope", "--quiet", "-p", f"MemoryMax={mem_max}", "-p", "MemorySwapMax=0", "nice", "-n", "10", str(py), str(worker)]


def run_worker(texts: list[str], *, threads: int = 4, batch_size: int = 8, mem_max: str = "1600M", timeout_s: float = 900.0,
               worker: Optional[Path] = None, runner: Callable[..., Any] = subprocess.run) -> dict:
    """One model process for ALL the night's texts (the model loads once per window, not per member). Returns the worker's JSON; ``{"error": ...}``
    when it could not run (venv/model missing -> ``model_missing``; killed by the cap -> ``failed`` with the return code). Never raises."""
    py = venv_python()
    worker = worker or Path(__file__).resolve().parents[2] / "scripts" / "night" / "jobs" / "glinerel_worker.py"
    if not py.exists():
        return {"error": "model_missing", "kind": "no_venv"}
    req = {"texts": texts, "entities": list(ENT_LABELS.values()), "relations": list(REL_LABELS.values()), "batch_size": batch_size,
           "entity_threshold": LOW_E, "relation_threshold": LOW_R}
    env = {**os.environ, "OMP_NUM_THREADS": str(threads), "MKL_NUM_THREADS": str(threads), "HF_HUB_OFFLINE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        p = runner(worker_argv(py, worker, mem_max), input=json.dumps(req), capture_output=True, text=True, timeout=timeout_s, env=env)
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    except OSError as exc:
        return {"error": "failed", "kind": type(exc).__name__}
    try:
        out = json.loads((p.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": "failed", "rc": p.returncode}
    if isinstance(out, dict) and p.returncode not in (0, 3):
        out.setdefault("error", "failed")
        out["rc"] = p.returncode
    return out if isinstance(out, dict) else {"error": "failed"}


def real_members(user_ids: Iterable[str]) -> list[str]:
    """The members whose day is read: never a guest sentinel / ``guest-<id>`` principal, never a harness-minted synthetic id."""
    from user_filters import is_synthetic_user, is_unidentified

    return sorted({u for u in user_ids if u and not is_unidentified(u) and not is_synthetic_user(u)})


# ── the orchestrator ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
Turn = tuple  # (turn_id, text, ts)


async def run_night(*, night: str, members: list[str], load_turns: Callable[[str], Awaitable[list]], load_stored: Callable[[str], Awaitable[list]],
                    extract: Callable[[list], dict], dry_run: bool = False, d: Optional[Path] = None, run_mode: Optional[str] = None, use_ledger: bool = True) -> dict:
    """Read each member's day, run the model ONCE over all of it, compare with the 4B's stored facts, write the private shadow file. Counts only in
    the returned summary. A member whose day could not be read (ledger outage, query failure) is withheld and counted, never guessed."""
    m = run_mode or mode()
    if m == "off":
        return {"status": "flag_off", "mode": m}
    started = time.monotonic()
    wall_start = time.time()
    work: list[tuple[str, Turn, str]] = []                  # (member, turn, prepared text)
    summary: dict[str, Any] = {"night": night, "mode": m, "members": {}, "skipped_forget_turns": 0, "withheld_members": 0}
    for uid in members:
        try:
            turns = await load_turns(uid)
        except Exception as exc:  # noqa: BLE001 - fail closed: this member is not read tonight
            summary["withheld_members"] += 1
            logger.warning("night_extract: %s turns unavailable (%s)", "member", type(exc).__name__)
            continue
        summary["members"][uid] = {"turns": len(turns), "facts": 0, "agree": 0, "extractor_only": 0, "contradicts": 0}
        for t in turns:
            prepared = prepare(t[1])
            if prepared is None:
                summary["skipped_forget_turns"] += 1
            else:
                work.append((uid, t, prepared))
    if not work:
        return {**summary, "status": "nothing_to_do", "turns": 0, "wall_s": round(time.monotonic() - started, 1)}
    out = await asyncio.to_thread(extract, [w[2] for w in work])
    if out.get("error") or len(out.get("results") or []) != len(work):
        return {**summary, "status": out.get("error") or "failed", "turns": len(work), "wall_s": round(time.monotonic() - started, 1)}
    rows: list[dict] = []
    per_member: dict[str, list[dict]] = {}
    for (uid, t, _p), res in zip(work, out["results"]):
        for f in postprocess(res) + lexicon_facts(t[1]):
            f.update(night=night, user_id=uid, turn_id=str(t[0]), ts=float(t[2]) if t[2] else 0.0)
            per_member.setdefault(uid, []).append(f)
    for uid, facts in per_member.items():
        try:
            stored = await load_stored(uid)
        except Exception:  # noqa: BLE001 - no comparison, the facts are still the extractor's
            stored = []
        c = compare(facts, stored)
        summary["members"][uid].update({"facts": len(facts), **c})
        rows.extend(facts)
    if dry_run:
        rows = await _drop_forgotten(rows) if use_ledger else rows
    else:
        dd = d or state_dir()
        with _locked(dd):
            # the second wall, INSIDE the output lock: "forget X" writes the ledger first and ``erase_entity`` then takes this same lock, so a forget either lands before
            # this check (the fact is not written) or waits for the lock and erases what was written - there is no check-then-write window
            rows = await _drop_forgotten(rows) if use_ledger else rows
            erased = _erased_at(dd)           # a member erased since this run loaded their day: their snapshot is never published
            rows = [r for r in rows if erased.get(str(r.get("user_id")), 0.0) < wall_start]
            keep_old = [r for r in _read(facts_path(night, dd)) if r.get("user_id") not in per_member]       # a re-run replaces the member's own rows
            _write_private(facts_path(night, dd), "".join(json.dumps(r, sort_keys=True) + "\n" for r in keep_old + rows))
            _write_private(summary_path(night, dd), json.dumps({**summary, "perf": {k: out.get(k) for k in ("load_s", "infer_s", "peak_rss_mb", "threads")}},
                                                               indent=1, sort_keys=True))
    totals = {k: sum(v[k] for v in summary["members"].values()) for k in ("facts", "agree", "extractor_only", "contradicts")}
    return {**summary, **totals, "status": "ok", "turns": len(work), "wall_s": round(time.monotonic() - started, 1),
            "load_s": out.get("load_s"), "infer_s": out.get("infer_s"), "peak_rss_mb": out.get("peak_rss_mb"), "dry_run": dry_run, "rows": rows if dry_run else None}


async def _drop_forgotten(rows: list[dict]) -> list[dict]:
    try:
        import memory_forgotten as mf
    except Exception:  # noqa: BLE001 - no ledger module = no way to show a fact is not forgotten: withhold everything (fail closed)
        return []
    out: list[dict] = []
    by_user: dict[str, list[dict]] = {}
    for r in rows:
        by_user.setdefault(r["user_id"], []).append(r)
    for uid, rs in by_user.items():
        try:
            kept, _ = await mf.keep_unforgotten(uid, rs, text_of=fact_text, near=False, strict=True)
        except Exception:  # noqa: BLE001 - LedgerUnavailable: withhold this member's facts
            kept = []
        out.extend(kept)
    return out
