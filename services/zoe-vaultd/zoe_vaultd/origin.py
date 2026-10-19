"""The origin rule (credential-vault.md section 6.3). Pure functions; no I/O.

Every input to these checks comes from the vault (the item's URIs) or from the browser (a frame's committed URL,
`securityOrigin`, `domainAndRegistry`, and what the frame itself reports from the broker's isolated world). Nothing a
caller (zoe-data, the model) says is ever an input. Every refusal carries a reason code; there is no override.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

from .psl import PublicSuffixList

DEFAULT_PORTS = {"https": 443, "http": 80}

# Bitwarden UriMatchStrategy values (libs/common/src/models/domain/domain-service.ts)
MATCH_DOMAIN = 0       # "base domain"
MATCH_HOST = 1
MATCH_STARTS_WITH = 2
MATCH_EXACT = 3
MATCH_REGEX = 4
MATCH_NEVER = 5

KIND_HOST = "host"
KIND_BASE = "base"


@dataclass(frozen=True)
class Origin:
    scheme: str
    host: str
    port: int

    def __str__(self) -> str:
        if DEFAULT_PORTS.get(self.scheme) == self.port:
            return f"{self.scheme}://{self.host}"
        return f"{self.scheme}://{self.host}:{self.port}"


@dataclass(frozen=True)
class UriRule:
    origin: Origin
    kind: str  # KIND_HOST or KIND_BASE


def origin_equal(a: "Origin | None", b: "Origin | None") -> bool:
    """THE origin comparison: scheme, ASCII host and port all equal. Every exact-origin decision goes through here."""
    return a is not None and b is not None and a == b


def is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def normalise_host(host: str | None) -> str | None:
    """Lower-case + IDNA (punycode) per label; None for anything that is not a plain DNS name or IP literal."""
    if not host:
        return None
    host = host.strip()
    if host.endswith("."):
        host = host[:-1]
    if not host or len(host) > 253:
        return None
    if is_ip_literal(host):
        return str(ipaddress.ip_address(host.strip("[]")))
    labels = []
    for label in host.split("."):
        if not label:
            return None
        try:
            ascii_label = label.encode("idna").decode("ascii").lower()
        except (UnicodeError, ValueError):
            return None
        if not ascii_label or len(ascii_label) > 63:
            return None
        if not all(c.isalnum() or c == "-" for c in ascii_label):
            return None
        labels.append(ascii_label)
    return ".".join(labels)


def origin_of_url(url: str | None) -> Origin | None:
    """The origin of an absolute http(s) URL; None for any other scheme, userinfo, or an unparsable host."""
    if not url or not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    scheme = (parts.scheme or "").lower()
    if scheme not in DEFAULT_PORTS:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    host = normalise_host(parts.hostname)
    if host is None:
        return None
    return Origin(scheme, host, port if port is not None else DEFAULT_PORTS[scheme])


def parse_origin(text: str | None) -> Origin | None:
    """Strict parse of a serialised origin ("https://host[:port]", no path); "null" and anything else is None."""
    if not text or not isinstance(text, str) or text == "null":
        return None
    o = origin_of_url(text)
    if o is None:
        return None
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return None
    return o


def scheme_allowed(o: Origin, *, lab_profile: bool) -> bool:
    if o.scheme == "https":
        return lab_profile or not is_ip_literal(o.host)
    # http only for localhost, and only in the lab profile
    return lab_profile and o.scheme == "http" and o.host == "localhost"


def item_rules(uris: list[dict] | None, *, lab_profile: bool) -> tuple[list[UriRule], list[str]]:
    """Turn an item's URI list into origin rules. Returns (rules, notes); a regex URI is refused, never honoured."""
    rules: list[UriRule] = []
    notes: list[str] = []
    for entry in uris or []:
        uri = entry.get("uri") if isinstance(entry, dict) else None
        match = entry.get("match") if isinstance(entry, dict) else None
        if match == MATCH_NEVER:
            continue
        if match == MATCH_REGEX:
            notes.append("regex_uri_refused")
            continue
        o = origin_of_url(uri)
        if o is None:
            notes.append("unparsable_uri")
            continue
        if not scheme_allowed(o, lab_profile=lab_profile):
            notes.append("scheme_refused")
            continue
        if match == MATCH_DOMAIN:
            if is_ip_literal(o.host):
                notes.append("base_domain_on_ip_refused")
                continue
            rules.append(UriRule(o, KIND_BASE))
        else:
            # null (the member's default), host, exact and starts-with are all treated as EXACT HOST (6.3)
            rules.append(UriRule(o, KIND_HOST))
    return rules, notes


def rule_matches(rule: UriRule, o: Origin, *, psl: PublicSuffixList, browser_registrable: str | None,
                 lab_profile: bool) -> tuple[bool, str]:
    if not scheme_allowed(o, lab_profile=lab_profile):
        return False, "scheme_refused"
    if rule.kind == KIND_HOST:
        same = origin_equal(o, rule.origin)
        return (same, "ok" if same else "origin_mismatch")
    # base domain: same scheme, registrable domains equal under the pinned PSL (private section included), and the
    # browser's own eTLD+1 must agree with ours.
    if o.scheme != rule.origin.scheme or is_ip_literal(o.host):
        return False, "origin_mismatch"
    ours = psl.registrable_domain(o.host)
    theirs = psl.registrable_domain(rule.origin.host)
    if ours is None or theirs is None or ours != theirs:
        return False, "origin_mismatch"
    if (browser_registrable or "").lower() != ours:
        return False, "registrable_disagreement"
    return True, "ok"


def origin_allowed(o: Origin, rules: list[UriRule], *, psl: PublicSuffixList, browser_registrable: str | None,
                   lab_profile: bool, host_rules_only: bool = False) -> tuple[bool, str]:
    reason = "origin_mismatch"
    for rule in rules:
        if host_rules_only and rule.kind != KIND_HOST:
            continue
        ok, why = rule_matches(rule, o, psl=psl, browser_registrable=browser_registrable, lab_profile=lab_profile)
        if ok:
            return True, "ok"
        if why != "origin_mismatch":
            reason = why
    return False, reason


@dataclass(frozen=True)
class FrameFacts:
    """What the browser says about one frame. `world_origin` is `self.origin` read from the broker's isolated world
    inside that frame; `secure` is `isSecureContext` from the same place."""
    frame_id: str
    url: str
    security_origin: str
    domain_and_registry: str | None
    world_origin: str | None = None
    secure: bool | None = None


SPECIAL_URL_PREFIXES = ("about:", "data:", "blob:", "file:", "javascript:", "filesystem:", "chrome:", "view-source:")


def committed_origin(frame: FrameFacts, *, lab_profile: bool) -> tuple[Origin | None, str]:
    """The frame's committed origin, or a refusal reason. All three views (URL, securityOrigin, the frame's own
    self.origin) must agree; an opaque, inherited (about:blank / srcdoc) or special-scheme frame is refused."""
    url = frame.url or ""
    if url.lower().startswith(SPECIAL_URL_PREFIXES) or not url:
        return None, "opaque_origin"
    url_origin = origin_of_url(url)
    sec = parse_origin(frame.security_origin)
    if url_origin is None or sec is None:
        return None, "opaque_origin"
    if url_origin != sec:
        return None, "opaque_origin"
    if frame.world_origin is not None:
        world = parse_origin(frame.world_origin)
        if world is None or world != sec:
            return None, "opaque_origin"
    if frame.secure is False and sec.scheme == "https":
        return None, "opaque_origin"
    if not scheme_allowed(sec, lab_profile=lab_profile):
        return None, "scheme_refused"
    return sec, "ok"


def check_frame(frame: FrameFacts, ancestors: list[FrameFacts], rules: list[UriRule], *, psl: PublicSuffixList,
                lab_profile: bool) -> tuple[bool, str, Origin | None]:
    """Section 6.3 for the frame that owns the field. `ancestors` runs from the parent up to the top frame (empty
    when the field is in the top frame). Returns (allowed, reason, the frame's committed origin)."""
    if not rules:
        return False, "no_usable_uri", None
    origin, why = committed_origin(frame, lab_profile=lab_profile)
    if origin is None:
        return False, why, None
    chain: list[Origin] = []
    for anc in ancestors:
        a_origin, a_why = committed_origin(anc, lab_profile=lab_profile)
        if a_origin is None:
            return False, "ancestor_" + a_why, origin
        ok, a_reason = origin_allowed(a_origin, rules, psl=psl, browser_registrable=anc.domain_and_registry,
                                      lab_profile=lab_profile)
        if not ok:
            return False, "ancestor_" + a_reason, origin
        chain.append(a_origin)
    top = chain[-1] if chain else origin
    if origin == top:
        ok, reason = origin_allowed(origin, rules, psl=psl, browser_registrable=frame.domain_and_registry,
                                    lab_profile=lab_profile)
        return ok, reason, origin
    # A cross-origin frame: the item must LIST this frame's origin (host rule), and the top frame must match too
    # (checked above as an ancestor).
    ok, reason = origin_allowed(origin, rules, psl=psl, browser_registrable=frame.domain_and_registry,
                                lab_profile=lab_profile, host_rules_only=True)
    if not ok:
        return False, "cross_origin_frame", origin
    return True, "ok", origin


def form_target_allowed(action_url: str | None, frame_origin: Origin, rules: list[UriRule], *,
                        psl: PublicSuffixList, lab_profile: bool) -> tuple[bool, str]:
    """Section 6.4: the form (or a submit button's formaction) must post to the frame's own origin or to an origin
    the item lists (host rules only: a base-domain rule never widens where a password may be POSTed)."""
    if action_url is None or action_url == "":
        return True, "ok"
    o = origin_of_url(action_url)
    if o is None:
        return False, "form_action_mismatch"
    if origin_equal(o, frame_origin):
        return True, "ok"
    ok, _ = origin_allowed(o, rules, psl=psl, browser_registrable=None, lab_profile=lab_profile,
                           host_rules_only=True)
    return (True, "ok") if ok else (False, "form_action_mismatch")


def expected_login_origin(item_fields: dict, rules: list[UriRule], *, psl: PublicSuffixList,
                          lab_profile: bool) -> tuple[str | None, str]:
    """Where login mode navigates: the item's `zoe:login_url` when it matches one of the item's URIs (exactly, by
    origin), else the first URI. Returns (url, reason)."""
    login_url = item_fields.get("zoe:login_url")
    if login_url:
        o = origin_of_url(login_url)
        if o is None or not scheme_allowed(o, lab_profile=lab_profile):
            return None, "login_url_refused"
        def covers(r: UriRule) -> bool:
            if r.origin == o:
                return True
            if r.kind != KIND_BASE or r.origin.scheme != o.scheme or is_ip_literal(o.host):
                return False
            mine = psl.registrable_domain(o.host)
            return mine is not None and mine == psl.registrable_domain(r.origin.host)

        if not any(covers(r) for r in rules):
            return None, "login_url_not_in_item"
        return login_url, "ok"
    for r in rules:
        if r.kind == KIND_HOST:
            return str(r.origin) + "/", "ok"
    return None, "no_usable_uri"
