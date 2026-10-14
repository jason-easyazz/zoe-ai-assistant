"""calc_speech - spoken arithmetic: parse it safely, answer it the way a person would say it.

Live 2026-10-15: "what's two plus two", "what about 100 times 100", "that one divided by 3.5" and "what about 3 times pi" all went to
the 4B brain (one at 3.2 s first token) because the only calculator, ``intent_router._execute_calculate``, took DIGITS only and
answered in symbols ("15 * 4 = 60"). This module is that calculator, rebuilt for speech:

* number words ("two", "twenty one", "a hundred", "three point five", "5 million"), digits, "1,000"
* the constants pi and e
* operators as symbols or words: + - * x / × ÷, plus, minus, times, multiplied by, divided by, over, squared, cubed,
  square root of, square of, percent of
* follow-ups that point at the previous result inside a short window: "that", "that one", "the answer", "it" ("that one divided by
  3.5"), and an operator-led "times three" / "and plus ten"
* a spoken reply: "Two plus two is four." / "That's about 2,857.14." (small whole numbers in words, big ones grouped, a rounded
  answer says "about")

SAFETY: this is a tiny recursive-descent parser over a closed vocabulary. It never calls ``eval`` / ``exec`` / ``compile`` / ``ast``
(a test pins that), a word it does not know rejects the WHOLE utterance, and every intermediate value is bounded. The match is
whole-utterance anchored: "what's two plus two in French", "times are hard" and "pi day" are not arithmetic and return ``None``.

Pure apart from one bounded in-memory map of the last result per conversation (``remember`` / ``recall``). NEVER raises.
"""
from __future__ import annotations

import logging
import math
import re
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from fractions import Fraction
from typing import Optional, Union

logger = logging.getLogger(__name__)

Num = Union[Fraction, float]

#: how long "that one" keeps pointing at the last result (seconds)
WINDOW_S = 180.0
_MAX_KEYS = 256
#: |value| above this is "too big to say" (and aborts evaluation early)
_MAX_ABS = 1e15
_MAX_CHARS = 140
_MAX_TOKENS = 40

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}
_SCALES = {"thousand": 10 ** 3, "million": 10 ** 6, "billion": 10 ** 9}
_NUM_WORDS = set(_UNITS) | set(_TENS) | set(_SCALES) | {"hundred", "and", "point"}
_SMALL_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
                "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"]

_CONSTS = {"pi": math.pi, "e": math.e}

# multi-word operators / references, longest first: (word sequence) -> token
_PHRASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("the", "square", "root", "of"), "sqrt"),
    (("square", "root", "of"), "sqrt"),
    (("the", "square", "of"), "sq"),
    (("square", "of"), "sq"),
    (("multiplied", "by"), "*"),
    (("divided", "by"), "/"),
    (("percent", "of"), "pctof"),
    (("per", "cent", "of"), "pctof"),
    (("%", "of"), "pctof"),
    (("the", "last", "answer"), "ref"),
    (("the", "last", "result"), "ref"),
    (("the", "last", "one"), "ref"),
    (("the", "previous", "answer"), "ref"),
    (("the", "previous", "result"), "ref"),
    (("the", "previous", "one"), "ref"),
    (("that", "one"), "ref"),
    (("that", "answer"), "ref"),
    (("that", "result"), "ref"),
    (("that", "number"), "ref"),
    (("that", "total"), "ref"),
    (("the", "answer"), "ref"),
    (("the", "result"), "ref"),
    (("the", "total"), "ref"),
    (("the", "sum"), "ref"),
)
_WORD_OPS = {"plus": "+", "minus": "-", "times": "*", "over": "/", "x": "*", "squared": "squared", "cubed": "cubed",
             "that": "ref", "it": "ref", "ans": "ref", "negative": "-"}
_BINARY = {"+", "-", "*", "/", "pctof"}
#: an operator word that may open a follow-up with the previous result left implicit ("times three", "and plus ten")
_IMPLICIT_LEADS = frozenset({"plus", "minus", "times", "multiplied", "divided"})

_LEAD_RE = re.compile(
    r"^(?:(?:hey|ok|okay|um|uh|so|well|and|now|then|zoe|please|right)[\s,]+)*"
    r"(?:(?:can|could|would|will) you (?:please )?)?"
    r"(?:(?:what is|what would be|what are|how much is|how much would be|how much are|calculate|compute|work out|figure out"
    r"|tell me|give me|what does|what do you get for|what do you get if you)\s+)*"
    r"(?:(?:what|how) about\s+)?"
    r"(?:(?:the )?(?:answer|result|sum|total|product|quotient|difference) (?:of|to|for)\s+)?")
_ASK_LEAD_RE = re.compile(r"\b(?:what is|what would be|what are|how much|calculate|compute|work out|figure out|tell me|give me|what does"
                          r"|what do you|answer|result|sum|total|product|quotient|difference)\b")
_TAIL_RE = re.compile(r"(?:[\s,]+(?:equals?|please|for me|thanks|thank you|then|now|again))*[\s=.]*$")
_CONTRACTIONS = ((r"\bwhat's\b", "what is"), (r"\bwhats\b", "what is"), (r"\bthat's\b", "that is"), (r"\bhow's\b", "how is"))
_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?|\.\d+|[a-z]+|[-+*/%^]|\S")
_DIGITS_RE = re.compile(r"\d+(?:\.\d+)?|\.\d+")


class _Reject(Exception):
    """Not (valid) arithmetic - the parser gave up."""


class _Math(Exception):
    """Valid arithmetic with no spoken answer (divide by zero, root of a negative, too big)."""

    def __init__(self, reply: str) -> None:
        super().__init__(reply)
        self.reply = reply


@dataclass(frozen=True)
class Calc:
    reply: str          # what to say
    value: Optional[float]   # the number to remember for a follow-up (None when the answer was an error)
    uses_ref: bool      # the sum pointed at the previous result
    canonical: str      # digits-and-words form of the sum, re-parsable ("2 plus 2")


# ── tokens ───────────────────────────────────────────────────────────────────

def _prep(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "").lower().strip().replace("\u2019", "'")
    s = s.replace("×", " * ").replace("÷", " / ").replace("−", "-").replace("–", "-")
    s = re.sub(r"[?!]+", " ", s)
    for pat, rep in _CONTRACTIONS:
        s = re.sub(pat, rep, s)
    s = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", s)           # twenty-one
    s = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", s)         # 1,000 -> 1000
    s = s.replace(",", " ")
    return re.sub(r"\s+", " ", s).strip()


def _tokens(s: str) -> list[str]:
    raw = _TOKEN_RE.findall(s)
    out: list[str] = []
    i = 0
    while i < len(raw):
        for seq, tok in _PHRASES:
            n = len(seq)
            if tuple(raw[i:i + n]) == seq:
                out.append(tok)
                i += n
                break
        else:
            w = raw[i]
            if w == "the" and i + 1 < len(raw) and raw[i + 1] in ("square",):
                i += 1
                continue
            out.append(w)
            i += 1
    return out


def _map_ops(toks: list[str]) -> list[str]:
    """Words that are operators become their symbol; everything else is kept for the number / constant parsers."""
    out = []
    for t in toks:
        if t in ("plus", "minus", "times", "over", "x", "negative"):
            out.append(_WORD_OPS[t])
        elif t in ("that", "it", "ans"):
            out.append("ref")
        else:
            out.append(t)
    return out


# ── numbers ──────────────────────────────────────────────────────────────────

def _parse_number(toks: list[str], i: int) -> Optional[tuple[Fraction, int]]:
    n = len(toks)
    if i >= n:
        return None
    t = toks[i]
    if _DIGITS_RE.fullmatch(t):
        val = Fraction(t)
        i += 1
        if i < n and toks[i] in _SCALES:
            val *= _SCALES[toks[i]]
            i += 1
        elif i < n and toks[i] == "hundred":
            val *= 100
            i += 1
        return val, i
    if t not in _NUM_WORDS and not (t == "a" and i + 1 < n and (toks[i + 1] in _SCALES or toks[i + 1] == "hundred")):
        return None
    total = 0
    cur = 0
    seen = False
    j = i
    if toks[j] == "a":
        cur, j, seen = 1, j + 1, True
    while j < n:
        w = toks[j]
        if w in _UNITS:
            v = _UNITS[w]
            if cur % 100 == 0:
                cur += v
            elif 20 <= cur % 100 <= 90 and cur % 10 == 0 and 0 < v < 10:
                cur += v
            else:
                break
        elif w in _TENS:
            if cur % 100 != 0:
                break
            cur += _TENS[w]
        elif w == "hundred":
            if not (0 < cur < 100):
                break
            cur *= 100
        elif w in _SCALES:
            if cur <= 0 and not seen:
                break
            total += (cur or 1) * _SCALES[w]
            cur = 0
        elif w == "and":
            if not (seen and j + 1 < n and (toks[j + 1] in _UNITS or toks[j + 1] in _TENS)):
                break
            j += 1
            continue
        elif w == "point":
            break
        else:
            break
        seen = True
        j += 1
    if not seen:
        return None
    val = Fraction(total + cur)
    if j < n and toks[j] == "point":                       # "three point five"
        k = j + 1
        digits = ""
        while k < n and toks[k] in _UNITS and _UNITS[toks[k]] < 10:
            digits += str(_UNITS[toks[k]])
            k += 1
        if not digits:
            return None
        val += Fraction(int(digits), 10 ** len(digits))
        j = k
    return val, j


# ── grammar ──────────────────────────────────────────────────────────────────
# expr    := term (('+'|'-') term)*
# term    := unary (('*'|'/'|'pctof') unary)*
# unary   := '-' unary | ('sqrt'|'sq') unary | primary ('squared'|'cubed')*
# primary := number | pi | e | ref

class _Parser:
    def __init__(self, toks: list[str], has_prev: bool) -> None:
        self.t = toks
        self.i = 0
        self.has_prev = has_prev
        self.uses_ref = False
        self.ops = 0

    def peek(self) -> Optional[str]:
        return self.t[self.i] if self.i < len(self.t) else None

    def expr(self):
        node = self.term()
        while self.peek() in ("+", "-"):
            op = self.t[self.i]
            self.i += 1
            node = ("bin", op, node, self.term())
            self.ops += 1
        return node

    def term(self):
        node = self.unary()
        while self.peek() in ("*", "/", "pctof"):
            op = self.t[self.i]
            self.i += 1
            node = ("bin", op, node, self.unary())
            self.ops += 1
        return node

    def unary(self):
        p = self.peek()
        if p == "-":
            self.i += 1
            return ("neg", self.unary())
        if p in ("sqrt", "sq"):
            self.i += 1
            self.ops += 1
            return ("fn", p, self.unary())
        node = self.primary()
        while self.peek() in ("squared", "cubed"):
            node = ("pow", node, 2 if self.t[self.i] == "squared" else 3)
            self.i += 1
            self.ops += 1
        return node

    def primary(self):
        p = self.peek()
        if p is None:
            raise _Reject
        if p == "ref":
            if not self.has_prev:
                raise _Reject
            self.i += 1
            self.uses_ref = True
            return ("ref",)
        if p in _CONSTS:
            self.i += 1
            return ("const", p)
        got = _parse_number(self.t, self.i)
        if got is None:
            raise _Reject
        val, self.i = got
        return ("num", val)


# ── evaluation ───────────────────────────────────────────────────────────────

def _bound(v: Num) -> Num:
    if abs(v) > _MAX_ABS:
        raise _Math("That's too big for me to say out loud.")
    return v


def _exact_sqrt(f: Fraction) -> Optional[Fraction]:
    a, b = math.isqrt(f.numerator), math.isqrt(f.denominator)
    if a * a == f.numerator and b * b == f.denominator:
        return Fraction(a, b)
    return None


def _solve(node, prev: Optional[float]) -> Num:
    kind = node[0]
    if kind == "num":
        return _bound(node[1])
    if kind == "const":
        return _CONSTS[node[1]]
    if kind == "ref":
        if prev is None:
            raise _Reject
        return _bound(prev)
    if kind == "neg":
        return -_solve(node[1], prev)
    if kind == "pow":
        v = _solve(node[1], prev)
        return _bound(v ** node[2])
    if kind == "fn":
        v = _solve(node[2], prev)
        if node[1] == "sq":
            return _bound(v * v)
        if v < 0:
            raise _Math("I can't take the square root of a negative number.")
        if isinstance(v, Fraction):
            ex = _exact_sqrt(v)
            if ex is not None:
                return ex
        return math.sqrt(float(v))
    op, a, b = node[1], _solve(node[2], prev), _solve(node[3], prev)
    if op == "+":
        return _bound(a + b)
    if op == "-":
        return _bound(a - b)
    if op == "*":
        return _bound(a * b)
    if op == "pctof":
        return _bound(a / 100 * b)
    if b == 0:
        raise _Math("I can't divide by zero.")
    return _bound(a / b)


# ── speech ───────────────────────────────────────────────────────────────────

def _is_small_int(v: Num) -> bool:
    return isinstance(v, Fraction) and v.denominator == 1 and 0 <= v <= 20


def _fmt(v: Num, *, words: bool = False) -> tuple[str, bool]:
    """``(text, exact)`` for a number: whole numbers grouped, others to 2 places (3 significant figures under 1)."""
    if words and _is_small_int(v):
        return _SMALL_WORDS[int(v)], True
    neg = v < 0
    a = abs(v)
    f = a if isinstance(a, Fraction) else Fraction(a).limit_denominator(10 ** 12)
    if a == 0:
        return "0", True
    if f.denominator == 1:
        text, exact = f"{int(f):,}", True
    elif a >= 1:
        text, exact = _round_text(f, 2)
    else:
        dp = min(8, 2 - math.floor(math.log10(float(a))))
        text, exact = _round_text(f, dp)
    if text in ("0", ""):
        text, exact = "0", False
    return ("minus " + text if neg and text != "0" else text), exact


def _round_text(f: Fraction, dp: int) -> tuple[str, bool]:
    scaled = f * 10 ** dp
    rounded = int(scaled + Fraction(1, 2))
    exact = abs(scaled - rounded) < Fraction(1, 10 ** 6) if isinstance(f, Fraction) else False
    whole, frac = divmod(rounded, 10 ** dp)
    s = f"{whole:,}"
    fs = str(frac).rjust(dp, "0").rstrip("0")
    return (s + ("." + fs if fs else "")), exact


_BIN_WORDS = {"+": "plus", "-": "minus", "*": "times", "/": "divided by", "pctof": "percent of"}


def _say(node, words: bool) -> str:
    kind = node[0]
    if kind == "num":
        return _fmt(node[1], words=words)[0]
    if kind == "const":
        return "pi" if node[1] == "pi" else "the number e"
    if kind == "ref":
        return "that"
    if kind == "neg":
        return "minus " + _say(node[1], words)
    if kind == "pow":
        return f"{_say(node[1], words)} {'squared' if node[2] == 2 else 'cubed'}"
    if kind == "fn":
        return ("the square root of " if node[1] == "sqrt" else "the square of ") + _say(node[2], words)
    return f"{_say(node[2], words)} {_BIN_WORDS[node[1]]} {_say(node[3], words)}"


def _all_small(node) -> bool:
    kind = node[0]
    if kind == "num":
        return _is_small_int(node[1])
    if kind in ("const", "ref"):
        return False
    if kind in ("neg", "pow"):
        return _all_small(node[1])
    if kind == "fn":
        return _all_small(node[2])
    return _all_small(node[2]) and _all_small(node[3])


# ── public API ───────────────────────────────────────────────────────────────

def parse(text: str, prev: Optional[float] = None) -> Optional[Calc]:
    """The spoken answer to an arithmetic utterance, else ``None``. ``prev`` is the last result of this conversation (inside the
    window): without it a reference ("that one") is not arithmetic and the utterance is not claimed. NEVER raises."""
    try:
        return _parse(text, prev)
    except (_Reject, _Math, RecursionError, OverflowError, ValueError, ZeroDivisionError):
        return None
    except Exception as exc:  # noqa: BLE001 - a calculator must never break a turn
        logger.debug("calc_speech: parse failed (non-fatal): %s", type(exc).__name__)
        return None


def _parse(text: str, prev: Optional[float]) -> Optional[Calc]:
    s = _prep(text)
    if not s or len(s) > _MAX_CHARS:
        return None
    lead = _LEAD_RE.match(s)
    asked = bool(lead and _ASK_LEAD_RE.search(lead.group(0)))   # "what is ..." opens a fresh sum, never a follow-up
    s = s[lead.end():] if lead else s
    s = _TAIL_RE.sub("", s, count=1).strip()
    if not s:
        return None
    toks = _map_ops(_tokens(s))
    if not toks or len(toks) > _MAX_TOKENS:
        return None
    has_prev = prev is not None
    # an operator word opening a follow-up leaves "that" implicit: "times three" / "plus ten"
    if has_prev and not asked and s.split()[0] in _IMPLICIT_LEADS and toks[0] in _BINARY and len(toks) > 1:
        toks = ["ref"] + toks
    p = _Parser(toks, has_prev)
    tree = p.expr()
    if p.i != len(toks) or p.ops == 0:
        return None
    try:
        value = _solve(tree, prev)
    except _Math as m:
        return Calc(m.reply, None, p.uses_ref, _say(tree, False))
    return Calc(_reply(tree, value, p.uses_ref), float(value), p.uses_ref, _say(tree, False))


def _reply(tree, value: Num, uses_ref: bool) -> str:
    words = _all_small(tree) and _is_small_int(value)
    text, exact = _fmt(value, words=words)
    about = "" if exact else "about "
    if uses_ref:
        return f"That's {about}{text}."
    lead = _say(tree, words)
    return f"{lead[:1].upper()}{lead[1:]} is {about}{text}."


# ── the last result of a conversation ────────────────────────────────────────

_last: "OrderedDict[str, tuple[float, float]]" = OrderedDict()


def remember(key: str, value: float, *, now: Optional[float] = None) -> None:
    """Keep ``value`` as the conversation's last result. Bounded; NEVER raises."""
    try:
        if not key or value is None or not math.isfinite(value):
            return
        _last[key] = (float(value), time.monotonic() if now is None else now)
        _last.move_to_end(key)
        while len(_last) > _MAX_KEYS:
            _last.popitem(last=False)
    except Exception:  # noqa: BLE001
        pass


def recall(key: str, *, now: Optional[float] = None) -> Optional[float]:
    """The conversation's last result when it is inside the window, else ``None``."""
    hit = _last.get(key or "")
    if hit is None:
        return None
    if (time.monotonic() if now is None else now) - hit[1] > WINDOW_S:
        _last.pop(key, None)
        return None
    return hit[0]


def forget(key: Optional[str] = None) -> None:
    """Drop one conversation's last result (all of them with no key). For tests and forget-me flows."""
    if key is None:
        _last.clear()
    else:
        _last.pop(key, None)


def answer(text: str, key: str, *, now: Optional[float] = None) -> Optional[str]:
    """Reply to ``text`` for the conversation ``key`` and remember the result for the next turn; ``None`` when it is not arithmetic."""
    res = parse(text, recall(key, now=now))
    if res is None:
        return None
    if res.value is not None:
        remember(key, res.value, now=now)
    return res.reply
