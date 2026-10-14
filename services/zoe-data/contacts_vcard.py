"""vCard 4.0 read/write for the fields the contacts model maps (``docs/architecture/contacts-data-model.md`` section 4).

No vCard library is installed in zoe-data (no ``vobject`` / ``vcard`` in requirements), so this is a SMALL codec for the mapped
fields only - not a general vCard implementation. What it covers, per RFC 6350 (vCard 4.0) and RFC 9554 (the JSContact extensions):

  * content lines: unfolding (CRLF/LF + one space or tab), ``group.`` prefixes, parameters (quoted values, comma lists, repeated
    ``TYPE``), text escaping (``\\n \\, \\; \\\\``), structured values (``;`` components, ``,`` lists);
  * folding on write at 75 octets without splitting a UTF-8 sequence, CRLF line ends;
  * properties: FN, N (5 components, and RFC 9554's 7 with secondary surname + generation; ``ALTID`` + ``PHONETIC`` / ``SCRIPT``),
    NICKNAME, EMAIL, TEL (text or ``tel:`` URI), ADR (+ ``LABEL``), ORG, TITLE, URL, IMPP, SOCIALPROFILE (+ ``SERVICE-TYPE``), PHOTO
    (a URI reference only - an inline ``data:`` / ``ENCODING=b`` photo is DROPPED and counted, never stored), NOTE, CATEGORIES,
    PRONOUNS, BDAY / ANNIVERSARY (``YYYYMMDD``, ``YYYY-MM-DD``, ``--MMDD``, ``--MM-DD``), RELATED (text or URI), UID, plus Apple's
    ``X-PHONETIC-FIRST-NAME`` / ``X-PHONETIC-LAST-NAME`` and Zoe's own ``X-ZOE-SAY-AS`` (a pronunciation hint for TTS);
  * ``PREF`` (1..100, 1 = most preferred) and vCard 3's ``TYPE=pref`` (read as ``pref = 1``).

Everything else is ignored on read (counted in ``unmapped``) and never written. The card shape both directions use is the plain
dict documented on ``empty_card`` - JSContact-flavoured names (``emails``, ``phones``, ``addresses`` ...), one list per kind.
Pure: no I/O, no database.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

PRODID = "-//Zoe//contacts 0052//EN"

#: vCard RELATED ``TYPE`` values (RFC 6350 section 6.6.6) the reader accepts.
RELATED_TYPES = frozenset({"contact", "acquaintance", "friend", "met", "co-worker", "colleague", "co-resident", "neighbor", "child",
                           "parent", "sibling", "spouse", "kin", "muse", "crush", "date", "sweetheart", "me", "agent", "emergency"})


def empty_card() -> dict:
    """The card shape. Every list item is a dict; ``types`` is a list of lower-case words; ``pref`` is 1..100 or None."""
    return {
        "uid": None,
        "full_name": "",
        "name": {},          # given, given2, surname, surname2, title, credential, generation, phonetic{...}, phonetic_system,
                             # phonetic_script, say_as
        "nicknames": [], "emails": [], "phones": [], "addresses": [], "orgs": [], "titles": [], "urls": [], "socials": [],
        "ims": [], "photos": [], "notes": [], "groups": [], "pronouns": [],
        "dates": [],         # {"kind": "birthday"|"anniversary", "year": int|None, "month": int, "day": int}
        "related": [],       # {"name": str, "uri": str|None, "types": [...], "zoe_rel": str|None, "zoe_role": str|None}
        "unmapped": 0, "photos_inline_dropped": 0,
    }


# -- reading ------------------------------------------------------------------------------------------------------------------

def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in re.split(r"\r\n|\n|\r", text or ""):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw.strip():
            lines.append(raw)
    return lines


def _split_unescaped(value: str, sep: str) -> list[str]:
    out, cur, i = [], [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            cur.append(value[i:i + 2])
            i += 2
            continue
        if ch == sep:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    out.append("".join(cur))
    return out


def unescape(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _parse_line(line: str) -> Optional[tuple[str, dict, str]]:
    """``(NAME, {PARAM: [values]}, raw value)``; None for a line with no ':'."""
    in_q, colon = False, -1
    for i, ch in enumerate(line):
        if ch == '"':
            in_q = not in_q
        elif ch == ":" and not in_q:
            colon = i
            break
    if colon < 0:
        return None
    head, value = line[:colon], line[colon + 1:]
    parts, cur, in_q = [], [], False
    for ch in head:
        if ch == '"':
            in_q = not in_q
            cur.append(ch)
        elif ch == ";" and not in_q:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    name = parts[0].split(".")[-1].strip().upper()      # drop a ``group.`` prefix
    params: dict[str, list[str]] = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
        else:                                           # vCard 2.1 bare type ("TEL;CELL:")
            k, v = "TYPE", p
        vals, cur, in_q = [], [], False
        for ch in v:
            if ch == '"':
                in_q = not in_q
            elif ch == "," and not in_q:
                vals.append("".join(cur))
                cur = []
            else:
                cur.append(ch)
        vals.append("".join(cur))
        params.setdefault(k.strip().upper(), []).extend(x for x in vals if x != "")
    return name, params, value


def _types(params: dict) -> tuple[list[str], Optional[int]]:
    types, pref = [], None
    for t in params.get("TYPE", []):
        t = t.strip().lower()
        if t in ("internet", "x400"):         # vCard 3 email transport types: not a context
            continue
        if t == "pref":
            pref = 1
        elif t and t not in types:
            types.append(t)
    for p in params.get("PREF", []):
        try:
            n = int(p)
        except ValueError:
            continue
        if 1 <= n <= 100:
            pref = n
    return types, pref


def _one(params: dict, key: str) -> Optional[str]:
    vals = params.get(key)
    return vals[0] if vals else None


def parse_date(value: str) -> Optional[tuple[Optional[int], int, int]]:
    """``(year | None, month, day)`` from the RFC 6350 date forms; None if it is not a calendar date."""
    v = value.strip().split("T")[0]
    m = re.fullmatch(r"(\d{4})-?(\d{2})-?(\d{2})", v)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.fullmatch(r"--(\d{2})-?(\d{2})", v)
        if not m:
            return None
        y, mo, d = None, int(m.group(1)), int(m.group(2))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return y, mo, d


def _list(value: str) -> list[str]:
    return [unescape(x).strip() for x in _split_unescaped(value, ",") if unescape(x).strip()]


def _components(value: str) -> list[list[str]]:
    return [[unescape(x) for x in _split_unescaped(comp, ",")] for comp in _split_unescaped(value, ";")]


def _first(comps: list[list[str]], i: int) -> str:
    return (comps[i][0] if i < len(comps) and comps[i] else "").strip()


def _name_from_n(comps: list[list[str]]) -> dict:
    """RFC 6350 N (family; given; additional; prefixes; suffixes) + RFC 9554's secondary surname (6th) and generation (7th). A 9554
    reader ignores the copy a writer also put in the old component (family / suffixes) for back compatibility."""
    surname2 = _first(comps, 5)
    generation = _first(comps, 6)
    families = [x.strip() for x in (comps[0] if comps else []) if x.strip()]
    suffixes = [x.strip() for x in (comps[4] if len(comps) > 4 else []) if x.strip()]
    if surname2:
        families = [f for f in families if f != surname2]
    elif len(families) > 1:
        surname2 = families[1]
    if generation:
        suffixes = [s for s in suffixes if s != generation]
    name = {
        "surname": families[0] if families else "",
        "surname2": surname2,
        "given": _first(comps, 1),
        "given2": " ".join(x.strip() for x in (comps[2] if len(comps) > 2 else []) if x.strip()),
        "title": " ".join(x.strip() for x in (comps[3] if len(comps) > 3 else []) if x.strip()),
        "credential": " ".join(suffixes),
        "generation": generation,
    }
    return {k: v for k, v in name.items() if v}


def parse_vcards(text: str) -> list[dict]:
    """Every ``BEGIN:VCARD`` .. ``END:VCARD`` block in ``text`` as a card dict (``empty_card``'s shape)."""
    cards: list[dict] = []
    card: Optional[dict] = None
    for line in _unfold(text):
        parsed = _parse_line(line)
        if parsed is None:
            continue
        name, params, raw = parsed
        if name == "BEGIN" and raw.strip().upper() == "VCARD":
            card = empty_card()
            continue
        if card is None:
            continue
        if name == "END" and raw.strip().upper() == "VCARD":
            if not card["full_name"]:
                n = card["name"]
                card["full_name"] = " ".join(x for x in (n.get("given"), n.get("given2"), n.get("surname")) if x)
            cards.append(card)
            card = None
            continue
        types, pref = _types(params)
        text_value = unescape(raw).strip()
        if name in ("VERSION", "PRODID", "REV", "KIND"):
            continue
        if name == "UID":
            card["uid"] = text_value or None
        elif name == "FN":
            card["full_name"] = card["full_name"] or text_value
        elif name == "N":
            comps = _components(raw)
            if params.get("PHONETIC") or (params.get("SCRIPT") and params.get("ALTID")):
                ph = _name_from_n(comps)
                card["name"]["phonetic"] = {k: v for k, v in ph.items() if k in ("given", "given2", "surname", "surname2")}
                if _one(params, "PHONETIC"):
                    card["name"]["phonetic_system"] = _one(params, "PHONETIC").lower()
                if _one(params, "SCRIPT"):
                    card["name"]["phonetic_script"] = _one(params, "SCRIPT")
            else:
                card["name"].update(_name_from_n(comps))
        elif name in ("X-PHONETIC-FIRST-NAME", "X-PHONETIC-LAST-NAME", "X-PHONETIC-MIDDLE-NAME"):
            key = {"X-PHONETIC-FIRST-NAME": "given", "X-PHONETIC-LAST-NAME": "surname", "X-PHONETIC-MIDDLE-NAME": "given2"}[name]
            if text_value:
                card["name"].setdefault("phonetic", {})[key] = text_value
        elif name == "X-ZOE-SAY-AS":
            if text_value:
                card["name"]["say_as"] = text_value
        elif name == "NICKNAME":
            for nick in _list(raw):
                card["nicknames"].append({"value": nick, "types": types, "pref": pref})
        elif name == "EMAIL":
            if text_value:
                card["emails"].append({"value": text_value.removeprefix("mailto:"), "types": types, "pref": pref,
                                       "label": _one(params, "X-ABLABEL")})
        elif name == "TEL":
            v = text_value[4:] if text_value.lower().startswith("tel:") else text_value
            if v:
                card["phones"].append({"value": v, "types": types, "pref": pref, "label": _one(params, "X-ABLABEL")})
        elif name == "ADR":
            comps = _components(raw)
            parts = {k: _first(comps, i) for i, k in enumerate(("po_box", "extended", "street", "locality", "region", "postcode",
                                                                 "country"))}
            parts = {k: v for k, v in parts.items() if v}
            if parts:
                card["addresses"].append({"parts": parts, "types": types, "pref": pref, "label": _one(params, "LABEL")})
        elif name == "ORG":
            comps = [c[0].strip() if c else "" for c in _components(raw)]
            if comps and comps[0]:
                card["orgs"].append({"value": comps[0], "parts": {"units": [c for c in comps[1:] if c]}, "types": types,
                                     "pref": pref})
        elif name in ("TITLE", "ROLE"):
            if text_value:
                card["titles"].append({"value": text_value, "parts": {"role": name == "ROLE"}, "types": types, "pref": pref})
        elif name == "URL":
            if text_value:
                card["urls"].append({"value": text_value, "types": types, "pref": pref})
        elif name in ("SOCIALPROFILE", "X-SOCIALPROFILE"):
            if text_value:
                service = _one(params, "SERVICE-TYPE") or (types[0] if types else "")
                card["socials"].append({"value": text_value, "parts": {"service": service.lower(),
                                                                       "username": _one(params, "USERNAME") or ""},
                                        "types": [], "pref": pref})
        elif name == "IMPP":
            if text_value:
                card["ims"].append({"value": text_value, "types": types, "pref": pref})
        elif name == "PHOTO":
            enc = (_one(params, "ENCODING") or "").lower()
            if enc in ("b", "base64") or text_value.lower().startswith("data:"):
                card["photos_inline_dropped"] += 1        # never an inline blob in a row
            elif text_value:
                card["photos"].append({"value": text_value, "types": types, "pref": pref})
        elif name == "NOTE":
            if text_value:
                card["notes"].append({"value": text_value})
        elif name == "CATEGORIES":
            for g in _list(raw):
                if g not in card["groups"]:
                    card["groups"].append(g)
        elif name == "PRONOUNS":
            if text_value:
                card["pronouns"].append({"value": text_value, "pref": pref})
        elif name in ("BDAY", "ANNIVERSARY"):
            d = parse_date(text_value)
            if d:
                card["dates"].append({"kind": "birthday" if name == "BDAY" else "anniversary", "year": d[0], "month": d[1],
                                      "day": d[2]})
            else:
                card["unmapped"] += 1
        elif name == "RELATED":
            is_uri = (_one(params, "VALUE") or "").lower() != "text" and re.match(r"^[a-z][a-z0-9+.-]*:", text_value or "", re.I)
            rel_types = [t for t in types if t in RELATED_TYPES]
            if text_value:
                card["related"].append({"name": "" if is_uri else text_value, "uri": text_value if is_uri else None,
                                        "types": rel_types, "zoe_rel": _one(params, "X-ZOE-REL"),
                                        "zoe_role": _one(params, "X-ZOE-ROLE")})
        else:
            card["unmapped"] += 1
    return cards


# -- writing ------------------------------------------------------------------------------------------------------------------

def escape(value: Any) -> str:
    return (str(value or "").replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;"))


def _param(name: str, value: str) -> str:
    v = str(value).replace('"', "'").replace("\n", " ")
    return f';{name}="{v}"' if any(c in v for c in ",;:") else f";{name}={v}"


def _params(item: dict, *, extra: Iterable[tuple[str, str]] = ()) -> str:
    out = ""
    types = [t for t in (item.get("types") or []) if t]
    if types:
        out += ";TYPE=" + ",".join(types)
    if item.get("pref"):
        out += f";PREF={int(item['pref'])}"
    for k, v in extra:
        if v:
            out += _param(k, v)
    return out


def fold(line: str) -> str:
    """RFC 6350 section 3.2: lines longer than 75 octets are split, continuation lines start with one space. Never inside a
    UTF-8 sequence."""
    out, cur, size = [], [], 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        limit = 75 if not out else 74          # a continuation line carries the leading space
        if size + n > limit:
            out.append("".join(cur))
            cur, size = [], 0
        cur.append(ch)
        size += n
    out.append("".join(cur))
    return "\r\n ".join(out)


def _date(d: dict) -> str:
    if d.get("year"):
        return f"{int(d['year']):04d}{int(d['month']):02d}{int(d['day']):02d}"
    return f"--{int(d['month']):02d}{int(d['day']):02d}"


def to_vcard(card: dict) -> str:
    """One vCard 4.0 (CRLF lines, folded). Only the mapped fields; a photo is written only when it is a remote URI."""
    n = card.get("name") or {}
    lines = ["BEGIN:VCARD", "VERSION:4.0", f"PRODID:{PRODID}"]
    if card.get("uid"):
        lines.append(f"UID:{card['uid']}")
    full = card.get("full_name") or " ".join(x for x in (n.get("given"), n.get("given2"), n.get("surname")) if x)
    lines.append("FN:" + escape(full))
    family = [x for x in (n.get("surname"), n.get("surname2")) if x]
    suffixes = [x for x in (n.get("credential"), n.get("generation")) if x]
    comps = [",".join(escape(f) for f in family), escape(n.get("given")), escape(n.get("given2")), escape(n.get("title")),
             ",".join(escape(s) for s in suffixes), escape(n.get("surname2")), escape(n.get("generation"))]
    phonetic = n.get("phonetic") or {}
    rfc9554_phonetic = phonetic and (n.get("phonetic_system") or n.get("phonetic_script"))
    lines.append(("N;ALTID=1:" if rfc9554_phonetic else "N:") + ";".join(comps))
    if rfc9554_phonetic:
        pcomps = [escape(phonetic.get("surname")), escape(phonetic.get("given")), escape(phonetic.get("given2")), "", "",
                  escape(phonetic.get("surname2")), ""]
        head = "N;ALTID=1" + (f";PHONETIC={n['phonetic_system']}" if n.get("phonetic_system") else "")
        head += f";SCRIPT={n['phonetic_script']}" if n.get("phonetic_script") else ""
        lines.append(head + ":" + ";".join(pcomps))
    if phonetic.get("given"):
        lines.append("X-PHONETIC-FIRST-NAME:" + escape(phonetic["given"]))
    if phonetic.get("surname"):
        lines.append("X-PHONETIC-LAST-NAME:" + escape(phonetic["surname"]))
    if n.get("say_as"):
        lines.append("X-ZOE-SAY-AS:" + escape(n["say_as"]))
    for it in card.get("nicknames") or []:
        lines.append("NICKNAME" + _params(it) + ":" + escape(it["value"]))
    for it in card.get("emails") or []:
        lines.append("EMAIL" + _params(it) + ":" + escape(it["value"]))
    for it in card.get("phones") or []:
        lines.append("TEL" + _params(it) + ":" + it["value"].replace("\n", " "))
    for it in card.get("addresses") or []:
        p = it.get("parts") or {}
        vals = [escape(p.get(k)) for k in ("po_box", "extended", "street", "locality", "region", "postcode", "country")]
        lines.append("ADR" + _params(it, extra=[("LABEL", it.get("label") or "")]) + ":" + ";".join(vals))
    for it in card.get("orgs") or []:
        units = (it.get("parts") or {}).get("units") or []
        lines.append("ORG" + _params(it) + ":" + ";".join([escape(it["value"])] + [escape(u) for u in units]))
    for it in card.get("titles") or []:
        prop = "ROLE" if (it.get("parts") or {}).get("role") else "TITLE"
        lines.append(prop + _params(it) + ":" + escape(it["value"]))
    for it in card.get("urls") or []:
        lines.append("URL" + _params(it) + ":" + it["value"])
    for it in card.get("socials") or []:
        p = it.get("parts") or {}
        lines.append("SOCIALPROFILE" + _params(it, extra=[("SERVICE-TYPE", p.get("service") or ""),
                                                          ("USERNAME", p.get("username") or "")]) + ":" + it["value"])
    for it in card.get("ims") or []:
        lines.append("IMPP" + _params(it) + ":" + it["value"])
    for it in card.get("photos") or []:
        if re.match(r"^https?://", it.get("value") or ""):        # a local media reference never leaves the box
            lines.append("PHOTO" + _params(it) + ":" + it["value"])
    for d in card.get("dates") or []:
        lines.append(("BDAY:" if d["kind"] == "birthday" else "ANNIVERSARY:") + _date(d))
    for it in card.get("notes") or []:
        lines.append("NOTE:" + escape(it["value"]))
    if card.get("groups"):
        lines.append("CATEGORIES:" + ",".join(escape(g) for g in card["groups"]))
    for it in card.get("pronouns") or []:
        lines.append("PRONOUNS" + _params(it) + ":" + escape(it["value"]))
    for r in card.get("related") or []:
        extra = [("X-ZOE-REL", r.get("zoe_rel") or ""), ("X-ZOE-ROLE", r.get("zoe_role") or "")]
        if r.get("uri"):
            lines.append("RELATED" + _params({"types": r.get("types")}, extra=extra) + ":" + r["uri"])
        elif r.get("name"):
            lines.append("RELATED" + _params({"types": r.get("types")}, extra=[("VALUE", "text")] + extra) + ":" + escape(r["name"]))
    lines.append("END:VCARD")
    return "".join(fold(line) + "\r\n" for line in lines)
