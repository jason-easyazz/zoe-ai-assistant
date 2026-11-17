"""The brain's adapter: tool calls in, kernel proposals out, receipt summaries back.

What the brain process holds: a socket path and a credential. No database path, no driver, no
store, no member identity of its own. Who the turn is for, and which utterance it came from,
are handed in by the trusted voice surface as a ``Turn``: the model never writes either.

* ``tools()`` is DERIVED from the kernel's own ``describe`` answer (the actions the brain's
  manifest names that declared their ``params``). Parameters marked ``from: turn`` are not shown
  to the model; they are filled from the ``Turn``.
* ``propose()`` turns one tool call into one ``act`` over the socket. The kernel decides
  (manifest, the person's grant, the cause, the arguments), performs, and answers with a receipt
  or a refusal. The model is given ONE short sentence built here from that answer, never the
  receipt itself, a store row or a reason string it could be talked into repeating wrongly.
* A kernel that is not there is an answer too: ``unavailable`` (never sent: nothing changed) or
  ``unknown`` (sent, no reply: it may or may not have landed; the kernel's recovery will say).

Imports: the standard library and the socket client. Nothing that opens a store.
"""
from __future__ import annotations

import dataclasses

from .. import wire
from ..client import Client
from ..receipts import check_params
from ..spine import Refused

# kernel refusal reason (a fixed string) -> what the brain may honestly say
SAY = {
    "no grant from that person": "I am not permitted to do that for you. Nothing changed.",
    "asks for more than its manifest": "That is not something I am able to do. Nothing changed.",
    "no such action": "That is not something I am able to do. Nothing changed.",
    "unknown argument": "I could not do that: the request was not valid. Nothing changed.",
    "missing argument": "I could not do that: the request was incomplete. Nothing changed.",
    "wrong argument type": "I could not do that: the request was not valid. Nothing changed.",
    "needs exactly one of its alternative arguments": "I could not do that: the request was incomplete. Nothing changed.",
    "the person has not accepted an offer for this": "That needs your yes first. Nothing changed.",
    "derives from a forgotten event": "I could not do that: what you asked for has been forgotten.",
}
SAY_OTHER = "I could not do that. Nothing changed."


@dataclasses.dataclass(frozen=True)
class Turn:
    member: str          # who is speaking: from the trusted surface, never from the model
    cause: int           # the utterance event the surface recorded for this turn


@dataclasses.dataclass(frozen=True)
class Outcome:
    status: str          # done | unconfirmed | refused | invalid | unavailable | unknown
    say: str             # the one sentence the brain gets back
    receipt: int | None = None
    reason: str = ""             # the kernel's (or the adapter's) fixed reason code, for diagnostics
    shape: dict | None = None    # argument NAMES present / missing / unknown / wrong-typed: never values


def shape(visible: dict, arguments: object) -> dict:
    """Declared argument names only; unknown keys become a fixed code, never caller text."""
    args = arguments if isinstance(arguments, dict) else {}
    return {"present": sorted(set(args) & set(visible)),
            "missing": sorted(n for n, s in visible.items() if s.get("required") and n not in args),
            "unknown": ["unknown_argument"] if set(args) - set(visible) else [],
            "bad_value": sorted(n for n, s in visible.items() if n in args and check_params({n: s}, {n: args[n]}))}


def tool_name(action: str) -> str:
    return action.replace(".", "_")


class BrainBridge:
    def __init__(self, socket_path: str, credential: bytes, *, skill: str = "brain",
                 timeout: float = 5.0, prevalidate: bool = True) -> None:
        self._conn = (socket_path, skill, credential, timeout)
        self.prevalidate = prevalidate
        self._client: Client | None = None
        self._catalog: dict[str, dict] | None = None

    # ---- connection ---------------------------------------------------------------------------------
    def _connect(self) -> Client:
        if self._client is None:
            self._client = Client(*self._conn[:3], timeout=self._conn[3])
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ---- the tool list, derived ---------------------------------------------------------------------
    def catalog(self, refresh: bool = False) -> dict[str, dict]:
        if self._catalog is None or refresh:
            self._catalog = {tool_name(a["action"]): a for a in self._connect().describe()}
        return self._catalog

    @staticmethod
    def _visible(entry: dict) -> dict:
        """What the model may set: never a ``from`` param (``turn``: filled here; ``caller``: a non-model client's, e.g. an
        absolute ``due_at`` zoe-data already knows)."""
        return {n: s for n, s in entry["params"].items() if "from" not in s}

    @staticmethod
    def _required(vis: dict) -> list:
        """Required for the model: declared required, or the only visible member of a ``one_of`` group."""
        groups: dict[str, list] = {}
        for n, s in vis.items():
            if "one_of" in s:
                groups.setdefault(s["one_of"], []).append(n)
        return sorted({n for n, s in vis.items() if s.get("required")} | {g[0] for g in groups.values() if len(g) == 1})

    def tools(self) -> list[dict]:
        """OpenAI-style function schemas, one per action the kernel described. An action that
        needs a person's yes is left out: the brain cannot give it, so it is not offered."""
        out = []
        for name, entry in sorted(self.catalog().items()):
            if entry["needs_accept"]:
                continue
            vis = self._visible(entry)
            out.append({"type": "function", "function": {
                "name": name, "description": entry["doc"],
                "parameters": {"type": "object", "additionalProperties": False,
                               "required": self._required(vis),
                               "properties": {n: {k: s[k] for k in ("type", "description", "pattern", "minimum", "maximum") if k in s}
                                              for n, s in vis.items()}}}})
        return out

    # ---- one proposal -------------------------------------------------------------------------------
    def propose(self, turn: Turn, tool: str, arguments: object) -> Outcome:
        try:
            entry = self.catalog().get(tool)
        except (OSError, ConnectionError, wire.WireError):
            self.close()
            return Outcome("unavailable", "I could not do that: I cannot reach my action service. "
                           "Nothing changed.")
        if entry is None:
            return Outcome("invalid", "I could not do that: there is no such tool. Nothing changed.")
        if self.prevalidate:
            bad = check_params(self._visible(entry), arguments)
            if bad:
                return Outcome("invalid", SAY[bad], reason=bad, shape=shape(self._visible(entry), arguments))
        args = dict(arguments) if isinstance(arguments, dict) else {}
        if any("from" in entry["params"].get(k, {}) for k in args):
            return Outcome("invalid", SAY["unknown argument"], reason="unknown argument",
                           shape=shape(self._visible(entry), arguments))    # the model never supplies what the turn does
        for name, spec in entry["params"].items():
            if spec.get("from") == "turn":
                args[name] = turn.cause
        try:
            client = self._connect()
        except (OSError, ConnectionError, wire.WireError):
            self.close()
            return Outcome("unavailable", "I could not do that: I cannot reach my action service. "
                           "Nothing changed.")
        try:
            r = client.act(entry["action"], turn.member, args, caused_by=[turn.cause])
        except Refused as exc:
            reason = str(exc).split(": ", 2)[-1]        # fixed wire reason; accepts older kernel formatting
            return Outcome("refused", SAY.get(reason, SAY_OTHER), reason=reason)
        except wire.WireError as exc:
            return Outcome("invalid" if exc.code == wire.BAD_REQUEST else "refused", SAY_OTHER)
        except (OSError, ConnectionError):
            self.close()        # the request was sent: whether it landed is the kernel's to say later
            return Outcome("unknown", "I could not confirm that went through. It may not have.")
        shown = ", ".join(f"{k}={v}" for k, v in sorted(arguments.items()))
        if r.verified:
            return Outcome("done", f"Done: {entry['doc'].lower()} ({shown}). Confirmed by "
                           f"read-back. Receipt {r.id}.", r.seq)
        return Outcome("unconfirmed", "I tried, but I could not confirm it worked. "
                       "Do not say it is done.", r.seq)
