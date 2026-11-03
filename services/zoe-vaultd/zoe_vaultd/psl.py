"""Public Suffix List lookup for the base-domain rule (credential-vault.md section 6.3).

The broker computes the registrable domain (eTLD+1) itself from a pinned PSL file, INCLUDING the private section, the
way the Bitwarden clients do (tldts with allowPrivateDomains). A private suffix such as a shared hosting platform is
then its own public suffix, so two tenants under it never share a registrable domain. The file is loaded once from a
path in the config; it is never fetched at run time.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

PRIVATE_MARKER = "===BEGIN PRIVATE DOMAINS==="


def _ascii_label_rule(rule: str) -> str | None:
    """Lower-case and punycode one rule; None when it cannot be encoded (the rule is then dropped and counted)."""
    out = []
    for label in rule.split("."):
        if label in ("*",) or label.startswith("!"):
            prefix = "!" if label.startswith("!") else ""
            core = label[1:] if prefix else label
            if core == "*":
                out.append(prefix + core)
                continue
            label_ascii = _to_ascii(core)
            if label_ascii is None:
                return None
            out.append(prefix + label_ascii)
            continue
        label_ascii = _to_ascii(label)
        if label_ascii is None:
            return None
        out.append(label_ascii)
    return ".".join(out)


def _to_ascii(label: str) -> str | None:
    if not label:
        return None
    try:
        return label.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return None


class PublicSuffixList:
    def __init__(self, rules: set[str], wildcards: set[str], exceptions: set[str], *, sha256: str = "",
                 dropped: int = 0, private_rules: int = 0) -> None:
        self.rules = rules            # "co.uk", "github.io"
        self.wildcards = wildcards    # "ck" for the rule "*.ck"
        self.exceptions = exceptions  # "www.ck" for the rule "!www.ck"
        self.sha256 = sha256
        self.dropped = dropped
        self.private_rules = private_rules

    @classmethod
    def load(cls, path: str | Path, *, include_private: bool = True) -> "PublicSuffixList":
        raw = Path(path).read_bytes()
        return cls.parse(raw.decode("utf-8"), include_private=include_private,
                         sha256=hashlib.sha256(raw).hexdigest())

    @classmethod
    def parse(cls, text: str, *, include_private: bool = True, sha256: str = "") -> "PublicSuffixList":
        rules: set[str] = set()
        wildcards: set[str] = set()
        exceptions: set[str] = set()
        in_private = False
        dropped = 0
        private_rules = 0
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("//"):
                if PRIVATE_MARKER in line:
                    in_private = True
                continue
            if in_private and not include_private:
                continue
            rule = line.split()[0]
            ascii_rule = _ascii_label_rule(rule)
            if ascii_rule is None:
                dropped += 1
                continue
            if in_private:
                private_rules += 1
            if ascii_rule.startswith("!"):
                exceptions.add(ascii_rule[1:])
            elif ascii_rule.startswith("*."):
                wildcards.add(ascii_rule[2:])
            else:
                rules.add(ascii_rule)
        return cls(rules, wildcards, exceptions, sha256=sha256, dropped=dropped, private_rules=private_rules)

    def public_suffix_labels(self, labels: list[str]) -> int:
        """How many trailing labels form the public suffix (PSL algorithm; default rule "*" = 1)."""
        n = len(labels)
        best = 1
        exception_len = 0
        for i in range(n):
            cand = ".".join(labels[i:])
            length = n - i
            if cand in self.exceptions:
                exception_len = max(exception_len, length - 1)
            if cand in self.rules:
                best = max(best, length)
            if i + 1 < n and ".".join(labels[i + 1:]) in self.wildcards:
                best = max(best, length)
        if exception_len:
            return exception_len
        return best

    def registrable_domain(self, host: str) -> str | None:
        """eTLD+1 of an already-normalised ASCII host; None for an IP literal or a host that is itself a suffix."""
        if not host or _is_ip_literal(host):
            return None
        labels = host.rstrip(".").split(".")
        if any(not label for label in labels):
            return None
        suffix_len = self.public_suffix_labels(labels)
        if len(labels) <= suffix_len:
            return None
        return ".".join(labels[-(suffix_len + 1):])


def _is_ip_literal(host: str) -> bool:
    import ipaddress

    h = host.strip("[]")
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False
