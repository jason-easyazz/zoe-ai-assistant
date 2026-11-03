#!/usr/bin/env python3
"""Panel config drift — the live panel's ``.env.voice`` against the tracked template.

Deep dive 2 (2026-10-19) V-3: 36 of the 46 keys in the panel's ``~/.zoe-voice/.env.voice`` existed
ONLY on the Pi, so a dead SD card lost the tuning and a second panel would be set up from a 14-key
example with different defaults. ``scripts/setup/zoe-voice.env.example`` is now the configuration
as code (every key the daemon reads, its default, its meaning, the panel's tuned values); this
script says where the live panel has moved away from it.

READ-ONLY and SECRET-SAFE: it reads the live file over ``ssh`` (``cat``, nothing else), parses it
in memory, and REPLACES every secret value with ``<set>`` / ``<empty>`` the moment it is parsed -
before anything is compared, stored or printed. A key is secret when the template tags it
``[secret]`` OR its name looks like a credential (TOKEN / SECRET / PASSWORD / CLIENT_ID / KEY),
so a new credential the template does not know yet is still masked. Default output is key NAMES
only; ``--show-values`` adds the non-secret values (tuning numbers, site paths).

What it reports:
  * ``dead``          set on the panel, read by no code (safe to delete from the panel)
  * ``undocumented``  set on the panel, read by the code, missing from the template (add it)
  * ``value``         a tuned (non-site, non-secret) key whose live value differs from the template
  * ``unset``         active in the template, not set on the panel (the code default applies there)
  * ``set_default``   set on the panel but commented in the template (record the tuned value)
  * ``secret``        a [secret] key the template sets that is empty or missing on the panel
  * ``mode``          the live file is readable by group/other (it holds the device token)
Site keys ([site]: URLs, ids, paths, devices) are compared by presence only.

Exit 0 = no drift, 1 = drift, 2 = the live file could not be read.

    python3 scripts/maintenance/panel_env_drift.py                 # ssh zoe-pi, names only
    python3 scripts/maintenance/panel_env_drift.py --show-values   # + non-secret values
    python3 scripts/maintenance/panel_env_drift.py --live-file F   # a local copy instead of ssh
    python3 scripts/maintenance/panel_env_drift.py --source-keys   # every key the code reads
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SETUP = REPO / "scripts" / "setup"
TEMPLATE = SETUP / "zoe-voice.env.example"
# The files the panel runs (deploy-pi-voice.sh ships the first five; zoe_face_id.py is installed by hand).
SOURCES = ("zoe_voice_daemon.py", "zoe_voice_announce.py", "zoe_voice_enroll_mode.py",
           "zoe_speaker_resnet34.py", "zoe_enroll_flow.py", "zoe_face_id.py")
ENV_HELPERS = {"_env", "_int_env", "_float_env", "_prob_env", "_count_env", "getenv"}
SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSW|CLIENT_ID|(^|_)KEY($|_)")
MASK_SET, MASK_EMPTY = "<set>", "<empty>"
DEFAULT_HOST = "zoe-pi"
DEFAULT_PATH = ".zoe-voice/.env.voice"   # relative to the panel user's home


# ── what the code reads ─────────────────────────────────────────────────────────────────────────────────

def source_keys(files=SOURCES, setup: Path = SETUP) -> tuple[set[str], set[str]]:
    """(primary keys, legacy aliases) every shipped file reads, from the AST: ``os.environ.get``,
    ``os.getenv``, ``os.environ[...]``, ``"X" in os.environ`` and the daemon's ``_env``-style
    helpers (``_env``'s extra arguments are legacy aliases). Underscore-prefixed names are the
    daemon's own process-internal variables, not configuration."""
    primary: set[str] = set()
    legacy: set[str] = set()
    for name in files:
        path = setup / name
        if not path.exists():
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                fn = node.func
                fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
                is_get = (isinstance(fn, ast.Attribute) and fn.attr == "get"
                          and isinstance(fn.value, ast.Attribute) and fn.value.attr == "environ")
                if (is_get or fname in ENV_HELPERS) and node.args and isinstance(node.args[0], ast.Constant) \
                        and isinstance(node.args[0].value, str):
                    primary.add(node.args[0].value)
                    if fname == "_env":
                        legacy |= {a.value for a in node.args[2:] if isinstance(a, ast.Constant)}
            elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) \
                    and node.value.attr == "environ" and isinstance(node.slice, ast.Constant):
                primary.add(node.slice.value)
            elif isinstance(node, ast.Compare) and isinstance(node.left, ast.Constant) \
                    and isinstance(node.left.value, str) \
                    and any(isinstance(c, ast.Attribute) and c.attr == "environ" for c in node.comparators):
                primary.add(node.left.value)
    keep = lambda keys: {k for k in keys if re.fullmatch(r"[A-Z][A-Z0-9_]*", k)}  # noqa: E731
    return keep(primary), keep(legacy)


# ── the template ────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class Entry:
    key: str
    active: bool          # KEY=value (the panel sets it) vs #KEY=value (code default)
    value: str
    tags: set = field(default_factory=set)
    meaning: str = ""


_ASSIGN = re.compile(r"^(#?)([A-Z][A-Z0-9_]*)=(.*)$")
_DESCR = re.compile(r"^#\s+([A-Z][A-Z0-9_]*)\s*((?:\[[a-z]+\]\s*)*):\s*(.+)$")


def parse_template(text: str) -> dict[str, Entry]:
    entries: dict[str, Entry] = {}
    descr: dict[str, tuple[set, str]] = {}
    for raw in text.splitlines():
        line = raw.rstrip()
        m = _DESCR.match(line)
        if m:
            descr[m.group(1)] = (set(re.findall(r"\[([a-z]+)\]", m.group(2))), m.group(3).strip())
            continue
        m = _ASSIGN.match(line)
        if m:
            key = m.group(2)
            if key in entries:
                raise ValueError(f"template lists {key} twice")
            tags, meaning = descr.get(key, (set(), ""))
            entries[key] = Entry(key, m.group(1) == "", _unquote(m.group(3)), set(tags), meaning)
    return entries


def _unquote(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v


# ── the live file ───────────────────────────────────────────────────────────────────────────────────────

def is_secret(key: str, template: dict[str, Entry]) -> bool:
    entry = template.get(key)
    return bool(SECRET_NAME.search(key)) or (entry is not None and "secret" in entry.tags)


def parse_live(text: str, template: dict[str, Entry]) -> dict[str, str]:
    """KEY -> value, with every secret value replaced by <set>/<empty> AT PARSE TIME."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = _unquote(value)
        out[key] = (MASK_SET if value else MASK_EMPTY) if is_secret(key, template) else value
    return out


def read_live(host: str, path: str, timeout: int = 15) -> tuple[str, str]:
    """(file text, octal mode) over ssh; raises RuntimeError on failure. Only ``stat`` and ``cat``."""
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
           f"stat -c %a {path} && cat {path}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"ssh {host} could not read {path} (exit {r.returncode})")
    mode, _, text = r.stdout.partition("\n")
    return text, mode.strip()


# ── the comparison ──────────────────────────────────────────────────────────────────────────────────────

def compare(template: dict[str, Entry], live: dict[str, str], code_keys: set[str],
            mode: str | None = None) -> dict[str, list]:
    drift: dict[str, list] = {k: [] for k in ("dead", "undocumented", "value", "unset", "set_default",
                                              "secret", "mode")}
    for key in sorted(live):
        entry = template.get(key)
        if key not in code_keys:
            drift["dead"].append(key)
        elif entry is None:
            drift["undocumented"].append(key)
        elif not entry.active:
            drift["set_default"].append(key)
        elif is_secret(key, template):
            if live[key] != MASK_SET:
                drift["secret"].append(key)
        elif "site" not in entry.tags and live[key] != entry.value:
            drift["value"].append(key)
    for key, entry in sorted(template.items()):
        if entry.active and key not in live:
            (drift["secret"] if is_secret(key, template) else drift["unset"]).append(key)
    if mode and len(mode) >= 2 and mode[-2:] != "00":
        drift["mode"].append(mode)
    return drift


def render(drift: dict[str, list], template: dict[str, Entry], live: dict[str, str], show_values: bool) -> str:
    labels = {
        "dead": "set on the panel, read by NO code (delete from the panel)",
        "undocumented": "set on the panel, read by the code, NOT in the template (add it)",
        "value": "tuned value differs from the template",
        "unset": "active in the template, NOT set on the panel (code default applies there)",
        "set_default": "set on the panel but commented in the template (record the tuned value)",
        "secret": "secret missing or empty on the panel",
        "mode": "live file is readable by group/other (it holds the device token; want 600)",
    }
    lines = [f"panel env: {len(live)} live keys, {sum(e.active for e in template.values())} active template keys"]
    total = 0
    for kind, keys in drift.items():
        if not keys:
            continue
        total += len(keys)
        lines.append(f"  {kind}: {labels[kind]}")
        for key in keys:
            if kind == "mode":
                lines.append(f"    file mode {key}")
                continue
            extra = ""
            if show_values and not is_secret(key, template):
                tv = template[key].value if key in template else "-"
                lv = live.get(key, "-")
                extra = f"   panel={lv!r} template={tv!r}"
            lines.append(f"    {key}{extra}")
    lines.append("no drift" if total == 0 else f"{total} drift item(s)")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=DEFAULT_HOST, help="ssh target (default zoe-pi)")
    ap.add_argument("--path", default=DEFAULT_PATH, help="live file, relative to the panel user's home")
    ap.add_argument("--live-file", type=Path, help="compare a local copy instead of reading over ssh")
    ap.add_argument("--template", type=Path, default=TEMPLATE)
    ap.add_argument("--show-values", action="store_true", help="also print NON-secret values")
    ap.add_argument("--json", action="store_true", help="machine-readable drift (key names only)")
    ap.add_argument("--source-keys", action="store_true", help="print every key the panel code reads, then exit")
    args = ap.parse_args(argv)

    primary, legacy = source_keys()
    if args.source_keys:
        print("\n".join(sorted(primary | legacy)))
        return 0
    template = parse_template(args.template.read_text(encoding="utf-8"))
    try:
        if args.live_file:
            text, mode = args.live_file.read_text(encoding="utf-8"), None
        else:
            text, mode = read_live(args.host, args.path)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"cannot read the live panel env: {exc}", file=sys.stderr)
        return 2
    live = parse_live(text, template)
    text = ""  # drop the raw text (it held the secrets) before anything else happens
    drift = compare(template, live, primary | legacy, mode)
    if args.json:
        print(json.dumps({"live_keys": len(live), "drift": drift}, indent=2))
    else:
        print(render(drift, template, live, args.show_values))
    return 1 if any(drift.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
