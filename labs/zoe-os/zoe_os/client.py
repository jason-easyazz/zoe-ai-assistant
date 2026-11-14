"""The skill's side of the socket. A skill process holds this and nothing else of the kernel.

``Client`` is the protocol, one method per op. ``RemoteKernel`` gives a skill written against
the in-process prototype the same shapes (``kernel.spine.publish(event)``,
``kernel.actions.act(...)``) so the SAME skill class runs on the far side of the boundary.
The ``source`` / ``reader`` / ``skill`` names those calls carry are dropped here: the
protocol has no such field, the kernel uses the connection's verified identity.
"""
from __future__ import annotations

import socket
import time

from . import wire
from .events import Event, Kind
from .receipts import Receipt
from .spine import Refused


class Client:
    def __init__(self, path: str, skill: str, credential: bytes, *, timeout: float = 10.0) -> None:
        self.skill, self._timeout, self._id = skill, timeout, 0
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(path)
        hello = self._recv()
        wire.write_frame(self.sock, {"v": wire.VERSION, "skill": skill, "proof": wire.proof(
            credential, bytes.fromhex(hello["nonce"]))}, timeout=timeout)
        self._check(self._recv())

    def close(self) -> None:
        self.sock.close()

    def _recv(self) -> dict:
        reply = wire.read_frame(self.sock, max_frame=wire.MAX_REPLY, idle=self._timeout)
        if reply is None:
            raise ConnectionError("the kernel closed the connection")
        return reply

    @staticmethod
    def _check(reply: dict):
        if reply.get("ok"):
            return reply.get("result")
        err = reply.get("error", {})
        if err.get("code") == wire.REFUSED:
            raise Refused(err.get("reason", ""))
        raise wire.WireError(err.get("code", wire.INTERNAL), err.get("reason", ""))

    def call(self, op: str, **args):
        self._id += 1
        wire.write_frame(self.sock, {"v": wire.VERSION, "id": self._id, "op": op, "args": args},
                         timeout=self._timeout)
        return self._check(self._recv())

    def publish(self, kind, *, subject: str | None, payload: dict, caused_by=(),
                visibility="private", mentions=()) -> Event:
        return wire.event_from_wire(self.call(
            "publish", kind=Kind(kind).value, subject=subject, payload=payload,
            caused_by=list(caused_by), visibility=getattr(visibility, "value", visibility),
            mentions=list(mentions)))

    def subscribe(self, *, kinds=None, subject: str | None = None, from_seq: int | None = None) -> int:
        args: dict = {"subject": subject}
        if kinds:
            args["kinds"] = sorted(Kind(k).value for k in kinds)
        if from_seq is not None:
            args["from_seq"] = from_seq
        return self.call("subscribe", **args)["sub"]

    def poll(self, sub: int, *, max: int = 1, wait_ms: int = 0, ack: int | None = None) -> list[Event]:
        args = {"sub": sub, "max": max, "wait_ms": wait_ms} | ({} if ack is None else {"ack": ack})
        return [wire.event_from_wire(e) for e in self.call("poll", **args)["events"]]

    def drain(self, sub: int) -> list[Event]:
        out: list[Event] = []
        while batch := self.poll(sub, max=wire.MAX_POLL):
            out += batch
        return out

    def act(self, action: str, on_behalf_of: str, args: dict, *, caused_by) -> Receipt:
        return Receipt(**self.call("act", action=action, on_behalf_of=on_behalf_of, args=args,
                                   caused_by=list(caused_by)))

    def forget(self, subject: str, *, actor: str, **more) -> dict:
        return self.call("forget", subject=subject, actor=actor, **more)

    def grant(self, person: str, skill: str, **more) -> dict:
        return self.call("grant", person=person, skill=skill, **more)

    def undo(self, person: str, *, actor: str) -> Receipt:
        return Receipt(**self.call("undo", person=person, actor=actor))

    def revoke(self, person: str, skill: str) -> dict:
        return self.call("revoke", person=person, skill=skill)

    def explain(self, seq: int | None = None, **more) -> dict:
        """``person=`` (a surface asking for a person) and ``last=`` go through as given."""
        return self.call("explain", **({} if seq is None else {"seq": seq}), **more)

    # the Context shape a skill's ``on_event(ev, ctx)`` expects
    now = staticmethod(time.time)

    def run(self, skill, *, from_seq: int | None = None) -> int:
        """Feed a skill everything pending for its manifest's reads. Returns events handled."""
        sub, handled = self.subscribe(kinds=skill.manifest["reads"], from_seq=from_seq), 0
        for ev in self.drain(sub):
            handled += 1
            try:
                skill.on_event(ev, self)
            except Refused:
                pass
        return handled


class _RemoteSub:
    def __init__(self, client: Client, sub: int) -> None:
        self._client, self._sub = client, sub

    def poll(self, timeout: float = 0.0) -> Event | None:
        got = self._client.poll(self._sub, wait_ms=int(timeout * 1000))
        return got[0] if got else None


class _RemoteSpine:
    def __init__(self, client: Client) -> None:
        self._c = client

    def publish(self, event: Event) -> Event:
        return self._c.publish(event.kind, subject=event.subject, payload=event.payload,
                               caused_by=event.caused_by, visibility=event.visibility,
                               mentions=event.mentions)

    def subscribe(self, reader: str = "", *, kinds=None, subject=None, from_seq=None) -> _RemoteSub:
        return _RemoteSub(self._c, self._c.subscribe(kinds=kinds, subject=subject, from_seq=from_seq))

    def replay(self, reader: str = "", from_seq: int = 0, *, kinds=None, subject=None) -> list[Event]:
        return self._c.drain(self._c.subscribe(kinds=kinds, subject=subject, from_seq=from_seq))


class _RemoteActions:
    def __init__(self, client: Client) -> None:
        self._c = client

    def act(self, skill: str, action: str, on_behalf_of: str, args: dict, *, caused_by) -> Receipt:
        return self._c.act(action, on_behalf_of, args, caused_by=caused_by)


class RemoteKernel:
    """What ``kernel`` looks like from a skill process: two narrow views, no objects behind them."""

    def __init__(self, client: Client) -> None:
        self.spine, self.actions = _RemoteSpine(client), _RemoteActions(client)
