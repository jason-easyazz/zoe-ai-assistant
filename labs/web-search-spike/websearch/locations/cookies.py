"""A per-retailer cookie jar, so the picker dance runs ONCE per session.

WHY A JAR AT ALL
----------------
An `interaction` recipe costs a CloakBrowser launch (~553 MB, 30-60 s). Paying
that per PRODUCT is absurd: a 40-product weekly run would be 40 launches at one
retailer. Paying it per RETAILER PER SESSION is the point — select the store
once, keep the cookies that encode the selection, and read every product with
plain httpx afterwards.

WHY IT IS DOMAIN-SCOPED TWICE
-----------------------------
Session cookies are credentials-shaped. Sending Liquorland's cookies to BWS
would be, at best, a leak of one retailer's session into another's request and,
at worst, a way to make a cross-site request look like a logged-in one. The jar
therefore filters by domain **on write and again on read**:

- on write, because that is where a browser context — which holds cookies for
  every domain the page touched, including CDNs and ad networks — gets narrowed
  down to the retailer;
- on read, because the file on disk is not a trusted input. It can be
  hand-edited, copied between machines, or written by an older version of this
  code with a looser filter. A read-side filter means a bad file degrades to
  "fewer cookies" rather than "cookies for the wrong site".

`test_cookie_jar_never_leaks_across_domains` is the negative control, and it
asserts BOTH directions — a poisoned file must not leak on read either.

SUBDOMAINS ARE IN SCOPE, SIBLING DOMAINS ARE NOT
------------------------------------------------
`api.bws.com.au` must receive `bws.com.au` cookies: that is the same site and
the store selection genuinely lives on the parent domain. `bwsfoo.com.au` must
not, and neither must `evilbws.com.au`. Matching is therefore on a LABEL
BOUNDARY (`host == base or host.endswith("." + base)`), never a substring —
a substring test is how `evilbws.com.au` gets your cookies.
"""

from __future__ import annotations

import json
import os
import pathlib
import time
from typing import Any, Iterable

#: Where jars live. Outside the repo: these are live session cookies for
#: third-party sites and they are neither committable nor reviewable.
DEFAULT_COOKIE_DIR = pathlib.Path.home() / ".zoe-store-sessions"

#: A cached selection older than this is refused. A store picker's cookie can
#: outlive the selection it encoded (site-side session expiry, a promo cycle),
#: and a silently-stale store attribution is exactly the lie this package
#: exists to prevent. One week: a weekly pricing run re-does the dance anyway.
DEFAULT_MAX_AGE_S = 7 * 24 * 3600


def cookie_dir() -> pathlib.Path:
    """The jar root. `ZOE_STORE_COOKIE_DIR` overrides; used by every test."""
    override = os.environ.get("ZOE_STORE_COOKIE_DIR", "").strip()
    return pathlib.Path(override) if override else DEFAULT_COOKIE_DIR


def host_of(url_or_host: str) -> str:
    """Bare lowercase host from a URL or a host. No scheme required."""
    value = (url_or_host or "").strip().lower()
    if "//" in value:
        value = value.split("//", 1)[1]
    value = value.split("/", 1)[0].split("?", 1)[0]
    if "@" in value:
        value = value.rsplit("@", 1)[1]
    if value.startswith("["):  # IPv6 literal
        return value.split("]", 1)[0] + "]"
    return value.split(":", 1)[0]


def in_scope(cookie_domain: str, base_domain: str) -> bool:
    """Is `cookie_domain` the same site as `base_domain`, or a subdomain of it?

    A leading dot (`.bws.com.au`) is the classic domain-cookie spelling and is
    normalised away. The comparison is on a label boundary — see the module
    docstring for why a substring test is a vulnerability, not a shortcut.
    """
    c = (cookie_domain or "").strip().lower().lstrip(".")
    b = (base_domain or "").strip().lower().lstrip(".")
    if not c or not b:
        return False
    return c == b or c.endswith("." + b)


class CookieJar:
    """On-disk, one file per registrable domain, filtered on write AND on read."""

    def __init__(self, root: pathlib.Path | None = None, *, max_age_s: int = DEFAULT_MAX_AGE_S):
        self.root = pathlib.Path(root) if root is not None else cookie_dir()
        self.max_age_s = max_age_s

    # ------------------------------------------------------------------ paths

    def path_for(self, domain: str) -> pathlib.Path:
        """One file per domain. The name is SANITISED — a domain string reaches
        this from a URL, and `../../etc/foo` must not become a path."""
        safe = "".join(ch for ch in host_of(domain) if ch.isalnum() or ch in ".-_")
        if not safe or safe.strip(".") == "":
            raise ValueError(f"refusing to build a jar path from {domain!r}")
        return self.root / f"{safe}.json"

    # ------------------------------------------------------------------- I/O

    def save(
        self,
        domain: str,
        cookies: Iterable[dict[str, Any]],
        *,
        store_id: str = "",
        store_label: str = "",
        local_storage: dict[str, str] | None = None,
    ) -> pathlib.Path:
        """Persist ONLY the cookies belonging to `domain`. Returns the path."""
        base = host_of(domain)
        kept = [c for c in cookies if in_scope(str(c.get("domain", "")), base)]
        payload = {
            "domain": base,
            "saved_at": time.time(),
            "store_id": store_id,
            "store_label": store_label,
            "cookies": kept,
            "local_storage": dict(local_storage or {}),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path_for(base)
        path.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
        try:
            path.chmod(0o600)  # session cookies, not world-readable
        except OSError:
            pass
        return path

    def _read(self, domain: str) -> dict[str, Any] | None:
        path = self.path_for(domain)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def age_s(self, domain: str) -> float | None:
        data = self._read(domain)
        if not data:
            return None
        try:
            return max(0.0, time.time() - float(data.get("saved_at", 0)))
        except (TypeError, ValueError):
            return None

    def is_fresh(self, domain: str) -> bool:
        age = self.age_s(domain)
        return age is not None and age <= self.max_age_s

    def load(self, domain: str) -> list[dict[str, Any]]:
        """Cookies for `domain` ONLY. Stale jars return nothing.

        Re-filters on read: the file is not a trusted input (see the module
        docstring). A stale jar returns `[]` rather than old cookies, because
        the caller's next move on an empty jar is "re-run the picker", which is
        exactly the right response to an expired selection.
        """
        data = self._read(domain)
        if not data or not self.is_fresh(domain):
            return []
        base = host_of(domain)
        raw = data.get("cookies") or []
        return [c for c in raw if isinstance(c, dict) and in_scope(str(c.get("domain", "")), base)]

    def meta(self, domain: str) -> dict[str, Any]:
        """`store_id` / `store_label` / `saved_at` for a fresh jar, else `{}`."""
        data = self._read(domain)
        if not data or not self.is_fresh(domain):
            return {}
        return {
            "store_id": data.get("store_id", ""),
            "store_label": data.get("store_label", ""),
            "saved_at": data.get("saved_at"),
            "age_s": self.age_s(domain),
            "local_storage": data.get("local_storage") or {},
        }

    def as_header(self, domain: str, *, target_url: str | None = None) -> str:
        """A `Cookie:` header value for `target_url` (default: the domain itself).

        Filters AGAIN against the actual request host, so a jar loaded for
        `bws.com.au` sends nothing at all when handed a `liquorland.com.au`
        URL — the caller cannot misuse a correctly-loaded jar.
        """
        base = host_of(domain)
        host = host_of(target_url) if target_url else base
        if not in_scope(host, base):
            return ""
        pairs = [
            f"{c['name']}={c['value']}"
            for c in self.load(base)  # already domain-filtered on read
            if c.get("name") and c.get("value") is not None
        ]
        return "; ".join(pairs)

    def clear(self, domain: str) -> bool:
        path = self.path_for(domain)
        if path.is_file():
            path.unlink()
            return True
        return False

    def domains(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.stem for p in self.root.glob("*.json"))
