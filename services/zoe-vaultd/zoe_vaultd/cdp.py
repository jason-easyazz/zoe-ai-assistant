"""A minimal Chrome DevTools Protocol client over the private DevTools pipe (credential-vault.md 5.1 and 6.2).

Chromium's `--remote-debugging-pipe` speaks NUL-terminated JSON. `zoe-vault-browser` bridges that pipe onto a Unix
socket that only the two broker users can open; this client talks to it with flattened sessions. Every event is
recorded, in arrival order, so the fill can ask "did anything touch my binding since index N?" (6.2 step 3).
"""

from __future__ import annotations

import json
import socket
import threading


class CDPError(Exception):
    pass


class CDP:
    def __init__(self, sock: socket.socket, *, default_timeout: float = 20.0) -> None:
        self.sock = sock
        self.default_timeout = default_timeout
        self._id = 0
        self._send_lock = threading.Lock()
        self._cv = threading.Condition()
        self._results: dict[int, dict] = {}
        self.events: list[tuple[str, dict, str | None]] = []   # (method, params, sessionId)
        self.closed = False
        self._listeners: list = []
        self._reader = threading.Thread(target=self._read_loop, name="cdp-reader", daemon=True)
        self._reader.start()

    @classmethod
    def connect_unix(cls, path: str, **kw) -> "CDP":
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(path)
        return cls(s, **kw)

    def on_event(self, fn) -> None:
        """fn(method, params, session_id) runs on the reader thread; keep it short and never block on send()."""
        self._listeners.append(fn)

    def _read_loop(self) -> None:
        buf = b""
        try:
            while True:
                chunk = self.sock.recv(1 << 16)
                if not chunk:
                    break
                buf += chunk
                while b"\0" in buf:
                    raw, buf = buf.split(b"\0", 1)
                    if not raw:
                        continue
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    self._dispatch(msg)
        except OSError:
            pass
        finally:
            with self._cv:
                self.closed = True
                self._cv.notify_all()

    def _dispatch(self, msg: dict) -> None:
        if "id" in msg:
            with self._cv:
                self._results[msg["id"]] = msg
                self._cv.notify_all()
            return
        method = msg.get("method")
        if not method:
            return
        params = msg.get("params") or {}
        sid = msg.get("sessionId")
        for fn in list(self._listeners):
            try:
                fn(method, params, sid)
            except Exception:  # a listener bug must not kill the reader
                pass
        with self._cv:
            self.events.append((method, params, sid))
            self._cv.notify_all()

    def event_count(self) -> int:
        with self._cv:
            return len(self.events)

    def events_since(self, index: int) -> list[tuple[str, dict, str | None]]:
        with self._cv:
            return list(self.events[index:])

    def send(self, method: str, params: dict | None = None, *, session: str | None = None,
             timeout: float | None = None) -> dict:
        with self._send_lock:
            self._id += 1
            mid = self._id
            msg: dict = {"id": mid, "method": method, "params": params or {}}
            if session:
                msg["sessionId"] = session
            data = json.dumps(msg).encode() + b"\0"
            try:
                self.sock.sendall(data)
            except OSError as exc:
                raise CDPError(f"{method}: send failed") from exc
        deadline = timeout if timeout is not None else self.default_timeout
        with self._cv:
            self._cv.wait_for(lambda: mid in self._results or self.closed, timeout=deadline)
            if mid not in self._results:
                raise CDPError(f"{method}: closed" if self.closed else f"{method}: timeout")
            res = self._results.pop(mid)
        if "error" in res:
            raise CDPError(f"{method}: {res['error'].get('message', 'error')}")
        return res.get("result") or {}

    def wait_event(self, predicate, *, start: int = 0, timeout: float = 10.0):
        """Block until an event at index >= start satisfies predicate(method, params, sid); returns it or None."""
        with self._cv:
            idx = start

            def find():
                nonlocal idx
                while idx < len(self.events):
                    ev = self.events[idx]
                    idx += 1
                    if predicate(*ev):
                        return ev
                return None

            found = [None]

            def ready():
                found[0] = find()
                return found[0] is not None or self.closed

            self._cv.wait_for(ready, timeout=timeout)
            return found[0]

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
