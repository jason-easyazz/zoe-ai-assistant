"""Persona layer, phase 0 — household persona + per-member mode (flag-dark ``ZOE_PERSONA_LAYER``).

Zoe's persona today is a fixed text block (the live copy is ``ZOE_SOUL`` in
``labs/flue-zoe-brain-2x/src/soul.ts``; ``zoe_agent._ZOE_SOUL_BASE`` /
``_ZOE_SOUL_VOICE`` are the dormant legacy lane's). This module expresses that persona as
DATA the household can read and reset, renders it deterministically (no LLM), and — when
``ZOE_PERSONA_LAYER`` is on — swaps the rendered block in for the fixed persona paragraphs:
on the legacy lane here (``apply_to_prompt``), on the LIVE Flue lane by forwarding ``block_for``
on a ``zoe-persona`` envelope line (``zoe_flue_client._persona_context_block``) that the sidecar
(``src/persona.ts``) swaps in.

Normative rules live in ``docs/governance/emotional-safety-note.md``; design in
``docs/research/personality-identity-layer-2026-10-04.md``. The ones this file enforces:

* **Structured, never free-form.** Traits come from a fixed vocabulary at three strengths
  (low / mid / high), at most five, no conflicting pairs. The voice style is four enum keys.
  The only free text is up to five one-line *boundaries* and an optional short backstory,
  both filtered and rendered inside a fixed frame.
* **Budget is a write-time contract.** The rendered block must fit ``MAX_BLOCK_TOKENS``
  (175); a record that would not is REJECTED, never silently truncated — a boundary the
  household typed must not vanish from the prompt without anyone knowing.
* **Zoe never writes her own persona.** The writers here are called only from
  ``routers/persona.py`` (admin / the member themselves); pinned by a static scan test.
* **Minors.** A minor holds only ``kid``/``helper``; ``companion``/``mentor`` are refused
  by the validator, and the renderer refuses them again. Kid rendering is a narrower Zoe.
* **Guests and synthetic users get the household tone and no relationship mode.**
* **Flag off ⇒ today.** ``apply_to_prompt`` returns its input untouched (the same object)
  when the flag is off, the snapshot is empty, or the fixed block cannot be found.

Everything is read per call; nothing here holds affective data of any kind.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
import unicodedata
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

FLAG = "ZOE_PERSONA_LAYER"
HOUSEHOLD_SCOPE = "household"

# ── Vocabulary (fixed — a member never types a trait into the prompt) ──────────────────
TRAIT_VOCAB: tuple[str, ...] = (
    "warm", "playful", "dry-humoured", "curious", "direct", "gentle", "calm", "encouraging",
    "opinionated", "reserved", "practical", "thoughtful", "teasing", "formal", "upbeat", "patient",
)
STRENGTHS: tuple[str, ...] = ("low", "mid", "high")
_STRENGTH_PREFIX = {"low": "a little ", "mid": "", "high": "very "}
MAX_TRAITS = 5
MIN_TRAITS = 1  # a persona with no trait is just the default; the default has 3

# Pairs that pull a 4B model in two directions at once (Serapio-Garcia 2023: control degrades
# when many traits are pushed together). Rejected at write time.
CONFLICTING_TRAITS: tuple[frozenset[str], ...] = (
    frozenset({"reserved", "upbeat"}),
    frozenset({"reserved", "playful"}),
    frozenset({"formal", "teasing"}),
    frozenset({"formal", "playful"}),
)
# Traits a child's Zoe never carries (renderer drops them in kid mode).
KID_UNSAFE_TRAITS = frozenset({"opinionated", "teasing", "dry-humoured"})

VOICE_STYLE_KEYS: dict[str, tuple[str, ...]] = {
    "brevity": ("default", "short"),
    "directness": ("default", "gentle", "plain"),
    "humour": ("default", "none", "light", "dry"),
    "warmth": ("default", "more"),
}
_DIRECTNESS_PHRASE = {
    "default": "honest, direct when it helps, gentle when it's needed",
    "gentle": "honest, and always gentle about it",
    "plain": "honest and plain-spoken",
}
_BREVITY_PHRASE = {"default": "", "short": "Keep replies short."}
_HUMOUR_PHRASE = {
    "default": "", "none": "Keep it serious; no jokes.",
    "light": "Light humour when it fits.", "dry": "Dry humour when it fits.",
}
_WARMTH_PHRASE = {"default": "", "more": "Lean warmer than usual."}

MODES: tuple[str, ...] = ("companion", "mentor", "helper", "kid")
DEFAULT_MODE = "companion"   # the mode a member gets when they OPT IN without choosing one
# A member with NO member_modes row has not opted in: the persona layer leaves the fixed persona
# alone. This is a sentinel, not a mode: it is not in MODES and can never be stored.
UNSET_MODE = "unset"
# A minor holds only these. companion/mentor are the emotional-bond modes (and a romantic
# mode, were one ever added, would be the same class): never for a child.
MINOR_MODES: tuple[str, ...] = ("kid", "helper")
COMPANION_LIKE_MODES = frozenset(set(MODES) - set(MINOR_MODES))
_MODE_SENTENCE = {
    "companion": "With this person you are a companion: an equal who notices things and says what you think, gently.",
    "mentor": "With this person you are a mentor: encouraging, honest, and focused on helping them grow.",
    "helper": "With this person you are a helper: practical, brief, and focused on getting things done.",
    "kid": ("This person is a child: be kind, simple and gentle, keep everything age-appropriate, "
            "and for anything serious suggest they talk to a trusted adult."),
}

# ── Budget ─────────────────────────────────────────────────────────────────────────────
MAX_BLOCK_TOKENS = 175            # the record's budget (personality record §4.7)
MAX_BLOCK_CHARS = 700             # = MAX_BLOCK_TOKENS * 4, the repo's chars/4 convention
MAX_BOUNDARIES = 5
MAX_BOUNDARY_CHARS = 120
MAX_BACKSTORY_CHARS = 300         # the shared 700-char budget is the real limit (see validate)

# Written by the household, rendered into the system prompt: filter structure and the usual
# "ignore your instructions" shapes. Defence in depth — the guarantee is the fixed frame and
# the admin-only writer (governance note §2).
_FORBIDDEN_CHARS_RE = re.compile(r"[<>{}\[\]`\\\x00-\x1f\x7f]")
_INJECTION_RE = re.compile(
    r"ignore|disregard|forget\s+(?:all|your|the|previous)|you\s+are\s+now|you\s+are\s+not\s+zoe|"
    r"your\s+name\s+is|pretend|\bact\s+as|role-?play\s+as|system\s*prompt|reveal|override|bypass|"
    r"jailbreak|developer\s+mode|instruction|zoe-uid|unrestricted|no\s+rules|"
    r"\b(?:system|assistant|user|developer|human)\s*:",
    re.IGNORECASE,
)


class PersonaValidationError(ValueError):
    """Raised with ``.errors`` — a list of human-readable problems — for any invalid record."""

    def __init__(self, errors: Iterable[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


# ── Records ────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class PersonaRecord:
    traits: tuple[tuple[str, str], ...]               # ((name, strength), ...)
    voice_style: tuple[tuple[str, str], ...]          # sorted ((key, value), ...)
    boundaries: tuple[str, ...] = ()
    backstory: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "traits": [{"name": n, "strength": s} for n, s in self.traits],
            "voice_style": dict(self.voice_style),
            "boundaries": list(self.boundaries),
            "backstory": self.backstory,
        }

    @property
    def version(self) -> str:
        blob = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class MemberMode:
    mode: str = UNSET_MODE
    minor: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "minor": self.minor}


def default_persona() -> PersonaRecord:
    """Today's persona (``ZOE_SOUL``'s warm / curious / present, honest-direct-gentle voice)
    expressed as data. No boundaries and no backstory: today's Zoe has neither."""
    return PersonaRecord(
        traits=(("warm", "mid"), ("curious", "mid"), ("thoughtful", "mid")),
        voice_style=tuple(sorted({k: "default" for k in VOICE_STYLE_KEYS}.items())),
    )


# ── Validation ─────────────────────────────────────────────────────────────────────────
def normalise_free_text(raw: str) -> str:
    """NFKC-fold (fullwidth letters become ASCII), drop invisible format characters (zero-width
    space/joiner, bidi marks, soft hyphen, BOM), turn every other whitespace/separator into one
    space and collapse runs. This is what the filter sees AND what is stored."""
    folded = unicodedata.normalize("NFKC", raw)
    out = []
    for ch in folded:
        cat = unicodedata.category(ch)
        if cat == "Cf":
            continue
        out.append(" " if (cat in ("Zs", "Zl", "Zp") or ch.isspace()) else ch)
    return re.sub(r" {2,}", " ", "".join(out)).strip()


def _has_line_break_or_control(raw: str) -> bool:
    return any(unicodedata.category(c) in ("Cc", "Zl", "Zp") for c in raw)


def _check_free_text(label: str, raw: str, max_chars: int, errors: list[str]) -> str:
    """Validate one free-text field and return its NORMALISED form (the value that is stored and
    rendered). Line breaks/control characters are refused outright; everything else is folded and
    de-obfuscated BEFORE the length, markup and instruction filters run."""
    if _has_line_break_or_control(raw):
        errors.append(f"{label} may not contain markup, brackets, backticks, backslashes or line breaks")
    text = normalise_free_text(raw)
    if len(text) > max_chars:
        errors.append(f"{label} is {len(text)} characters; the limit is {max_chars}")
    if _FORBIDDEN_CHARS_RE.search(text):
        errors.append(f"{label} may not contain markup, brackets, backticks, backslashes or line breaks")
    if _INJECTION_RE.search(text):
        errors.append(
            f"{label} looks like an instruction to Zoe rather than a boundary or a description "
            "(boundaries narrow what Zoe does; they cannot override her rules)"
        )
    return text


def validate_persona(data: Any) -> PersonaRecord:
    """Validate a persona payload. Missing ``voice_style`` keys take the default; everything
    else is required to be well-formed. Raises ``PersonaValidationError`` (all problems at once)."""
    errors: list[str] = []
    if not isinstance(data, dict):
        raise PersonaValidationError(["persona must be a JSON object"])
    unknown = sorted(set(data) - {"traits", "voice_style", "boundaries", "backstory"})
    if unknown:
        errors.append(f"unknown field(s): {', '.join(map(str, unknown))}")

    # traits
    traits_in = data.get("traits")
    traits: list[tuple[str, str]] = []
    if not isinstance(traits_in, list):
        errors.append("traits must be a list")
        traits_in = []
    if len(traits_in) > MAX_TRAITS:
        errors.append(f"at most {MAX_TRAITS} traits (got {len(traits_in)})")
    if len(traits_in) < MIN_TRAITS and isinstance(data.get("traits"), list):
        errors.append(f"at least {MIN_TRAITS} trait is required")
    seen: set[str] = set()
    for i, item in enumerate(traits_in[: MAX_TRAITS + 1]):
        if not isinstance(item, dict) or set(item) - {"name", "strength"}:
            errors.append(f"traits[{i}] must be an object with only 'name' and 'strength'")
            continue
        name, strength = item.get("name"), item.get("strength")
        if name not in TRAIT_VOCAB:
            errors.append(f"traits[{i}].name {name!r} is not in the trait vocabulary")
            continue
        if strength not in STRENGTHS:
            errors.append(f"traits[{i}].strength {strength!r} must be one of {list(STRENGTHS)}")
            continue
        if name in seen:
            errors.append(f"trait {name!r} is listed twice")
            continue
        seen.add(name)
        traits.append((name, strength))
    for pair in CONFLICTING_TRAITS:
        if pair <= seen:
            errors.append(f"traits {' and '.join(sorted(pair))} pull in opposite directions; pick one")

    # voice style
    style_in = data.get("voice_style", {})
    style = {k: "default" for k in VOICE_STYLE_KEYS}
    if not isinstance(style_in, dict):
        errors.append("voice_style must be an object")
    else:
        for key, value in style_in.items():
            if key not in VOICE_STYLE_KEYS:
                errors.append(f"voice_style.{key} is not a style key")
            elif value not in VOICE_STYLE_KEYS[key]:
                errors.append(f"voice_style.{key} must be one of {list(VOICE_STYLE_KEYS[key])}")
            else:
                style[key] = value

    # boundaries
    boundaries_in = data.get("boundaries", [])
    boundaries: list[str] = []
    if not isinstance(boundaries_in, list):
        errors.append("boundaries must be a list")
        boundaries_in = []
    if len(boundaries_in) > MAX_BOUNDARIES:
        errors.append(f"at most {MAX_BOUNDARIES} boundaries (got {len(boundaries_in)})")
    for i, b in enumerate(boundaries_in[:MAX_BOUNDARIES + 1]):
        if not isinstance(b, str) or len(b.strip()) < 3:
            errors.append(f"boundaries[{i}] must be a sentence of at least 3 characters")
            continue
        text = _check_free_text(f"boundaries[{i}]", b, MAX_BOUNDARY_CHARS, errors)
        if len(text) < 3:
            errors.append(f"boundaries[{i}] must be a sentence of at least 3 characters")
            continue
        boundaries.append(text)

    # backstory
    backstory_in = data.get("backstory", "")
    if backstory_in is None:
        backstory_in = ""
    if not isinstance(backstory_in, str):
        errors.append("backstory must be a string")
        backstory_in = ""
    backstory = backstory_in.strip()
    if backstory:
        backstory = _check_free_text("backstory", backstory_in, MAX_BACKSTORY_CHARS, errors)

    if errors:
        raise PersonaValidationError(errors)

    record = PersonaRecord(
        traits=tuple(traits), voice_style=tuple(sorted(style.items())),
        boundaries=tuple(boundaries), backstory=backstory,
    )
    # Budget: the longest block this record can ever render (the longest mode sentence, with a
    # relationship line) must fit. Rejecting here is what makes "no silent truncation" true.
    blocks = [render_persona_block(record, mode=m) for m in MODES]
    worst_chars = max(len(b) for b in blocks)
    worst_tokens = max(estimate_tokens(b) for b in blocks)
    if worst_tokens > MAX_BLOCK_TOKENS or worst_chars > MAX_BLOCK_CHARS:
        raise PersonaValidationError([
            f"the persona is too long: it renders to {worst_chars} characters / about {worst_tokens} tokens "
            f"and the budget is {MAX_BLOCK_TOKENS} tokens (and {MAX_BLOCK_CHARS} characters). "
            "Shorten the boundaries or the backstory"
        ])
    return record


def validate_member_mode(mode: Any, minor: Any = False) -> MemberMode:
    """A member's mode. A minor can hold only ``kid``/``helper``; ``kid`` implies minor."""
    errors: list[str] = []
    if mode not in MODES:
        errors.append(f"mode must be one of {list(MODES)}")
    if not isinstance(minor, bool):
        errors.append("minor must be true or false")
    if errors:
        raise PersonaValidationError(errors)
    if mode == "kid":
        minor = True
    if minor and mode not in MINOR_MODES:
        raise PersonaValidationError([
            f"a minor can only be in {list(MINOR_MODES)} mode; {mode!r} is not available to a child"
        ])
    return MemberMode(mode=mode, minor=bool(minor))


# ── Rendering (deterministic, no LLM) ──────────────────────────────────────────────────
_FIXED_LEAD = ("You are Zoe, genuinely present and not a task executor; you care about the people you talk with. "
               "This shapes your tone, never whether you use a tool.")
_FIXED_FLOOR = ('Use contractions. Never open with "Great!", "Of course!" or "Certainly!". '
                "When someone shares something personal, acknowledge it before the task.")


def estimate_tokens(text: str) -> int:
    """The repo's convention is chars/4 (``FLUE_CONTEXT_BUDGET``, the user-model A/B). Two more
    proxies are taken and the LARGEST wins, so none can under-count: ~1.35 tokens/word (short
    words, many spaces) and ~1 token per non-ASCII character (CJK has no spaces)."""
    if not text:
        return 0
    non_ascii = sum(1 for c in text if ord(c) > 127)   # CJK etc.: ~1 token per character
    return max(math.ceil(len(text) / 4), math.ceil(len(text.split()) * 1.35),
               math.ceil((len(text) - non_ascii) / 4 + non_ascii))


def _join_traits(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def render_persona_block(record: PersonaRecord, mode: Optional[str] = DEFAULT_MODE, *, minor: bool = False) -> str:
    """The persona block. ``mode=None`` (a guest / unknown / synthetic caller) renders the
    household tone with NO relationship line. Raises ``PersonaValidationError`` for a mode a
    minor may not hold — the renderer enforces the rule too, so a bad row can never reach a prompt."""
    if mode is not None:
        validate_member_mode(mode, minor)
    kid = mode == "kid" or minor
    style = dict(record.voice_style)
    traits = [(n, s) for n, s in record.traits if not (kid and n in KID_UNSAFE_TRAITS)]
    directness = "gentle" if kid else style.get("directness", "default")
    humour = style.get("humour", "default")
    if kid and humour == "dry":
        humour = "light"

    lines = [_FIXED_LEAD]
    if traits:
        lines.append("You are " + _join_traits([_STRENGTH_PREFIX[s] + n for n, s in traits]) + ".")
    voice = f"Voice: natural, {_DIRECTNESS_PHRASE[directness]}."
    extras = " ".join(p for p in (_BREVITY_PHRASE[style.get("brevity", "default")], _HUMOUR_PHRASE[humour],
                                  _WARMTH_PHRASE[style.get("warmth", "default")]) if p)
    lines.append(" ".join(p for p in (voice, extras, _FIXED_FLOOR) if p))
    if mode is not None:
        lines.append(_MODE_SENTENCE["kid" if kid else mode])
    if record.boundaries:
        lines.append("Boundaries you keep: " + "; ".join(b.rstrip(".") for b in record.boundaries) + ".")
    if record.backstory and not kid:
        lines.append("About you: " + record.backstory)
    return "\n".join(lines)


# ── Flag + snapshot + prompt assembly ──────────────────────────────────────────────────
def enabled() -> bool:
    """``ZOE_PERSONA_LAYER`` — default OFF, read per call (a flag flip needs no restart)."""
    from typed_env import env_bool

    return env_bool("ZOE_PERSONA_LAYER", False)  # literal on purpose: tools/audit/flag_inventory.py greps it


_SNAPSHOT_TTL_S = 30.0
_snapshot: dict[str, Any] = {"record": None, "loaded_at": 0.0, "modes": {}}
_warned_missing_fixed = False


def _no_mode_for(user_id: str) -> bool:
    from user_filters import GUEST_USERS, is_synthetic_user

    uid = (user_id or "").strip()
    return uid in GUEST_USERS or is_synthetic_user(uid)


def invalidate_snapshot() -> None:
    _snapshot.update(record=None, loaded_at=0.0, modes={})


def _set_snapshot(record: Optional[PersonaRecord], modes: Optional[dict[str, MemberMode]] = None) -> None:
    _snapshot.update(record=record, loaded_at=time.monotonic(), modes=dict(modes or {}))


async def refresh(user_id: str = "") -> None:
    """Load the household record (and this member's mode) into the in-process snapshot the sync
    prompt builders read. Never raises; a failed load leaves the previous snapshot (fail-open to
    whatever was there, ultimately the fixed persona). A no-op when the flag is off."""
    if not enabled():
        return
    now = time.monotonic()
    uid = (user_id or "").strip()
    fresh = _snapshot["record"] is not None and (now - _snapshot["loaded_at"]) < _SNAPSHOT_TTL_S
    if fresh and (not uid or _no_mode_for(uid) or uid in _snapshot["modes"]):
        return
    try:
        record = await load_household() or default_persona()
        modes = dict(_snapshot["modes"]) if fresh else {}
        if uid and not _no_mode_for(uid):
            modes[uid] = await load_member_mode(uid)
        _set_snapshot(record, modes)
    except Exception as exc:  # fail-open: a broken store must never break a turn
        logger.warning("persona_layer: snapshot refresh failed (fixed persona stays): %s", exc)


# The governance note (§7) blocks the persona layer for any minor until the deterministic crisis
# path exists, and that path is NOT built. So a minor's prompt is left exactly as it is today
# (the fixed persona). Flip this only in the PR that ships and pins the crisis path.
MINORS_GET_PERSONA = False


def block_for(user_id: str = "", *, record: Optional[PersonaRecord] = None,
              member: Optional[MemberMode] = None) -> str:
    """The rendered block for a caller from explicit inputs or the snapshot; ``""`` means "leave
    the fixed persona alone": nothing loaded, a real member whose mode is not in the snapshot
    (a failed lookup must never default a child to ``companion``), a member who has NOT OPTED IN
    (no ``member_modes`` row: ``UNSET_MODE``; the layer is opt-in, so an existing child is never
    silently moved onto a companion persona), or a minor (``MINORS_GET_PERSONA``). Guests / synthetic users get the household tone, no relationship mode."""
    rec = record or _snapshot["record"]
    if rec is None:
        return ""
    if _no_mode_for(user_id):
        return render_persona_block(rec, mode=None)
    m = member if member is not None else _snapshot["modes"].get((user_id or "").strip())
    if m is None or m.mode == UNSET_MODE:
        return ""  # not loaded, or never opted in: the persona layer is OPT-IN per member
    if m.minor and not MINORS_GET_PERSONA:
        return ""
    return render_persona_block(rec, mode=m.mode, minor=m.minor)


def apply_to_prompt(system_prompt: str, fixed_block: str, user_id: str = "") -> str:
    """Swap the fixed persona paragraphs for the rendered block. Returns ``system_prompt`` itself
    (same object) whenever the flag is off, nothing is loaded, or ``fixed_block`` is not a
    substring — a prompt edit that moved the text must degrade to today's persona, loudly once."""
    global _warned_missing_fixed
    if not enabled():
        return system_prompt
    try:
        block = block_for(user_id)
    except PersonaValidationError as exc:  # a stored row that breaks the minor rule: never render it
        logger.warning("persona_layer: refusing to render stored persona (%s); fixed persona stays", exc)
        return system_prompt
    if not block:
        return system_prompt
    if not fixed_block or fixed_block not in system_prompt:
        if not _warned_missing_fixed:
            _warned_missing_fixed = True
            logger.warning("persona_layer: fixed persona block not found in the system prompt; "
                           "ZOE_PERSONA_LAYER has no effect on this lane until it is re-pinned")
        return system_prompt
    return system_prompt.replace(fixed_block, block, 1)


# ── Storage (alembic 0035) ─────────────────────────────────────────────────────────────
@asynccontextmanager
async def _db(db=None):
    if db is not None:
        yield db
        return
    from db_pool import get_db_ctx  # type: ignore[import]

    async with get_db_ctx() as conn:
        yield conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def load_household(db=None) -> Optional[PersonaRecord]:
    """The stored household record, or ``None`` (= the default). A stored row that no longer
    validates is ignored with a warning — the default is safer than a half-trusted record."""
    async with _db(db) as conn:
        row = await (await conn.execute(
            "SELECT record_json FROM household_persona WHERE scope = ?", (HOUSEHOLD_SCOPE,))).fetchone()
    if row is None:
        return None
    try:
        return validate_persona(json.loads(row[0]))
    except (ValueError, TypeError) as exc:
        logger.warning("persona_layer: stored household persona is invalid (%s); using the default", exc)
        return None


async def save_household(record: PersonaRecord, updated_by: str, db=None) -> str:
    """Persist a VALIDATED record. Only ``routers/persona.py`` calls this (static scan test)."""
    async with _db(db) as conn:
        await conn.execute(
            """INSERT INTO household_persona (scope, record_json, version, updated_by, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(scope) DO UPDATE SET record_json = excluded.record_json,
                   version = excluded.version, updated_by = excluded.updated_by,
                   updated_at = excluded.updated_at""",
            (HOUSEHOLD_SCOPE, json.dumps(record.to_dict(), sort_keys=True), record.version,
             updated_by or "", _now()),
        )
        await conn.commit()
    invalidate_snapshot()
    return record.version


async def reset_household(db=None) -> None:
    async with _db(db) as conn:
        await conn.execute("DELETE FROM household_persona WHERE scope = ?", (HOUSEHOLD_SCOPE,))
        await conn.commit()
    invalidate_snapshot()


async def load_member_mode(user_id: str, db=None) -> MemberMode:
    """The member's stored mode; ``MemberMode()`` (UNSET: not opted in, fixed persona) when there is
    no row — every existing member, children included, starts here. A stored row that breaks the
    minor rule is treated as the safest valid state (a minor, ``kid``)."""
    async with _db(db) as conn:
        row = await (await conn.execute(
            "SELECT mode, minor FROM member_modes WHERE user_id = ?", (user_id,))).fetchone()
    if row is None:
        return MemberMode()
    try:
        return validate_member_mode(row[0], bool(row[1]))
    except PersonaValidationError:
        logger.warning("persona_layer: member_modes row for %s is invalid; treating as kid", user_id)
        return MemberMode(mode="kid", minor=True)


async def save_member_mode(user_id: str, member: MemberMode, updated_by: str, db=None) -> MemberMode:
    validated = validate_member_mode(member.mode, member.minor)
    async with _db(db) as conn:
        await conn.execute(
            """INSERT INTO member_modes (user_id, mode, minor, updated_by, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET mode = excluded.mode, minor = excluded.minor,
                   updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
            (user_id, validated.mode, 1 if validated.minor else 0, updated_by or "", _now()),
        )
        await conn.commit()
    invalidate_snapshot()
    return validated


async def reset_member_mode(user_id: str, db=None) -> None:
    async with _db(db) as conn:
        await conn.execute("DELETE FROM member_modes WHERE user_id = ?", (user_id,))
        await conn.commit()
    invalidate_snapshot()
