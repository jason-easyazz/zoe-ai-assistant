#!/usr/bin/env python3
"""Compare a requirements file against the packages ACTUALLY installed.

Why this exists
---------------
``services/zoe-data/requirements.txt`` is a DECLARED spec, not an installed
manifest: nothing installs it automatically. zoe-data runs host-native
(``/usr/bin/python3 -m uvicorn main:app``, see
``scripts/setup/systemd/zoe-data.service``) against packages installed
out-of-band, so the file and the box drift silently and neither one announces
it. Measured 2026-08-06 on the live Orin: three exact pins disagreed with the
installed versions and three listed packages were not installed at all.

That is the #1409 class one layer up — a spec that exists in one place and is
enforced in no other. This script is the enforcement: it makes the disagreement
*visible* rather than inferred.

Where it can tell the truth
---------------------------
Only where the packages actually live. On a GitHub-hosted runner the answer is
meaningless (none of the Jetson stack is installed there), so the real check
runs on the self-hosted Jetson lane; ``tests/unit/test_requirements_drift_check.py``
covers the comparator's LOGIC offline, with a negative control.

Usage
-----
    /usr/bin/python3 scripts/maintenance/requirements_drift_check.py \\
        services/zoe-data/requirements.txt

    ... --json          machine-readable report
    ... --quiet         only print drift, not agreement

Exit codes: 0 = no drift, 1 = drift found, 2 = bad invocation.

Run it with the SAME interpreter that runs the service — the report is only
about the environment it is executed in.

**Only meaningful for a HOST-NATIVE service.** zoe-data qualifies; `zoe-auth`
does NOT — it is a container (`docker-compose.yml`: `build: ./services/zoe-auth`),
so its `requirements.txt` really is installed, by the image build, into an
environment this process cannot see. Pointing this script at a containerised
service's file from the host compares two unrelated environments and reports
loud, meaningless "drift" (measured: 7 spurious findings for zoe-auth). For a
container, run it INSIDE the container or not at all.

Marker convention
-----------------
A requirement line may carry a trailing ``# drift-optional: <reason>`` comment.
That declares the package genuinely optional (the importing code degrades
instead of failing), so "not installed" is recorded as INFO rather than drift.
It never excuses a VERSION mismatch — an installed optional still has to match.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from importlib.metadata import PackageNotFoundError, version as installed_version
from typing import Iterable, NamedTuple, Optional

# name[extras] followed by zero or more comma-joined specifiers.
_REQ_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?P<extras>\[[^\]]*\])?\s*(?P<specs>.*?)\s*$"
)
_SPEC_RE = re.compile(r"(==|!=|>=|<=|~=|>|<)\s*([^,\s]+)")
_OPTIONAL_RE = re.compile(r"#\s*drift-optional\b")

# Verdicts. Only these two mean "the file is lying about this package".
DRIFT_VERDICTS = frozenset({"MISMATCH", "MISSING"})


class Finding(NamedTuple):
    name: str
    spec: str            # "==0.32.0", ">=2.0,<3.0", "" when unpinned
    installed: Optional[str]
    verdict: str         # match | MISMATCH | MISSING | missing-optional | unpinned | uncheckable
    detail: str

    @property
    def is_drift(self) -> bool:
        return self.verdict in DRIFT_VERDICTS


def parse_version(raw: str) -> Optional[tuple]:
    """Leading dotted-numeric release of a version, or None if unusable.

    ``"1.26.4" -> (1, 26, 4)``; ``"3.10.0rc1" -> (3, 10, 0)``; ``"weird" -> None``.
    Deliberately not full PEP 440 — this module is stdlib-only so it can be
    imported on the slim CI lane, where ``packaging`` is not guaranteed. Anything
    it cannot read is reported as ``uncheckable`` rather than silently passed.
    """
    match = re.match(r"^\s*v?(\d+(?:\.\d+)*)", raw or "")
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _pad(left: tuple, right: tuple) -> tuple:
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)), right + (0,) * (width - len(right))


def satisfies(have: str, operator: str, want: str) -> Optional[bool]:
    """Does ``have`` satisfy ``operator want``? None when not decidable."""
    a, b = parse_version(have), parse_version(want)
    if operator == "==":
        # Exact pins compare as STRINGS: 0.32.0 and 0.32 are different pins even
        # though they are the same release, and we want that surfaced.
        return have.strip() == want.strip()
    if operator == "!=":
        return have.strip() != want.strip()
    if a is None or b is None:
        return None
    a, b = _pad(a, b)
    if operator == ">=":
        return a >= b
    if operator == "<=":
        return a <= b
    if operator == ">":
        return a > b
    if operator == "<":
        return a < b
    if operator == "~=":
        # ~=X.Y  <=>  >=X.Y, ==X.*
        return a >= b and a[: len(b) - 1] == b[: len(b) - 1]
    return None


def iter_requirements(text: str) -> Iterable[tuple[str, str, bool]]:
    """Yield ``(name, spec, optional)`` for each requirement line.

    Comments, blank lines, ``-r``/``-e``/option lines and environment markers are
    skipped or stripped. ``optional`` reflects the ``# drift-optional`` marker.
    """
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        optional = bool(_OPTIONAL_RE.search(raw_line))
        # Strip trailing comment and any PEP 508 environment marker.
        line = line.split("#", 1)[0].split(";", 1)[0].strip()
        if not line:
            continue
        match = _REQ_RE.match(line)
        if not match:
            continue
        yield match.group("name"), (match.group("specs") or "").strip(), optional


def check(text: str) -> list[Finding]:
    """Compare every requirement in ``text`` against the running interpreter."""
    findings: list[Finding] = []
    for name, spec, optional in iter_requirements(text):
        try:
            have = installed_version(name)
        except PackageNotFoundError:
            have = None

        if have is None:
            if optional:
                findings.append(
                    Finding(name, spec, None, "missing-optional",
                            "declared optional; importing code degrades without it")
                )
            else:
                findings.append(
                    Finding(name, spec, None, "MISSING",
                            "listed as a requirement but NOT INSTALLED in this environment")
                )
            continue

        constraints = _SPEC_RE.findall(spec)
        if not constraints:
            findings.append(Finding(name, "", have, "unpinned",
                                    "no version constraint — floats to whatever is installed"))
            continue

        undecidable = []
        for operator, want in constraints:
            ok = satisfies(have, operator, want)
            if ok is None:
                undecidable.append(f"{operator}{want}")
            elif not ok:
                findings.append(
                    Finding(name, spec, have, "MISMATCH",
                            f"installed {have} does not satisfy {operator}{want}")
                )
                break
        else:
            if undecidable:
                findings.append(Finding(name, spec, have, "uncheckable",
                                        f"cannot compare {have} against {', '.join(undecidable)}"))
            else:
                findings.append(Finding(name, spec, have, "match", ""))
    return findings


def render(findings: list[Finding], quiet: bool = False) -> str:
    rows = [f for f in findings if not quiet or f.verdict not in ("match", "unpinned")]
    if not rows:
        return "no drift\n"
    width = max(len(f.name) for f in rows) + 2
    out = [f"{'package'.ljust(width)}{'declared'.ljust(16)}{'installed'.ljust(16)}verdict"]
    out.append("-" * (width + 48))
    for f in rows:
        out.append(
            f"{f.name.ljust(width)}{(f.spec or '(unpinned)').ljust(16)}"
            f"{(f.installed or '-').ljust(16)}{f.verdict}"
        )
    drift = [f for f in findings if f.is_drift]
    out.append("")
    out.append(f"{len(findings)} requirements checked, {len(drift)} drifted")
    for f in drift:
        out.append(f"  ! {f.name}: {f.detail}")
    return "\n".join(out) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("requirements", help="path to the requirements file")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--quiet", action="store_true", help="print only non-matching rows")
    args = parser.parse_args(argv)

    try:
        with open(args.requirements, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        print(f"cannot read {args.requirements}: {exc}", file=sys.stderr)
        return 2

    findings = check(text)
    drift = [f for f in findings if f.is_drift]

    if args.json:
        print(json.dumps({
            "requirements": args.requirements,
            "interpreter": sys.executable,
            "checked": len(findings),
            "drifted": len(drift),
            "findings": [f._asdict() for f in findings],
        }, indent=2))
    else:
        print(f"interpreter: {sys.executable}")
        print(render(findings, quiet=args.quiet))

    return 1 if drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
