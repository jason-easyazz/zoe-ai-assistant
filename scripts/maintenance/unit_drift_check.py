#!/usr/bin/env python3
"""Compare the INSTALLED user units (+ drop-ins) against the repo templates.

Why this exists
---------------
``scripts/setup/systemd/*.service`` are TEMPLATES. Merging a template change
does nothing to the box: an operator has to copy it into
``~/.config/systemd/user/`` and ``daemon-reload``. Nothing checks that they
ever did. Found by the 2026-10-04 evening log review
(``docs/knowledge/log-review-units-2026-10-04.md``): the router template had
carried ``--mlock`` + ``LimitMEMLOCK=infinity`` + ``MemoryMax=1280M`` for a
day while the live router still ran without them, so the exact page-refault
failure the template exists to prevent was still live — and every repo test
(which reads the template) was green.

That is the #1409 / requirements-drift class again: a spec in one place,
enforced in no other. This script is the enforcement. It is READ-ONLY: it never
runs systemctl, never writes, never restarts anything — it parses unit files.

What it compares
----------------
Per unit, the EFFECTIVE ``[Service]`` configuration on each side, i.e. the main
file with its ``<unit>.d/*.conf`` drop-ins applied in lexical order (an empty
``ExecStart=`` / ``Environment=`` / ``EnvironmentFile=`` resets the list, exactly
as systemd does). Then:

* ``ExecStart`` — the binary and every ``--flag value`` pair;
* the scalar directives in :data:`SCALAR_KEYS` (memory protection, limits,
  restart policy, exit-status mapping) with sizes normalised (``1G`` == ``1024M``);
* ``Environment`` — by variable name; values of secret-looking names are NEVER
  printed;
* ``EnvironmentFile`` / ``ExecStartPost`` / ``ExecStartPre`` — as sets.

``%h`` is expanded to ``--home`` on both sides, so a template using ``%h`` and a
live unit with the literal path compare equal.

Findings
--------
``missing``   the template sets X and the live unit does not — a template change
              that was never applied (the actionable class; exit 1).
``differs``   both set X, to different values (exit 1).
``live_only`` the live unit sets X and the template does not — a host edit nobody
              tracked (informational; exit 1 only with ``--strict``).
``not_installed`` the unit has a template but no installed file.

Usage
-----
    python3 scripts/maintenance/unit_drift_check.py
    python3 scripts/maintenance/unit_drift_check.py --units functiongemma-router --json
    python3 scripts/maintenance/unit_drift_check.py --strict

Exit codes: 0 = no drift, 1 = drift found, 2 = bad invocation / unreadable input.
Applying a fix is an OPERATOR step (copy the template or drop-in, ``daemon-reload``,
restart under the brain-window lock) — this tool only tells you what differs.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE_DIR = REPO_ROOT / "scripts" / "setup" / "systemd"
DEFAULT_LIVE_DIR = Path.home() / ".config" / "systemd" / "user"

# The latency/stability-relevant units this box actually runs.
DEFAULT_UNITS = (
    "llama-server",
    "functiongemma-router",
    "kokoro-tts",
    "flue-zoe-brain-2x",
    "flue-zoe-telegram",
    "serena-mcp",
    "zoe-data",
)

# Single-valued [Service] directives that matter for resilience. A directive not
# listed here is not compared (Description/After/etc. are cosmetic).
SCALAR_KEYS = (
    "Type",
    "WorkingDirectory",
    "Restart",
    "RestartSec",
    "TimeoutStartSec",
    "SuccessExitStatus",
    "LimitMEMLOCK",
    "LimitNOFILE",
    "MemorySwapMax",
    "MemoryLow",
    "MemoryHigh",
    "MemoryMax",
    "CPUWeight",
    "IOWeight",
    "Nice",
)
SIZE_KEYS = {"LimitMEMLOCK", "LimitNOFILE", "MemorySwapMax", "MemoryLow", "MemoryHigh", "MemoryMax"}
# systemd list-type directives: appended per assignment, an empty assignment resets.
LIST_KEYS = ("ExecStart", "ExecStartPre", "ExecStartPost", "Environment", "EnvironmentFile")
SET_KEYS = ("EnvironmentFile", "ExecStartPre", "ExecStartPost")

_SECRET_RE = re.compile(r"TOKEN|SECRET|PASS|KEY|CREDENTIAL", re.IGNORECASE)
_SIZE_RE = re.compile(r"^(\d+)([KMGT]?)$", re.IGNORECASE)
_SIZE_MULT = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


@dataclass(frozen=True)
class Finding:
    unit: str
    kind: str  # missing | differs | live_only | not_installed
    key: str
    template: str
    live: str


# ─── parsing ──────────────────────────────────────────────────────────────────


def _logical_lines(text: str) -> list[str]:
    """Join backslash-continued lines; drop blanks and full-line comments."""
    out: list[str] = []
    buf = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.lstrip()
        if not buf and (not stripped or stripped[0] in "#;"):
            continue
        if line.endswith("\\"):
            buf += line[:-1].strip() + " "
            continue
        out.append((buf + stripped).strip() if buf else stripped)
        buf = ""
    if buf:
        out.append(buf.strip())
    return out


def parse_service_section(texts: list[str], home: str) -> dict[str, object]:
    """Effective [Service] config from a main file + ordered drop-in texts."""
    cfg: dict[str, object] = {k: [] for k in LIST_KEYS}
    for text in texts:
        section = ""
        for line in _logical_lines(text):
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                continue
            if section != "Service" or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().replace("%h", home).replace("%%", "%")
            if key in LIST_KEYS:
                if value == "":
                    cfg[key] = []  # systemd: empty assignment resets the list
                else:
                    cfg[key].append(value)  # type: ignore[union-attr]
            elif key in SCALAR_KEYS:
                if value == "":
                    cfg.pop(key, None)
                else:
                    cfg[key] = value
    return cfg


def unit_texts(base: Path, name: str) -> list[str] | None:
    """Main-file text followed by drop-in texts in lexical order; None if no main file."""
    main = base / f"{name}.service"
    if not main.is_file():
        return None
    texts = [main.read_text(encoding="utf-8")]
    dropins = base / f"{name}.service.d"
    if dropins.is_dir():
        texts += [p.read_text(encoding="utf-8") for p in sorted(dropins.glob("*.conf"))]
    return texts


def norm_size(value: str) -> str:
    v = value.strip()
    if v.lower() == "infinity":
        return "infinity"
    m = _SIZE_RE.match(v)
    if not m:
        return v
    return str(int(m.group(1)) * _SIZE_MULT[m.group(2).upper()])


def exec_start_model(lines: list[str]) -> dict[str, list[str]]:
    """{'binary': [...], '--flag': ['value', ...]} from the last ExecStart= line."""
    model: dict[str, list[str]] = {}
    if not lines:
        return model
    try:
        toks = shlex.split(lines[-1].lstrip("-@+!:"))
    except ValueError:
        toks = lines[-1].split()
    if not toks:
        return model
    model["binary"] = [toks[0]]
    i = 1
    while i < len(toks):
        t = toks[i]
        if t.startswith("--") or (t.startswith("-") and len(t) == 2 and not t[1].isdigit()):
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if nxt is not None and not (nxt.startswith("--") or (nxt.startswith("-") and len(nxt) == 2 and not nxt[1].isdigit())):
                model.setdefault(t, []).append(nxt)
                i += 2
                continue
            model.setdefault(t, []).append("")
        else:
            model.setdefault("positional", []).append(t)
        i += 1
    return model


def env_model(lines: list[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in lines:
        try:
            toks = shlex.split(line)
        except ValueError:
            toks = line.split()
        for tok in toks:
            if "=" in tok:
                k, _, v = tok.partition("=")
                env[k] = v
    return env


def _show(key: str, value: str) -> str:
    return "<redacted>" if _SECRET_RE.search(key) else value


# ─── comparison ───────────────────────────────────────────────────────────────


def compare(unit: str, tmpl: dict[str, object], live: dict[str, object]) -> list[Finding]:
    out: list[Finding] = []

    def add(kind: str, key: str, t: str, lv: str) -> None:
        out.append(Finding(unit, kind, key, t, lv))

    # ExecStart: binary + flags.
    tm, lm = exec_start_model(tmpl["ExecStart"]), exec_start_model(live["ExecStart"])  # type: ignore[arg-type]
    for flag in sorted(set(tm) | set(lm)):
        tv, lv = sorted(tm.get(flag, [])), sorted(lm.get(flag, []))
        label = f"ExecStart {flag}"
        if tv == lv:
            continue
        if tv and not lv:
            add("missing", label, " ".join(tv), "(absent)")
        elif lv and not tv:
            add("live_only", label, "(absent)", " ".join(lv))
        else:
            add("differs", label, " ".join(tv), " ".join(lv))

    # Scalars.
    for key in SCALAR_KEYS:
        tv, lv = tmpl.get(key), live.get(key)
        if key in SIZE_KEYS:
            tv = norm_size(tv) if isinstance(tv, str) else tv
            lv = norm_size(lv) if isinstance(lv, str) else lv
        if tv == lv:
            continue
        if tv is not None and lv is None:
            add("missing", key, str(tv), "(absent)")
        elif lv is not None and tv is None:
            add("live_only", key, "(absent)", str(lv))
        else:
            add("differs", key, str(tv), str(lv))

    # Environment, by name.
    te, le = env_model(tmpl["Environment"]), env_model(live["Environment"])  # type: ignore[arg-type]
    for name in sorted(set(te) | set(le)):
        if te.get(name) == le.get(name):
            continue
        label = f"Environment {name}"
        if name in te and name not in le:
            add("missing", label, _show(name, te[name]), "(absent)")
        elif name in le and name not in te:
            add("live_only", label, "(absent)", _show(name, le[name]))
        else:
            add("differs", label, _show(name, te[name]), _show(name, le[name]))

    # Set-valued directives.
    for key in SET_KEYS:
        ts, ls = set(tmpl[key]), set(live[key])  # type: ignore[arg-type]
        for item in sorted(ts - ls):
            add("missing", key, item, "(absent)")
        for item in sorted(ls - ts):
            add("live_only", key, "(absent)", item)
    return out


def check(units, template_dir: Path, live_dir: Path, home: str) -> tuple[list[Finding], list[str]]:
    findings: list[Finding] = []
    problems: list[str] = []
    for name in units:
        t_texts = unit_texts(template_dir, name)
        if t_texts is None:
            problems.append(f"{name}: no template {template_dir / (name + '.service')}")
            continue
        l_texts = unit_texts(live_dir, name)
        if l_texts is None:
            findings.append(Finding(name, "not_installed", "(unit file)", "present", "absent"))
            continue
        tmpl = parse_service_section(t_texts, home)
        live = parse_service_section(l_texts, home)
        if not tmpl["ExecStart"]:
            problems.append(f"{name}: template parsed to an empty ExecStart (parser or template broken)")
            continue
        findings.extend(compare(name, tmpl, live))
    return findings, problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--template-dir", type=Path, default=DEFAULT_TEMPLATE_DIR)
    ap.add_argument("--live-dir", type=Path, default=DEFAULT_LIVE_DIR)
    ap.add_argument("--home", default=str(Path.home()), help="value substituted for %%h on both sides")
    ap.add_argument("--units", default=",".join(DEFAULT_UNITS), help="comma-separated unit names (no .service)")
    ap.add_argument("--strict", action="store_true", help="live-only host edits also fail the run")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    units = [u.strip().removesuffix(".service") for u in args.units.split(",") if u.strip()]
    if not units:
        print("no units given", file=sys.stderr)
        return 2
    findings, problems = check(units, args.template_dir, args.live_dir, args.home)

    actionable = [f for f in findings if f.kind != "live_only"]
    failing = findings if args.strict else actionable
    if args.json:
        print(json.dumps({"findings": [asdict(f) for f in findings], "problems": problems}, indent=2))
    else:
        for p in problems:
            print(f"ERROR  {p}", file=sys.stderr)
        for f in findings:
            print(f"{f.kind.upper():<13} {f.unit}: {f.key}  template={f.template!r}  live={f.live!r}")
        if not findings and not problems:
            print(f"no drift across {len(units)} unit(s)")
    if problems:
        return 2
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
