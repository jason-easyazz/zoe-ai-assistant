#!/usr/bin/env python3
"""Compare the INSTALLED user units (+ drop-ins) against the repo templates. READ-ONLY.

Merging a change to ``scripts/setup/systemd/*.service`` does nothing to the box: an
operator must copy it to ``~/.config/systemd/user/`` and ``daemon-reload``, and nothing
checks that they did. 2026-10-04 log review: the router template had carried ``--mlock``
+ ``LimitMEMLOCK`` + ``MemoryMax=1280M`` for a day while the live router ran without
them, and every test (which reads the template) stayed green
(``docs/knowledge/log-review-units-2026-10-04.md``). This tool parses unit files only —
it never calls systemctl and never writes.

Compared, per unit, on the EFFECTIVE config (main file + ``<unit>.d/*.conf`` in lexical
order; ``%h`` expanded on both sides): ``ExecStart`` binary and every ``--flag value`` /
``--flag=value``; the scalar directives in :data:`SCALAR_KEYS` (sizes and time spans
normalised: ``1G`` == ``1024M``, ``5min`` == ``300``); ``Environment`` by name; and the
APPEND-list directives (``SuccessExitStatus``, ``After``/``Wants``/..., ``EnvironmentFile``,
``ExecStartPre``/``Post``) as sets, accumulated across drop-ins exactly as systemd does
(an empty assignment resets the list).

VALUES ARE HASHED: a unit can carry a secret in any directive (a ``--api-key=`` flag, a
``Bearer`` header, a URL password, any variable name), so by default every value prints as
``sha256:<12 hex>`` (text and ``--json``; equal values show equal hashes). ``--show-values``
prints them (URL userinfo and secret-named variables/flags still redacted) -- trusted terminal only.

Findings: ``missing`` (template sets it, live does not) and ``differs`` are template changes
never applied (exit 1); ``live_only`` is an untracked host edit (exit 1 only with
``--strict``); ``not_installed`` has a template but no installed file.

    python3 scripts/maintenance/unit_drift_check.py [--units a,b] [--json] [--strict] [--show-values]

Exit: 0 no drift, 1 drift, 2 bad invocation / unreadable or undecodable unit file. Applying
a fix is an OPERATOR step (docs/knowledge/incident-runbook.md section 24).
"""
from __future__ import annotations

import argparse
import hashlib
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
    "llama-server", "functiongemma-router", "kokoro-tts", "flue-zoe-brain-2x",
    "flue-zoe-telegram", "serena-mcp", "zoe-data",
)

# Single-valued [Service] directives that matter for resilience (others are not compared).
SCALAR_KEYS = (
    "Type", "WorkingDirectory", "Restart", "RestartSec", "TimeoutStartSec",
    "LimitMEMLOCK", "LimitNOFILE", "MemorySwapMax", "MemoryLow", "MemoryHigh", "MemoryMax",
    "CPUWeight", "IOWeight", "Nice",
)
SIZE_KEYS = {"LimitMEMLOCK", "LimitNOFILE", "MemorySwapMax", "MemoryLow", "MemoryHigh", "MemoryMax"}
TIME_KEYS = {"RestartSec", "TimeoutStartSec"}
# systemd APPEND-list directives: every assignment accumulates, an empty one resets.
SERVICE_LIST_KEYS = (
    "ExecStart", "ExecStartPre", "ExecStartPost", "Environment", "EnvironmentFile", "SuccessExitStatus",
)
UNIT_LIST_KEYS = ("After", "Before", "Wants", "Requires", "BindsTo", "PartOf", "Conflicts")
LINE_SET_KEYS = ("EnvironmentFile", "ExecStartPre", "ExecStartPost")  # compared as sets of lines
TOKEN_SET_KEYS = ("SuccessExitStatus",) + UNIT_LIST_KEYS  # compared as sets of space-split items
LIST_KEYS = SERVICE_LIST_KEYS + UNIT_LIST_KEYS

_SECRET_RE = re.compile(r"TOKEN|SECRET|PASS|KEY|CREDENTIAL", re.IGNORECASE)
_USERINFO_RE = re.compile(r"(\w+://)[^/@\s]+@")
_SIZE_RE = re.compile(r"^(\d+)([KMGT]?)$", re.IGNORECASE)
_SIZE_MULT = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
_TIME_UNITS = {"": 1}
for _names, _mult in ((("us", "usec"), 1e-6), (("ms", "msec"), 1e-3), (("s", "sec", "second", "seconds"), 1),
                      (("m", "min", "minute", "minutes"), 60), (("h", "hr", "hour", "hours"), 3600),
                      (("d", "day", "days"), 86400), (("w", "week", "weeks"), 604800)):
    _TIME_UNITS.update(dict.fromkeys(_names, _mult))
_TIME_PART = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]*)")


class UnitReadError(Exception):
    """A unit file or drop-in could not be read or decoded (exit 2, never a drift code)."""


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
    """Effective config from a main file + ordered drop-in texts ([Service] + [Unit] lists)."""
    cfg: dict[str, object] = {k: [] for k in LIST_KEYS}
    for text in texts:
        section = ""
        for line in _logical_lines(text):
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                continue
            if section not in ("Service", "Unit") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().replace("%h", home).replace("%%", "%")
            wanted_list = SERVICE_LIST_KEYS if section == "Service" else UNIT_LIST_KEYS
            if key in wanted_list:
                if value == "":
                    cfg[key] = []  # systemd: an empty assignment resets the list
                else:
                    cfg[key].append(value)  # type: ignore[union-attr]
            elif section == "Service" and key in SCALAR_KEYS:
                if value == "":
                    cfg.pop(key, None)
                else:
                    cfg[key] = value
    return cfg


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # Type only: the message could echo file content.
        raise UnitReadError(f"{path}: cannot read ({type(exc).__name__})") from None


def unit_texts(base: Path, name: str) -> list[str] | None:
    """Main-file text followed by drop-in texts in lexical order; None if no main file."""
    main = base / f"{name}.service"
    if not main.is_file():
        return None
    texts = [_read(main)]
    dropins = base / f"{name}.service.d"
    try:
        confs = sorted(dropins.glob("*.conf")) if dropins.is_dir() else []
    except OSError as exc:
        raise UnitReadError(f"{dropins}: cannot list ({type(exc).__name__})") from None
    return texts + [_read(p) for p in confs]


def norm_size(value: str) -> str:
    v = value.strip()
    if v.lower() == "infinity":
        return "infinity"
    m = _SIZE_RE.match(v)
    return str(int(m.group(1)) * _SIZE_MULT[m.group(2).upper()]) if m else v


def norm_time(value: str) -> str:
    """systemd time span -> seconds ('5min' == '300', '1m 30s' == '90'); unparseable stays as is."""
    v = value.strip().lower()
    if v == "infinity":
        return v
    parts = _TIME_PART.findall(v)
    if not parts or "".join(f"{n}{u}" for n, u in parts) != re.sub(r"\s+", "", v):
        return value.strip()
    try:
        total = sum(float(n) * _TIME_UNITS[u] for n, u in parts)
    except KeyError:
        return value.strip()
    return f"{total:g}"


def _is_flag(tok: str) -> bool:
    return tok.startswith("--") or (tok.startswith("-") and len(tok) == 2 and not tok[1].isdigit())


def exec_start_model(lines: list[str]) -> dict[str, list[str]]:
    """{'binary': [...], '--flag': ['value', ...]} from the last ExecStart= line.

    ``--flag=value`` is split so the value never lands in a key.
    """
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
        if t.startswith("--") and "=" in t:
            flag, _, val = t.partition("=")
            model.setdefault(flag, []).append(val)
        elif _is_flag(t):
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if nxt is not None and not _is_flag(nxt):
                model.setdefault(t, []).append(nxt)
                i += 1
            else:
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


# ─── comparison ───────────────────────────────────────────────────────────────


def _disp(value: str, show: bool, name: str = "") -> str:
    """Display a value: a short hash by default, the (redacted) text only with --show-values."""
    if value == "(absent)":
        return value
    if not show:
        return "sha256:" + hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:12]
    if name and _SECRET_RE.search(name):
        return "<redacted>"
    return _USERINFO_RE.sub(r"\1<redacted>@", value)


def compare(unit: str, tmpl: dict[str, object], live: dict[str, object], show: bool = False) -> list[Finding]:
    out: list[Finding] = []

    def add(kind: str, key: str, t: str, lv: str, name: str = "") -> None:
        out.append(Finding(unit, kind, key, _disp(t, show, name), _disp(lv, show, name)))

    def diff(label: str, tv: str | None, lv: str | None, name: str = "") -> None:
        if tv == lv:
            return
        if tv is not None and lv is None:
            add("missing", label, tv, "(absent)", name)
        elif lv is not None and tv is None:
            add("live_only", label, "(absent)", lv, name)
        else:
            add("differs", label, str(tv), str(lv), name)

    # ExecStart: binary + flags.
    tm, lm = exec_start_model(tmpl["ExecStart"]), exec_start_model(live["ExecStart"])  # type: ignore[arg-type]
    for flag in sorted(set(tm) | set(lm)):
        tv, lv = sorted(tm.get(flag, [])), sorted(lm.get(flag, []))
        diff(f"ExecStart {flag}", " ".join(tv) if flag in tm else None,
             " ".join(lv) if flag in lm else None, flag)

    # Scalars.
    for key in SCALAR_KEYS:
        tv, lv = tmpl.get(key), live.get(key)
        norm = norm_size if key in SIZE_KEYS else norm_time if key in TIME_KEYS else None
        if norm:
            tv = norm(tv) if isinstance(tv, str) else tv
            lv = norm(lv) if isinstance(lv, str) else lv
        diff(key, tv, lv)  # type: ignore[arg-type]

    # Environment, by name.
    te, le = env_model(tmpl["Environment"]), env_model(live["Environment"])  # type: ignore[arg-type]
    for name in sorted(set(te) | set(le)):
        diff(f"Environment {name}", te.get(name), le.get(name), name)

    # Append-list directives, as sets.
    for key in LINE_SET_KEYS + TOKEN_SET_KEYS:
        def items(cfg: dict[str, object]) -> set[str]:
            vals: list[str] = cfg[key]  # type: ignore[assignment]
            return set(vals) if key in LINE_SET_KEYS else {t for v in vals for t in v.split()}

        ts, ls = items(tmpl), items(live)
        for item in sorted(ts - ls):
            add("missing", key, item, "(absent)")
        for item in sorted(ls - ts):
            add("live_only", key, "(absent)", item)
    return out


def check(units, template_dir: Path, live_dir: Path, home: str, show: bool = False) -> tuple[list[Finding], list[str]]:
    findings: list[Finding] = []
    problems: list[str] = []
    for name in units:
        try:
            t_texts = unit_texts(template_dir, name)
            if t_texts is None:
                problems.append(f"{name}: no template {template_dir / (name + '.service')}")
                continue
            l_texts = unit_texts(live_dir, name)
        except UnitReadError as exc:
            problems.append(f"{name}: {exc}")
            continue
        if l_texts is None:
            findings.append(Finding(name, "not_installed", "(unit file)", "present", "absent"))
            continue
        tmpl = parse_service_section(t_texts, home)
        live = parse_service_section(l_texts, home)
        if not tmpl["ExecStart"]:
            problems.append(f"{name}: template parsed to an empty ExecStart (parser or template broken)")
            continue
        findings.extend(compare(name, tmpl, live, show))
    return findings, problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--template-dir", type=Path, default=DEFAULT_TEMPLATE_DIR)
    ap.add_argument("--live-dir", type=Path, default=DEFAULT_LIVE_DIR)
    ap.add_argument("--home", default=str(Path.home()), help="value substituted for %%h on both sides")
    ap.add_argument("--units", default=",".join(DEFAULT_UNITS), help="comma-separated unit names (no .service)")
    ap.add_argument("--strict", action="store_true", help="live-only host edits also fail the run")
    ap.add_argument("--show-values", action="store_true", help="print values instead of sha256 prefixes")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    units = [u.strip().removesuffix(".service") for u in args.units.split(",") if u.strip()]
    if not units:
        print("no units given", file=sys.stderr)
        return 2
    findings, problems = check(units, args.template_dir, args.live_dir, args.home, args.show_values)

    failing = findings if args.strict else [f for f in findings if f.kind != "live_only"]
    if args.json:
        print(json.dumps({"findings": [asdict(f) for f in findings], "problems": problems}, indent=2))
    else:
        for p in problems:
            print(f"ERROR  {p}", file=sys.stderr)
        for f in findings:
            print(f"{f.kind.upper():<13} {f.unit}: {f.key}  template={f.template}  live={f.live}")
        if not findings and not problems:
            print(f"no drift across {len(units)} unit(s)")
    if problems:
        return 2
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
