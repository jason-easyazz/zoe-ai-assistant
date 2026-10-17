#!/usr/bin/env python3
"""Enable (or disable) the nginx RELAY LISTENER on this box (panel-anywhere phase R1).

    enable_relay_listener.py [--repo DIR] [--ssl-dir DIR] [--env-file FILE] [--skip-flag-check]
    enable_relay_listener.py --disable

The listener (``services/zoe-ui/nginx.d/relay-listener.conf``) is NOT referenced by ``nginx.conf`` directly. ``nginx.conf`` includes
``/etc/nginx/ssl/relay/*.conf`` - a glob that matches nothing on a box without the relay, so nginx starts exactly as before there.
This script is the only thing that puts a file in that glob, and it does so only when EVERYTHING the listener needs is in place:

  * ``ssl/relay/ca.pem`` + ``leaf.pem`` + ``leaf.key`` exist (``household_pki.py init-ca`` / ``issue-leaf``),
  * the leaf verifies against the CA, the key matches the leaf, the leaf is a ``*.zoe.internal`` server certificate and is not
    about to expire,
  * ``ZOE_RELAY_INGRESS`` is on in the zoe-data env file. ORDER MATTERS: with the listener up and the flag off, relayed requests
    would be judged as LAN requests. Turn the flag on and restart zoe-data FIRST.

It never restarts anything. After it succeeds it prints the throwaway ``nginx -t`` pre-flight and the ``docker restart zoe-ui`` to
run (nginx.conf is a single-file bind mount pinned to an inode; deploy.yml restarts the container for the same reason).
``--disable`` removes ``ssl/relay/listener.conf``; restart zoe-ui to drop the listener. Python >= 3.10, stdlib + host openssl.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
LISTENER_SRC = Path("services/zoe-ui/nginx.d/relay-listener.conf")
REQUIRED = ("ca.pem", "leaf.pem", "leaf.key")
MIN_LEAF_VALID_S = 7 * 24 * 3600
# zoe-data.service loads these EnvironmentFile= entries in this order and the LAST assignment wins (systemd semantics); the file this
# script checks must therefore be ALL of them, not just the first.
_ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?ZOE_RELAY_INGRESS\s*=\s*['\"]?([^'\"#\s]*)['\"]?\s*(?:#.*)?$", re.I)
DEFAULT_ENV_FILES = (Path("{repo}/.env"), Path("{repo}/services/zoe-data/.env"), Path("~/.hermes/.env"))


def _openssl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["openssl", *args], capture_output=True, text=True, check=False)


def flag_on(env_files) -> bool:
    """ZOE_RELAY_INGRESS as zoe-data will see it: the last assignment across the env files, in order, is truthy."""
    files = [env_files] if isinstance(env_files, (str, os.PathLike)) else list(env_files)
    value = None
    for f in files:
        try:
            text = Path(f).expanduser().read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            m = _ASSIGN_RE.match(line)
            if m:
                value = m.group(1).strip().lower()
    return value in ("1", "true", "yes", "on")


def problems(relay_dir: Path, env_file) -> list[str]:
    """Everything wrong with enabling the listener now; empty means it is safe to install."""
    out: list[str] = []
    for name in REQUIRED:
        f = relay_dir / name
        if not f.is_file() or f.stat().st_size == 0:
            out.append("missing %s (household_pki.py init-ca, then issue-leaf --hid <hid>)" % f)
    if out:
        return out
    ca, leaf, key = (relay_dir / n for n in REQUIRED)
    v = _openssl("verify", "-CAfile", str(ca), str(leaf))
    if v.returncode != 0 or "OK" not in v.stdout:
        out.append("leaf.pem does not verify against ca.pem: %s" % (v.stdout + v.stderr).strip())
    pub_leaf = _openssl("x509", "-in", str(leaf), "-noout", "-pubkey").stdout
    pub_key = _openssl("pkey", "-in", str(key), "-pubout").stdout
    if not pub_leaf.strip() or pub_leaf != pub_key:
        out.append("leaf.key is not the private key of leaf.pem")
    subj = _openssl("x509", "-in", str(leaf), "-noout", "-ext", "subjectAltName,extendedKeyUsage").stdout
    if not re.search(r"DNS:[a-z0-9-]+\.zoe\.internal\b", subj):
        out.append("leaf.pem has no <hid>.zoe.internal subjectAltName")
    if "TLS Web Server Authentication" not in subj:
        out.append("leaf.pem is not a serverAuth certificate")
    if _openssl("x509", "-in", str(leaf), "-noout", "-checkend", str(MIN_LEAF_VALID_S)).returncode != 0:
        out.append("leaf.pem expires within 7 days (or already has): issue a new leaf first")
    if _openssl("x509", "-in", str(ca), "-noout", "-checkend", "0").returncode != 0:
        out.append("ca.pem has expired")
    if env_file is not None and not flag_on(env_file):
        out.append("ZOE_RELAY_INGRESS is not on in %s (last assignment wins): set it to 1 and restart zoe-data BEFORE enabling the "
                   "listener (listener up + flag off = relayed requests judged as LAN requests)" % env_file)
    return out


def install(repo: Path, relay_dir: Path) -> Path:
    src = (repo / LISTENER_SRC).read_text(encoding="utf-8")
    dest = relay_dir / "listener.conf"
    fd, tmp = tempfile.mkstemp(dir=relay_dir, prefix=".listener.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(src)
        os.chmod(tmp, 0o644)
        os.replace(tmp, dest)  # atomic: nginx never sees half a file
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, default=REPO)
    ap.add_argument("--ssl-dir", type=Path, default=None, help="default: <repo>/ssl")
    ap.add_argument("--env-file", type=Path, action="append", default=None,
                    help="repeatable, in zoe-data.service order (last assignment wins); default: <repo>/.env, <repo>/services/zoe-data/.env, ~/.hermes/.env")
    ap.add_argument("--skip-flag-check", action="store_true")
    ap.add_argument("--disable", action="store_true")
    a = ap.parse_args(argv)
    relay_dir = (a.ssl_dir or a.repo / "ssl") / "relay"
    if a.disable:
        target = relay_dir / "listener.conf"
        if target.exists():
            target.unlink()
            print("removed %s. Run: docker restart zoe-ui" % target)
        else:
            print("the relay listener is not enabled (no %s)" % target)
        return 0
    env_file = None if a.skip_flag_check else (a.env_file or [Path(str(p).format(repo=a.repo)) for p in DEFAULT_ENV_FILES])
    bad = problems(relay_dir, env_file)
    if bad:
        for b in bad:
            print("refusing to enable the relay listener: %s" % b, file=sys.stderr)
        return 2
    dest = install(a.repo, relay_dir)
    print("installed %s" % dest)
    print("pre-flight (throwaway container, same as deploy.yml):")
    print("  docker run --rm --add-host zoe-auth:127.0.0.1 --add-host host.docker.internal:127.0.0.1 \\")
    print("    -v %s/services/zoe-ui/nginx.conf:/etc/nginx/conf.d/default.conf:ro \\" % a.repo)
    print("    -v %s/services/zoe-ui/nginx.d:/etc/nginx/zoe:ro -v %s:/etc/nginx/ssl:ro nginx:alpine nginx -t" % (a.repo, relay_dir.parent))
    print("then: docker restart zoe-ui   (the conf is an inode-pinned file mount; a reload would keep the old config)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
