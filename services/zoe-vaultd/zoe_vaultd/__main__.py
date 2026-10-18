"""Entry points. Each unit runs as its own uid (the lab starts them with setpriv; V2 adds systemd units):

  python3 -m zoe_vaultd core --config /etc/zoe-vaultd/config.json            (uid zoe-vault)
  python3 -m zoe_vaultd browser --socket S --group G --allowed-uid U ...     (uid zoe-vault-browser)
  python3 -m zoe_vaultd phone --socket S --group G --core-socket C            (uid zoe-vault-phone)
  python3 -m zoe_vaultd audit-verify /var/lib/zoe-vault/audit.jsonl
"""

from __future__ import annotations

import argparse
import grp
import os
import sys


def _log(prefix: str):
    def log(msg: str) -> None:
        print(f"{prefix}: {msg}", file=sys.stderr, flush=True)
    return log


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="zoe_vaultd")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("core")
    c.add_argument("--config", required=True)
    b = sub.add_parser("browser")
    b.add_argument("--socket", required=True)
    b.add_argument("--group", required=True)
    b.add_argument("--chromium", default="/usr/bin/chromium")
    b.add_argument("--profile-root", required=True)
    b.add_argument("--allowed-uid", type=int, action="append", required=True)
    b.add_argument("--extra-flag", action="append", default=[])
    p = sub.add_parser("phone")
    p.add_argument("--socket", required=True)
    p.add_argument("--group", required=True)
    p.add_argument("--core-socket", required=True)
    v = sub.add_parser("audit-verify")
    v.add_argument("path")
    args = ap.parse_args(argv)

    if args.cmd == "audit-verify":
        from .audit import verify
        ok, n, problem = verify(args.path)
        print(f"{'ok' if ok else 'BROKEN'} lines={n} {problem}".strip())
        return 0 if ok else 1

    if args.cmd == "core":
        from .core import Config, Core, serve
        from .peer import harden_process
        hardening = harden_process()
        cfg = Config.load(args.config)
        core = Core(cfg)
        core.start()
        _log("core")(f"started mode={cfg.unlock_mode} locked={core.locked} hardening={hardening}")
        serve(core)
        return 0

    if args.cmd == "browser":
        from .browser import Launcher
        os.umask(0o077)
        launcher = Launcher(socket_path=args.socket, chromium=args.chromium, profile_root=args.profile_root,
                            allowed_uids=set(args.allowed_uid), extra_flags=args.extra_flag, log=_log("browser"))
        launcher.serve_forever(group_gid=grp.getgrnam(args.group).gr_gid)

    if args.cmd == "phone":
        from .phone import PhoneServer
        srv = PhoneServer(args.socket, args.core_socket)
        os.chown(args.socket, -1, grp.getgrnam(args.group).gr_gid)
        os.chmod(args.socket, 0o660)
        _log("phone")("listening")
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
