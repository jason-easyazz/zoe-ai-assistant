#!/usr/bin/env python3
"""Switch polly's claude-sdk brain between the Max subscription and a dedicated API key.

    omnigent_claude_auth_mode.py status
    omnigent_claude_auth_mode.py api            # polly's brain bills the Console key
    omnigent_claude_auth_mode.py subscription   # remove the block: back to stored/OAuth-token auth

WHY THIS EXISTS. Claude Code has no automatic "subscription limit hit -> use the API key"
fallback (its docs: the session/weekly limit blocks requests until reset; `/usage-credits`
is a manual purchase). The only lever omnigent offers is the global `auth:` block of
~/.omnigent/config.yaml, which omnigent re-reads for EVERY new claude-sdk launch
(`runtime/workflow.py::_load_global_auth` — no restart needed). The claude-sdk executor
strips a plain ANTHROPIC_API_KEY before spawning the CLI (to force subscription auth), so
the supported route is `auth: {type: api_key, api_key: $VAR}`, which omnigent turns into a
Claude `apiKeyHelper` for the SDK-spawned CLI. The key lives ONLY in the container env
(from the out-of-repo env file) under ZOE_OMNIGENT_ANTHROPIC_API_KEY; config.yaml stores
the `$VAR` reference, never the value.

The edit is a marker-delimited block so it never touches the rest of config.yaml (the
entrypoint-seeded providers: block, host id, acp agents) and is trivially reversible.
Nothing here prints a secret. Runs on the host by default (pipes itself into the container
with `docker exec -i`); `--in-container` is the mode that actually edits, and is what the
offline tests drive with `--config`.

Limits: this switches NEW claude-sdk sessions only — a session already running keeps the
credential it started with. It does not touch native `claude_code` workers (they ride the
stored login / CLAUDE_CODE_OAUTH_TOKEN). Unverified against a live dispatch until the owner
runs the smoke test in docs/knowledge/omnigent-cross-review.md.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile

KEY_VAR = "ZOE_OMNIGENT_ANTHROPIC_API_KEY"
BEGIN = "# >>> zoe: polly claude-sdk API auth (managed by omnigent_claude_auth_mode.py) >>>"
END = "# <<< zoe: polly claude-sdk API auth <<<"
BLOCK = f"{BEGIN}\nauth:\n  type: api_key\n  api_key: ${KEY_VAR}\n{END}\n"
DEFAULT_CONFIG = os.path.expanduser("~/.omnigent/config.yaml")
CONTAINER = os.environ.get("OMNIGENT_CONTAINER", "zoe-omnigent")

_BLOCK_RE = re.compile(rf"^{re.escape(BEGIN)}\n.*?^{re.escape(END)}\n", re.S | re.M)
_FOREIGN_AUTH_RE = re.compile(r"^auth:", re.M)


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


def _write_atomic(path: str, text: str) -> None:
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".config.yaml.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        try:
            os.chmod(tmp, os.stat(path).st_mode & 0o777)
        except FileNotFoundError:
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def mode_of(text: str) -> str:
    if _BLOCK_RE.search(text):
        return "api"
    if _FOREIGN_AUTH_RE.search(text):
        return "foreign-auth-block"
    return "subscription"


def apply(action: str, config: str, env: dict[str, str]) -> tuple[int, str]:
    text = _read(config)
    current = mode_of(text)
    if action == "status":
        key = "set" if env.get(KEY_VAR) else "NOT set"
        return 0, f"mode={current} {KEY_VAR}={key} config={config}"
    if current == "foreign-auth-block":
        return 2, "refusing: config.yaml already has an unmanaged top-level `auth:` block; edit it by hand"
    if action == "api":
        if not env.get(KEY_VAR):
            # An `auth:` block that references an unset variable makes omnigent raise on
            # EVERY launch (check_unresolved_env_vars) — never write it without the key.
            return 2, f"refusing: {KEY_VAR} is not set in this environment (see the owner runbook)"
        if current == "api":
            return 0, "already in api mode"
        sep = "" if (not text or text.endswith("\n")) else "\n"
        _write_atomic(config, text + sep + ("\n" if text else "") + BLOCK)
        return 0, "api mode ON for new claude-sdk sessions (polly brain bills the dedicated key)"
    if action == "subscription":
        if current != "api":
            return 0, "already in subscription mode"
        rest = _BLOCK_RE.sub("", text).rstrip("\n")
        _write_atomic(config, rest + "\n" if rest.strip() else "")
        return 0, "api mode OFF (new claude-sdk sessions use stored login / CLAUDE_CODE_OAUTH_TOKEN)"
    return 1, f"unknown action: {action}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=("status", "api", "subscription"))
    ap.add_argument("--in-container", action="store_true", help="edit the config in THIS environment")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ns = ap.parse_args(argv)
    if ns.in_container:
        rc, msg = apply(ns.action, ns.config, dict(os.environ))
        print(msg, file=sys.stderr if rc else sys.stdout)
        return rc
    # --config is deliberately NOT forwarded: DEFAULT_CONFIG must expand ~ INSIDE the
    # container (HOME=/root there); a host-expanded path would point at the wrong file.
    with open(__file__, "rb") as fh:
        src = fh.read()
    proc = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "python3", "-", "--in-container", ns.action],
        input=src, check=False,
    )
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
