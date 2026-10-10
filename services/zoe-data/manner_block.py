"""The manner block - how Zoe behaves WITH a person, as a short counted prompt block (flag-dark ``ZOE_MANNER_BLOCK=off|on``).

WHAT IT IS. A block of at most ``MAX_BLOCK_TOKENS`` (160) tokens that rides the system prompt of the live Flue brain (the 4B day
brain). Every clause is a behaviour a rater can COUNT (MITI / EPITOME style, ``docs/research/person-likeness-2026-10-09.md``
section 3.3 and the blueprint's borrowed-pieces table): reflect what you heard before advising, name one specific true thing,
one question at a time, no hooks, disagree once kindly about a stated risk, no flattery. The person bench
(``scripts/perf/samantha_person.py``) holds the cells that count them; ``scripts/perf/manner_block_ab.py`` runs the block on and
off on the live brain. The flag ships ``off`` and flips only on those numbers (``docs/knowledge/samantha-person.md``).

WHERE THE WORDS LIVE. ``lexicons_data/<lang>.json`` under ``"manner": {"block": ...}`` - data, not code (``lexicons.py``):
English and Spanish exist, and a language with no entry gets the English block (an instruction read BY the model, never a
matcher run over the user's text, so there is nothing to guess). The language is read from the turn's own words
(``lexicons.detect``); the block is byte-stable per language, which is what keeps llama-server's prefix cache warm.

HOW IT TRAVELS. Exactly as the household persona does (``persona_layer`` / ``zoe_flue_client._persona_context_block``): zoe-data
renders the block per turn and forwards it on one ``zoe-manner:<JSON string>`` envelope line; the sidecar
(``labs/flue-zoe-brain-2x/src/manner.ts``) strips it and appends the block to THAT turn's system prompt, after the doctrines and
before the user-model card. It is never a mid-conversation message (the Gemma template folds the system text into the first user
turn). Flag off, a minor, a guest, a failed lookup, a 1.x sidecar => NO line => the wire bytes are exactly today's.

ADULTS ONLY. The block must never reach a child before the deterministic distress hand-off ships (blueprint question 7, emotional
safety note section 7). It is withheld from: a guest / anonymous / voice-daemon id (an unconfirmed voice may be a child), a member
whose ``member_modes`` row says ``minor`` or ``kid``, a member whose ``auth_users.role`` is a child role, and - failing closed -
any member whose lookup fails or takes longer than ``LOOKUP_BUDGET_S``. Synthetic bench ids (``demo_*`` / ``test_*``) carry no age
and get the block so the bench can measure it. A real member with NO ``member_modes`` row and an adult role is treated as an
adult: the owner sets the minor rows BEFORE flipping the flag (``docs/knowledge/samantha-person.md``).

Nothing here stores anything. Stdlib only apart from the lazy DB import.
"""
from __future__ import annotations

import asyncio
import logging
import math

logger = logging.getLogger(__name__)

FLAG = "ZOE_MANNER_BLOCK"
MODES = ("off", "on")
LEXICON_KEY = "manner"
FALLBACK_LANG = "en"

#: the record's budget for the whole block, in tokens (chars/4, the repo convention; ``estimate_tokens``)
MAX_BLOCK_TOKENS = 160
MAX_BLOCK_CHARS = MAX_BLOCK_TOKENS * 4
#: the wire-level ceiling the sidecar mirrors (``MANNER_MAX_CHARS`` in manner.ts): above the block so a longer language fits
WIRE_MAX_CHARS = 800

#: ``auth_users.role`` values that mean "a child" (zoe-auth passes roles through verbatim; ``tests/test_ws_roles.py`` lists them)
CHILD_ROLES = frozenset({"child", "kid", "minor", "teenager", "teen"})
MINOR_MODES = frozenset({"kid"})
#: a cold lookup is two primary-key reads; the first token never waits longer than this for them
LOOKUP_BUDGET_S = 0.5

_TRUTHY = frozenset({"on", "1", "true", "yes"})


def mode() -> str:
    """``ZOE_MANNER_BLOCK`` - ``off`` (default) or ``on``; read per call, so a flip needs no restart. Anything else is ``off``."""
    from typed_env import env_str

    raw = env_str("ZOE_MANNER_BLOCK", "off").lower()  # literal on purpose: tools/audit/flag_inventory.py greps it
    return "on" if raw in _TRUTHY else "off"


def enabled() -> bool:
    return mode() == "on"


def estimate_tokens(text: str) -> int:
    """chars/4 rounded up - the repo's convention (``persona_layer.estimate_tokens``); the real tokenizer count is measured in the A/B."""
    return math.ceil(len(text or "") / 4)


def block_text(lang: str) -> str:
    """The block for ``lang`` from ``lexicons_data/<lang>.json``, the English one when that language has none, ``""`` when even
    English has none or the text is over budget (a block that does not fit is dropped, never truncated)."""
    import lexicons

    for code in (lang, FALLBACK_LANG):
        entry = (lexicons.load(code) or {}).get(LEXICON_KEY) or {}
        text = str(entry.get("block") or "").strip() if isinstance(entry, dict) else ""
        if text:
            if estimate_tokens(text) > MAX_BLOCK_TOKENS or len(text) > WIRE_MAX_CHARS:
                logger.warning("MANNER_BLOCK %s text is over the %d-token budget; no block", code, MAX_BLOCK_TOKENS)
                return ""
            return text
    return ""


def language_of(message: str) -> str:
    """The language of the turn's own words (a script / function-word read; ``en`` when there is no evidence)."""
    import lexicons

    return lexicons.detect(message or "") or FALLBACK_LANG


def is_guest(user_id: str) -> bool:
    from user_filters import GUEST_USERS

    return (user_id or "").strip() in GUEST_USERS


def is_synthetic(user_id: str) -> bool:
    from user_filters import is_synthetic_user

    return is_synthetic_user((user_id or "").strip()) and not is_guest(user_id)


async def _is_minor(user_id: str, db=None) -> bool:
    """True when the member is flagged a minor (``member_modes``) or holds a child role (``auth_users``). Raises on a failed lookup -
    the caller fails CLOSED."""
    import persona_layer

    member = await persona_layer.load_member_mode(user_id, db=db)
    if member.minor or member.mode in MINOR_MODES:
        return True
    async with persona_layer._db(db) as conn:
        row = await (await conn.execute("SELECT role FROM auth_users WHERE user_id = ?", (user_id,))).fetchone()
    return bool(row) and str(row[0] or "").strip().lower() in CHILD_ROLES


async def eligible(user_id: str, db=None) -> bool:
    """May this member's turn carry the block? Adults only; guests and every lookup failure say no."""
    uid = (user_id or "").strip()
    if not uid or is_guest(uid):
        return False
    if is_synthetic(uid):
        return True
    try:
        return not await asyncio.wait_for(_is_minor(uid, db=db), timeout=LOOKUP_BUDGET_S)
    except Exception:  # noqa: BLE001 - includes the timeout: not knowing a member is an adult means no block
        logger.warning("MANNER_BLOCK lookup failed for %s; no block", uid, exc_info=True)
        return False


async def block_for(user_id: str, message: str = "", db=None) -> str:
    """The block for THIS member's turn, or ``""`` (= today's bytes). Never raises; flag off => ``""`` before any read."""
    try:
        if not enabled():
            return ""
        if not await eligible(user_id, db=db):
            return ""
        return block_text(language_of(message))
    except Exception:  # noqa: BLE001 - a manner is optional; the turn is not
        logger.warning("MANNER_BLOCK failed; no block", exc_info=True)
        return ""
