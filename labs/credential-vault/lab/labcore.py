"""Starts a zoe_vaultd unit, optionally with ONE protection deliberately broken (a negative control).

The production package has no switches for this: every break is a monkeypatch here, in lab code, so a negative
control can never be left on in the broker itself. Usage:
  labcore.py [--patch NAME] -- <zoe_vaultd arguments>      e.g.  labcore.py --patch nopeer -- core --config C
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, "/opt/zoe-vaultd")

from zoe_vaultd import browser, core, fill, origin, policy, vault  # noqa: E402
from zoe_vaultd.__main__ import main  # noqa: E402


def _naive_reg(host: str) -> str:
    return ".".join(host.split(".")[-2:])


def substring() -> None:
    """V2 NC: compare origins by host substring."""
    origin.origin_equal = lambda a, b: a is not None and b is not None and (b.host in a.host or a.host in b.host)


def nopsl() -> None:
    """V2 NC: base domain = the last two labels (no Public Suffix List, no cross-check with the browser)."""
    def rule_matches(rule, o, *, psl, browser_registrable, lab_profile):
        if not origin.scheme_allowed(o, lab_profile=lab_profile):
            return False, "scheme_refused"
        if rule.kind == origin.KIND_HOST:
            return (o == rule.origin, "ok" if o == rule.origin else "origin_mismatch")
        return (_naive_reg(o.host) == _naive_reg(rule.origin.host), "ok")
    origin.rule_matches = rule_matches
    from zoe_vaultd import psl as pslmod
    pslmod.PublicSuffixList.registrable_domain = lambda self, host: _naive_reg(host)


def inherit() -> None:
    """V2 NC: a frame's origin is whatever securityOrigin says, so about:blank / srcdoc inherit the parent's."""
    import dataclasses
    real = origin.check_frame

    def check_frame(frame, ancestors, rules, *, psl, lab_profile):
        # measured: Chromium does not report the parent's origin as an about:blank / srcdoc frame's securityOrigin,
        # so the broken rule has to do the inheriting itself, which is exactly the mistake this control models
        if frame.url.startswith("about:") and ancestors:
            frame = dataclasses.replace(frame, url=ancestors[0].url, security_origin=ancestors[0].security_origin,
                                        world_origin=None, secure=None)
        return real(frame, ancestors, rules, psl=psl, lab_profile=lab_profile)
    origin.check_frame = check_frame


def logsecret() -> None:
    """V3 NC: log the value at debug."""
    real = core.run_login

    def run_login(cdp, **kw):
        print(f"debug fill user={kw.get('username')} value={kw.get('password')}", file=sys.stderr, flush=True)
        return real(cdp, **kw)
    core.run_login = run_login


def otp_via_client() -> None:
    """V3 NC: the one-time code goes through zoe-data's uid instead of the phone unit."""
    core.ROLE_VERBS = {**core.ROLE_VERBS, "client": core.CLIENT_VERBS | {"otp"}}


def nosync() -> None:
    """V4 NC: serve from the cache without syncing."""
    vault.BwClient.sync = lambda self: None


def timeout_allow() -> None:
    """V5 NC: an unanswered approval defaults to ALLOW at its deadline."""
    def _expire(self, p):
        if p.state == "pending" and self._clock() >= p.expires:
            p.state = "approved"
    policy.Policy._expire = _expire


def trust_approval_id() -> None:
    """V6 NC: accept a caller-asserted approval (proposal) id in place of the capability."""
    real = policy.Policy.consume

    def consume(self, token, *, member, errand, step, item, operation="login"):
        try:
            return real(self, token, member=member, errand=errand, step=step, item=item, operation=operation)
        except policy.PolicyRefusal:
            p = self._proposals.get(token or "")
            if p is None:
                raise
            s = next(x for x in p.steps if x.step_id == step)
            return policy.Capability(member=member, item_id=item, errand=errand, step_id=step, operation=operation,
                                     origin=s.origin, login_url=s.login_url, category=s.category, label=s.label,
                                     expires=self._clock() + 60)
    policy.Policy.consume = consume


def debug_get() -> None:
    """V7 NC: a debug `get` verb that returns the item."""
    core.VERBS = core.VERBS | {"get"}
    core.ROLE_VERBS = {**core.ROLE_VERBS, "client": core.CLIENT_VERBS | {"get"}}
    core.Core.verb_get = lambda self, uid, req: {"ok": True, "item": self.bw.get_item(str(req.get("item")))}


def nopeer() -> None:
    """V8 NC: no SO_PEERCRED allow-list (every uid that can open the socket is a client)."""
    core.Core.role_for = lambda self, uid: "client"


def norecheck() -> None:
    """V13 NC: no binding, no re-check before the insert, no landed check: focus, then type."""
    def insert(self, b, f, which, value, frame_origin):
        expr = "globalThis.__zvUser" if which == "user" else "globalThis.__zvField"
        obj = self.cdp.send("Runtime.evaluate", {"expression": expr, "contextId": b.context_id}, session=f.session)
        oid = (obj.get("result") or {}).get("objectId")
        try:
            self.cdp.send("DOM.focus", {"objectId": oid}, session=f.session)
        except fill.CDPError:
            pass
        self.cdp.send("Input.insertText", {"text": value}, session=f.session)
    fill.LoginRun.insert = insert
    fill.LoginRun.binding_valid = lambda self, b: True
    fill.LoginRun.frame_unchanged = lambda self, b: True
    fill.LoginRun.check_targets = lambda self, f, ctx, frame_origin: None   # part of the re-check before insert


def unlock_for_client() -> None:
    """V18 NC: allow `unlock` for zoe-data's peer uid."""
    core.ROLE_VERBS = {**core.ROLE_VERBS, "client": core.CLIENT_VERBS | {"unlock"}}


SHARED_DEVTOOLS = "/run/zoe-vault-browser/shared.sock"


def shared_devtools() -> None:
    """V17 NC: the login browser's DevTools connection is SHARED: a second endpoint, open to every uid, joins the
    live browser the core is driving (what a TCP --remote-debugging-port gives any local process)."""
    import json
    import selectors
    import socket

    def _bridge(conn, to_chrome, from_chrome, pid):
        try:
            os.unlink(SHARED_DEVTOOLS)
        except FileNotFoundError:
            pass
        ls = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        ls.bind(SHARED_DEVTOOLS)
        os.chmod(SHARED_DEVTOOLS, 0o666)
        ls.listen(1)
        sel = selectors.DefaultSelector()
        sel.register(conn, selectors.EVENT_READ, "client")
        sel.register(from_chrome, selectors.EVENT_READ, "chrome")
        sel.register(ls, selectors.EVENT_READ, "listen")
        extra, ebuf, cbuf, offset = None, b"", b"", 1_000_000_000
        try:
            while browser._alive(pid):
                for key, _ in sel.select(timeout=1.0):
                    if key.data == "client":
                        data = conn.recv(1 << 16)
                        if not data:
                            return
                        browser._write_all(to_chrome, data)
                    elif key.data == "listen":
                        extra, _ = ls.accept()
                        sel.register(extra, selectors.EVENT_READ, "extra")
                    elif key.data == "extra":
                        data = extra.recv(1 << 16)
                        if not data:
                            sel.unregister(extra)
                            extra = None
                            continue
                        ebuf += data
                        while b"\0" in ebuf:
                            raw, ebuf = ebuf.split(b"\0", 1)
                            msg = json.loads(raw)
                            msg["id"] = msg["id"] + offset
                            browser._write_all(to_chrome, json.dumps(msg).encode() + b"\0")
                    else:
                        data = os.read(from_chrome, 1 << 16)
                        if not data:
                            return
                        cbuf += data
                        while b"\0" in cbuf:
                            raw, cbuf = cbuf.split(b"\0", 1)
                            msg = json.loads(raw)
                            if msg.get("id", 0) >= offset:
                                msg["id"] -= offset
                                if extra is not None:
                                    extra.sendall(json.dumps(msg).encode() + b"\0")
                                continue
                            conn.sendall(raw + b"\0")
                            if "id" not in msg and extra is not None:
                                extra.sendall(raw + b"\0")   # every event, so the second client learns sessions
        finally:
            ls.close()
            try:
                os.unlink(SHARED_DEVTOOLS)
            except FileNotFoundError:
                pass
    browser.Launcher._bridge = staticmethod(_bridge)


def group_widened() -> None:
    """V17 sub-control: the DevTools socket's mode widened to everyone (the peer check stays)."""
    real = browser.Launcher.bind

    def bind(self):
        srv = real(self)
        os.chmod(self.socket_path, 0o666)
        return srv
    browser.Launcher.bind = bind


PATCHES = {f.__name__: f for f in (substring, nopsl, inherit, logsecret, otp_via_client, nosync, timeout_allow,
                                   trust_approval_id, debug_get, nopeer, norecheck, unlock_for_client,
                                   shared_devtools, group_widened)}

if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--patch"]:
        name = args[1]
        PATCHES[name]()
        print(f"labcore: NEGATIVE CONTROL {name} applied", file=sys.stderr, flush=True)
        args = args[2:]
    if args[:1] == ["--"]:
        args = args[1:]
    sys.exit(main(args))
