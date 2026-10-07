"""Roles are stated, never guessed - on the ASK path too (Samantha bar S22, ZOE_ROLE_GUESS_GUARD).

``people_roles`` keeps a guessed role out of the STORE (extractors) and out of the roster reply.
It did nothing for the later ASK: since the named-person recall floor (#1899) a question like
"Who is Anika Reyes?" reaches memory, the packet carries the person's row ("Anika Reyes: 2
November 1985" - a name and a date, NO role) and the 4B brain, asked who she is, filled the gap
from the name: "Anika Reyes is your mother". Measured 2026-10-07: 3 of 4 live runs, with an empty
role set in the store and in the packet. The packet never said it; the model invented it.

Two halves, both evidence-gated (they act only on a turn where the named-person floor fired):

* ``rule_line`` - one line appended INSIDE the recall block when a named person's relationship to
  the user is stated by no packet row: ``Relationship not stated for: ...``. This is the explicit
  "role: unknown" marker plus the rule, where the brain reads its facts.
* ``neutralise`` / ``filter_stream`` - the deterministic backstop for a model that ignores the
  rule: a reply that makes a named person the holder of a family/partner role the packet and the
  user's own message never tie to that person is rewritten to a neutral phrasing ("is someone
  you've told me about") plus ONE who's-who question. Text is buffered only on such a turn;
  every other turn streams byte-identical.

``ZOE_ROLE_GUESS_GUARD`` = on (default) | shadow (logs, rewrites nothing) | off (per call). Stdlib
plus ``people_roles``; never raises.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, AsyncIterator, Iterable, Optional

import people_roles as _pr

logger = logging.getLogger(__name__)

ENV = "ZOE_ROLE_GUESS_GUARD"

# Relationship roles a name can never decide. "friend" / "colleague" are loose labels, not a
# gender or family guess (people_roles._LOOSE_ROLES), so they are not policed here.
GUESS_ROLES = (
    "wife", "husband", "spouse", "partner", "girlfriend", "boyfriend", "fiancée", "fiancé",
    "fiancee", "fiance", "mother", "mum", "mom", "mummy", "mommy", "father", "dad", "daddy",
    "son", "daughter", "sister", "brother", "sibling", "aunt", "auntie", "aunty", "uncle", "niece",
    "nephew", "cousin", "grandmother", "grandma", "granny", "nan", "nana", "grandfather",
    "grandpa", "grandad", "parent", "grandparent", "child", "kid", "stepmother", "stepfather",
    "stepson", "stepdaughter", "mother-in-law", "father-in-law", "sister-in-law", "brother-in-law",
)
_ROLE = "|".join(sorted((re.escape(r) for r in GUESS_ROLES), key=len, reverse=True))
_DET = (r"(?:your|his|her|their|my|our|the|a|an|(?-i:[A-Z][\w-]{1,30}\s[A-Z][\w-]{1,30})['’]s"
        r"|[A-Z][\w'’-]{1,30}['’]s)")
# "is" and its hedged cousins - a modal or a "seems" only softens the guess, it is still a guess.
_ADV = r"(?:probably|likely|possibly|perhaps|maybe|presumably|apparently|surely|certainly|definitely|actually|really|just|also|still)"
_VERB = (rf"(?:(?:might|may|could|must|should|would|can)(?:\s+(?:well|{_ADV}))?\s+be"
         rf"|(?:seems?|seemed|appears?|appeared|sounds?|sounded|looks?|looked)(?:\s+{_ADV})?\s+(?:to\s+be|like)"
         rf"|(?:is|was|are|were)(?:\s+{_ADV})*)")
_NEUTRAL = "someone you've told me about"

RULE = ("Relationship not stated for: {names}. Roles are stated, never guessed - do not call "
        "them anyone's wife, husband, partner, mother, father, son, daughter or any other "
        "relative, and do not infer one from their name. Say what is recorded about them "
        "and ask how they are related to the user.")


def mode() -> str:
    """``on`` (default, unset/1/true) | ``shadow`` | ``off`` - per-call env read."""
    raw = (os.environ.get("ZOE_ROLE_GUESS_GUARD") or "").strip().lower()
    if raw in ("0", "false", "no", "off", "disabled"):
        return "off"
    if raw == "shadow":
        return "shadow"
    return "on"


# -- who is named, and what the evidence states ---------------------------------

def _handles(names: Iterable[str]) -> list[tuple[str, str]]:
    """``[(handle, full name)]``: each full name, plus a first name when it is unique among
    ``names`` (a reply says "Anika" as often as "Anika Reyes")."""
    full = [n.strip() for n in names if n and n.strip()]
    firsts: dict[str, list[str]] = {}
    for n in full:
        firsts.setdefault(n.split()[0].lower(), []).append(n)
    out = [(n, n) for n in full]
    for f, owners in firsts.items():
        if len(owners) == 1 and len(f) >= 3 and len(owners[0].split()) > 1:
            out.append((owners[0].split()[0], owners[0]))
    return sorted(out, key=lambda h: -len(h[0]))


def stated_roles(name: str, evidence: str) -> list[str]:
    """The ``GUESS_ROLES`` the evidence text ties to ``name`` (the user's own words, or a stored
    row that says so)."""
    evidence = _mask_other_people(name, evidence or "")
    return [r for r in GUESS_ROLES if _pr.role_assignment_supported(name, r, evidence)]


def unstated_people(names: Iterable[str], packet: str) -> list[str]:
    """The named people whose packet rows state no relationship role for them."""
    return [n for n in names if n and n.strip() and not stated_roles(n, packet)]


def rule_line(names: Iterable[str], packet: str) -> str:
    """The explicit ``role: unknown`` marker + rule for the recall block ('' when every named
    person has a stated role, the guard is off, or nobody is named)."""
    if mode() == "off":
        return ""
    unknown = unstated_people(names, packet)
    if not unknown:
        return ""
    return RULE.format(names=", ".join(unknown))


# -- whose relative: the relationship OWNER ---------------------------------------
# A role is a relation BETWEEN two people. "Anika is Callum's wife" evidences Anika~wife for CALLUM;
# it licenses nothing about the user. So a claim is trusted only when the evidence ties the role to
# the same owner the claim names ("your" = the user; "Callum's" / "of Callum" = Callum). An owner the
# claim or the evidence leaves unspecified ("the wife", "a friend's wife") is not a conflict.

_USER_WORDS = frozenset({"user", "your", "my", "our", "you", "i", "me", "mine", "yours", "ours"})
_NAME_OWNER = r"(?:[A-Z][\w-]*\s+){0,2}[\w-]+"        # no apostrophe inside a token: it IS the possessive
_OWNER_POSS = re.compile(rf"(?P<own>{_NAME_OWNER})['\u2019]s\s+$")
_OWNER_DET = re.compile(r"\b(?P<own>your|my|our)\s+$", re.IGNORECASE)
_OWNER_OF = re.compile(r"^s?\s+(?:of|to)\s+(?:(?P<det>(?i:(?:your|my|our)(?:\s+[a-z]+)?|the\s+user|you|me|us))\b"
                       r"|(?P<own>(?:[A-Z][\w'\u2019-]*\s?){1,3}))")


def _is_relation(tok: str) -> bool:
    return tok in GUESS_ROLES or tok in _pr._LOOSE_ROLES


def _owner_key(raw: str):
    """One owner component: ``'user'`` | ``('name', tokens)`` | ``('rel', word)`` | ``None``."""
    toks = [t for t in re.findall(r"[\w-]+", raw.lower()) if t != "s"]
    if not toks:
        return None
    if len(toks) == 1 and toks[0] in _USER_WORDS:
        return "user"
    if len(toks) == 1 and _is_relation(toks[0]):
        return ("rel", toks[0])
    return ("name", tuple(toks))


def _owner_at(text: str, start: int, end: int):
    """The owner the text gives the role word at ``text[start:end]``: ``None`` (unspecified),
    ``'user'``, ``('name', tokens)`` (the COMPLETE named owner) or ``('rel', (words...))`` - the
    possessive chain of relation words. "User's friend's wife" and "your friend's wife" are both
    ``('rel', ('friend',))``: a leading user is implicit, so the two wordings compare equal."""
    m = _OWNER_OF.match(text[end:])
    if m:
        if m.group("det"):                     # "the wife of your brother" / "of the user" / "of me"
            parts = m.group("det").lower().split()
            if parts[0] in _USER_WORDS or parts[:2] == ["the", "user"]:
                rest = [] if parts[0] == "the" else parts[1:]
                return ("rel", (rest[0],)) if rest else "user"
        return _owner_key(m.group("own") or "")
    pos = text[:start]
    found = _OWNER_POSS.search(pos) or _OWNER_DET.search(pos)
    if not found:                              # "Callum's lovely wife": one adjective may sit between
        pos = re.sub(r"\w+\s+$", "", pos)
        found = _OWNER_POSS.search(pos) or _OWNER_DET.search(pos)
    comps: list = []
    while found:
        own = found.group("own")
        lead = own.split()[0].lower() if own.split() else ""
        if len(own.split()) > 1 and lead in _USER_WORDS:     # "User's friend" / "Your friend": the user, then a relation
            comps.append(_owner_key(own.split(None, 1)[1]))
            comps.append("user")
            break
        k = _owner_key(own)
        if k is None:
            break
        comps.append(k)
        if k == "user" or k[0] == "name":
            break
        pos = pos[:found.start()]
        found = _OWNER_POSS.search(pos) or _OWNER_DET.search(pos)
    if not comps:
        return None
    if comps[-1] == "user":
        comps.pop()
        if not comps:
            return "user"
    if comps[-1][0] == "name":                 # the outermost component is a named person
        return comps[-1]
    return ("rel", tuple(c[1] for c in reversed(comps)))


def _same_person(a: tuple, b: tuple) -> bool:
    """The COMPLETE owner: equal token sequences, or a bare given name against that person's full
    name ("Callum" / "Callum Reyes"). A shared surname or a shared given name alone is not a match."""
    if a == b:
        return True
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return len(short) == 1 and long_[0] == short[0]


def _owners_compatible(claim, evidenced: list) -> bool:
    if claim is None or not evidenced or None in evidenced:
        return True
    for e in evidenced:
        if claim == "user" and e == "user":
            return True
        if isinstance(claim, tuple) and isinstance(e, tuple) and claim[0] == e[0]:
            if claim[0] == "name" and _same_person(claim[1], e[1]):
                return True
            if claim[0] == "rel" and claim[1] == e[1]:
                return True
    return False


def _evidence_owners(name: str, role: str, evidence: str) -> list:
    """The owners the evidence gives ``role`` in the sentences that tie it to ``name``."""
    variants = {v.lower() for v in _pr._variants(role.lower())}
    role_rx = re.compile(r"\b(?:" + "|".join(sorted((re.escape(v) for v in variants), key=len, reverse=True)) + r")s?\b",
                         re.IGNORECASE)
    owners: list = []
    for sent in _pr._sentences(evidence):
        if not _pr.role_assignment_supported(name, role, sent):
            continue
        for rm in role_rx.finditer(sent):
            owners.append(_owner_at(sent, rm.start(), rm.end()))
    return owners


def _mask_other_people(name: str, evidence: str) -> str:
    """``people_roles`` matches a person by FIRST name only. Before asking it about "Anika Reyes",
    blank out every other full name that shares her first name ("Anika Patel"), so a role stated for
    Anika Patel cannot license one for Anika Reyes. A bare "Anika" still counts for her."""
    parts = name.split()
    if len(parts) < 2 or not evidence:
        return evidence
    want = [p.lower() for p in parts[1:]]

    def _sub(m: "re.Match[str]") -> str:
        got = [t.lower() for t in m.group("rest").split()]
        return m.group(0) if got[:len(want)] == want else "Zzother"

    return re.sub(rf"\b{re.escape(parts[0])}(?P<rest>(?:\s+[A-Z][\w-]*){{1,2}})", _sub, evidence)


def role_supported_for(name: str, role: str, evidence: str, owner="user") -> bool:
    """``name`` is ``role`` of ``owner`` (default: the user) per the evidence - the role AND whose."""
    evidence = _mask_other_people(name, evidence or "")
    if not _pr.role_assignment_supported(name, role, evidence):
        return False
    return _owners_compatible(owner, _evidence_owners(name, role, evidence))


def guarded_people(names: Iterable[str], packet: str = "") -> list[str]:
    """The people the reply is checked for: EVERY named person. A person with one stated role is
    still guarded - "User's sister is Anika" licenses "your sister", never "your mother" - so the
    role-specific evidence check (``role_supported_for``) decides each claim, not membership here."""
    return [n for n in names if n and n.strip()]


# -- what the user's own words can evidence -----------------------------------------
_HYPO_LEAD = re.compile(
    r"^\W*(?:is|are|was|were|am|do|does|did|can|could|would|should|will|may|might|who|whom|whose|what|which|"
    r"whether|if|maybe|perhaps|possibly|probably|i\s+(?:wonder|think|guess|suppose|believe|doubt|bet)|"
    r"not\s+sure|no\s+idea|i'?m\s+not\s+sure)\b", re.IGNORECASE)
_HYPO_ANY = re.compile(r"\b(?:if|whether|wonder|wondering|suppose|supposing|hypothetically|might\s+be|could\s+be)\b",
                       re.IGNORECASE)


def stated_text(user_text: str) -> str:
    """The part of the user's message that STATES things. A question ("Is my mother Anika?"), a
    wondering or a hypothetical ("maybe", "if", "I think") asks about a relationship; it never
    evidences one. Clause-wise, so "Anika is my mother, who is she again?" keeps its statement."""
    kept: list[str] = []
    for sent in _pr._sentences(user_text or ""):
        clauses = [c for c in re.split(r"\s*[,;]\s*|\s+(?:but|and)\s+", sent) if c.strip()]
        asks = sent.rstrip().endswith("?")
        for idx, c in enumerate(clauses):
            if _HYPO_LEAD.search(c) or _HYPO_ANY.search(c):
                continue
            if asks and len(clauses) == 1:     # "My mother is Anika?" - a question, not a statement
                continue
            kept.append(c.strip().rstrip("?"))
    return ". ".join(kept)


# -- the reply backstop -----------------------------------------------------------

def _claim_patterns(handle_alt: str) -> list[tuple[str, "re.Pattern[str]"]]:
    n = rf"(?P<n>{handle_alt})"
    return [
        # Anika Reyes is your mother / Anika was Callum's wife (+ "of Callum")
        ("holder", re.compile(
            rf"\b{n}\b(?P<mid>[^.!?\n]{{0,25}}?\b{_VERB}\s+)(?P<role>(?:{_DET}\s+)+"
            rf"(?:\w+\s+){{0,1}}(?P<r>{_ROLE})s?\b(?:\s+(?:of|to)\s+[A-Z][\w'’-]+(?:\s[A-Z][\w'’-]+)?)?)",
            re.IGNORECASE)),
        # Your mother is Anika Reyes / Callum's wife is Anika / your mother's name is Anika
        ("cop", re.compile(
            rf"(?P<role>\b(?:{_DET}\s+)+(?:\w+\s+){{0,1}}(?P<r>{_ROLE})s?(?:['’]s\s+name)?)"
            rf"(?P<mid>\s*,?\s*{_VERB}\s+(?:called\s+|named\s+)?){n}\b", re.IGNORECASE)),
        # She is your mother / He's Callum's brother - a pronoun on a guarded turn is the person asked about
        ("pron", re.compile(
            rf"\b(?P<pron>she|he|they)(?P<mid>(?:\s+{_VERB}|['’]s)\s+)(?P<role>(?:{_DET}\s+)+"
            rf"(?:\w+\s+){{0,1}}(?P<r>{_ROLE})s?\b)", re.IGNORECASE)),
        # your mother Anika / your mother, Anika / Callum's wife Anika
        ("pre", re.compile(rf"(?P<role>\b{_DET}\s+(?:\w+\s+){{0,1}}(?P<r>{_ROLE})s?,?\s+)(?={n}\b)",
                           re.IGNORECASE)),
        # Anika, your mother / Anika Reyes (your wife)
        ("post", re.compile(rf"\b{n}\b(?P<role>\s*,\s*{_DET}\s+(?:\w+\s+){{0,1}}(?P<r>{_ROLE})s?\b(?P<tc>,?)"
                            rf"|\s*\(\s*(?:{_DET}\s+)?(?:\w+\s+){{0,1}}(?P<r2>{_ROLE})s?\s*\))", re.IGNORECASE)),
    ]


def neutralise(reply: str, names: Iterable[str], packet: str, user_text: str = "") -> tuple[str, list[str]]:
    """``(reply, guessed)``: ``reply`` with every role the evidence never ties to a named person
    rewritten neutrally and ONE who's-who question added; ``guessed`` lists ``name~role`` per
    rewrite. Unchanged (and ``[]``) when nothing was guessed. Pure."""
    text = reply or ""
    names_l = list(names)
    handles = _handles(names_l)
    if not text.strip() or not handles:
        return text, []
    evidence = f"{packet or ''}\n{stated_text(user_text)}"
    fulls = list(dict.fromkeys(f for f in (n.strip() for n in names_l) if f))
    by_handle = {h.lower(): full for h, full in handles}
    alt = "|".join(re.escape(h) for h, _ in handles)
    guessed: list[str] = []
    asked: list[str] = []

    def _unsupported(handle: str, role: str, owner=None) -> Optional[str]:
        full = by_handle.get(handle.lower())
        if full and not role_supported_for(full, role.lower(), evidence, owner):
            return full
        return None

    out = text
    for kind, rx in _claim_patterns(alt):
        def sub(m: "re.Match[str]", kind: str = kind) -> str:
            grp = "r" if m.group("r") else "r2"
            role = m.group(grp) or ""
            owner = _owner_at(m.string, m.start(grp), m.end(grp))
            if kind == "pron":
                # no name to resolve: guilty only when NO named person is evidenced in that role
                if any(role_supported_for(f, role.lower(), evidence, owner) for f in fulls):
                    return m.group(0)
                full = fulls[0]
            else:
                full = _unsupported(m.group("n"), role, owner)
            if not full:
                return m.group(0)
            guessed.append(f"{full.split()[0].lower()}~{role.lower()}")
            if full not in asked:
                asked.append(full)
            if kind == "holder":
                return f"{m.group('n')}{m.group('mid')}{_NEUTRAL}"
            if kind == "cop":
                return f"{m.group('n')} is {_NEUTRAL}"
            if kind == "pron":
                return f"{m.group('pron')}{m.group('mid')}{_NEUTRAL}"
            if kind == "pre":
                return ""
            return m.group("n") + (" " if m.groupdict().get("tc") else "")
        out = rx.sub(sub, out)
    if not guessed:
        return text, []
    out = re.sub(r"\s{2,}", " ", out).replace(" ,", ",").replace(" .", ".").strip()
    if "related to you" not in out.lower():
        out += f" I haven't been told how {asked[0]} is related to you - could you tell me?"
    return out, guessed


async def filter_stream(turn: AsyncIterator[str], sink: dict[str, Any],
                        user_text: str = "") -> AsyncIterator[str]:
    """Wrap one brain turn. ``sink['names']`` / ``sink['packet']`` are filled by the recall
    block BEFORE the first reply delta; empty = this turn is not guarded and every delta passes
    through untouched. Otherwise the reply text is held (sentinels still pass at once), cleaned
    once, and emitted as one delta. Closing this closes the inner turn."""
    held: list[str] = []
    guarded: Optional[bool] = None
    try:
        async for delta in turn:
            if delta.startswith(("__TOOL__:", "__THINKING__:", "__UI__:")):
                yield delta
                continue
            if guarded is None:
                guarded = bool(sink.get("names")) and mode() != "off"
            if not guarded:
                yield delta
            else:
                held.append(delta)
        if held:
            raw = "".join(held)
            names, packet = sink.get("names") or [], sink.get("packet") or ""
            try:
                fixed, guessed = neutralise(raw, names, packet, user_text)
            except Exception as exc:  # noqa: BLE001 - the guard must never lose a reply
                logger.warning("role guess guard failed (%s) - reply passed through", type(exc).__name__)
                fixed, guessed = raw, []
            if guessed:
                logger.info("ROLE_GUESS_GUARD mode=%s people=%d guessed=%d", mode(), len(names), len(guessed))
            yield fixed if (guessed and mode() == "on") else raw
    finally:
        aclose = getattr(turn, "aclose", None)
        if aclose is not None:
            await aclose()
