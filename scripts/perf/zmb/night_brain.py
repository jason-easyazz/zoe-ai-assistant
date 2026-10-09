"""The lab's FAKE nightly brain: a deterministic, rule-based stand-in for the model the night mind (``services/zoe-data/night_mind.py``) calls.

It answers the two prompts the pass sends - ``TASK: MOMENTS`` (one chunk of the owner's words in, a JSON list of moments out) and ``TASK: THREADS``
(tonight's moments and the open threads in, a JSON list of create / update operations out) - from word lists and shape rules, with no model, no network and
no randomness. It proves the PLUMBING and the checks around the model (chunking, the verbatim-quote test, the observation gate, the stale test, the thread
bookkeeping, the restraint floor, the call budget), never what a real 4B does: the real clone measures that (``--model-url``, the bake-off window).

``lies`` makes it behave like a model that makes the mistakes the K cells plant (``zmb.life.Life.proposals_*``): extra moments whose quote is a
fabricated link, a hedged restatement, an inference presented as said, or a restated old value. A correct pass drops or holds every one of them; the cells
turn red when a control removes the check that does it. ``skip_moments`` / ``noise`` are test knobs.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

_LINE = re.compile(r"^\[(m\d+)\]\s+([A-Z][a-z]{2} \d{1,2} [A-Z][a-z]{2}):\s+(.*)$")
_NEW = re.compile(r'^(q\d+) \[([^\]]*)\] (\w+) (\w+): "(.*)"$')
_OLD = re.compile(r'^(t[\w-]+) "(.*)" last [^:]*: "(.*)"$')
_NAME = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]{2,}\b")
_WORD = re.compile(r"[a-z0-9]{4,}")
_STOP = frozenset("""about after again also been before being could does from have into just like more much only other over really should soon some still than that their
them then there they this those what when where which while will with would your week today morning night next""".split())

_HEALTH = re.compile(r"\b(knee|sore|physio|doctor|dr|dentist|hospital|pain|sick|injur\w*|scan|headache|surgery|operation|results|medic\w*|allerg\w*)\b", re.I)
_CHANGE = re.compile(r"\b(moved to|has moved|have moved|switched|instead|no longer|is off|called off|cancell?ed|stopped|quit|changed to)\b", re.I)
_DONE = re.compile(r"\b(went (?:really )?well|so much better|is better now|all done|finished|resolved|it'?s over)\b", re.I)
_PLAN = re.compile(r"\b(on the \d+(?:st|nd|rd|th)?|next \w+|tomorrow|on (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|going to|starts?|interview|booked)\b", re.I)
_FEEL: "list[tuple[str, re.Pattern]]" = [
    ("worried", re.compile(r"\b(anxious|worried|nervous|dreading|on edge|fed up|can'?t switch my brain off)\b", re.I)),
    ("sad", re.compile(r"\b(sad|upset|gutted|miserable|lonely|heartbroken)\b", re.I)),
    ("stressed", re.compile(r"\b(stressed|overwhelmed|exhausted|drained|burnt out)\b", re.I)),
    ("angry", re.compile(r"\b(angry|furious|annoyed)\b", re.I)),
    ("relieved", re.compile(r"\b(relieved|so much better)\b", re.I)),
    ("excited", re.compile(r"\b(excited|can'?t wait)\b", re.I)),
    ("proud", re.compile(r"\b(proud)\b", re.I)),
    ("happy", re.compile(r"\b(happy|glad|went (?:really )?well)\b", re.I)),
]
#: weight 3: an event big enough that the owner would want it remembered
_MAJOR = re.compile(r"\b(offer|accepted|got the job|died|funeral|diagnos\w*|surgery|operation|wedding|baby|engaged|redundan\w*)\b", re.I)


def _quote_of(text: str) -> str:
    words = text.split()
    if len(words) <= 25:
        return text
    first = re.split(r"(?<=[.!?])\s+", text)[0]
    return first if len(first.split()) <= 40 else " ".join(words[:25])


def _names(text: str) -> "list[str]":
    from night_mind import names_in
    return [n.capitalize() for n in names_in(text)]


def classify(text: str) -> "dict[str, Any]":
    """The fake brain's reading of ONE turn: kind / who / feeling / weight / later (also the K12 labelled set's candidate answer)."""
    who = _names(text)
    feeling = next((f for f, rx in _FEEL if rx.search(text)), "none")
    health = bool(_HEALTH.search(text))
    major = bool(_MAJOR.search(text))
    kind = ("change" if _CHANGE.search(text) else "health" if health else "feeling" if feeling != "none" else "plan" if _PLAN.search(text) else
            "progress" if major else "person" if who else "progress")
    weight = 3 if major else (2 if (feeling != "none" or health or kind in ("plan", "change")) else 1)
    later = "done" if _DONE.search(text) else ("open" if kind in ("plan", "progress", "change", "person", "health") else "na")
    return {"kind": kind, "who": who, "feeling": feeling, "weight": weight, "later": later}


def interesting(text: str) -> bool:
    c = classify(text)
    return bool(c["who"]) or c["feeling"] != "none" or bool(_HEALTH.search(text))


class FakeNightBrain:
    """``__call__(messages, max_tokens) -> str``: install with ``night_mind.set_llm``. ``lies`` = kinds of mistake to add (the ``Life.proposals_*`` names:
    ``true`` (a paraphrase), ``fabricated``, ``stale``, ``hedged``, ``said``); ``life`` supplies their sentences. ``calls`` / ``log`` record what it was asked."""

    def __init__(self, lies: "tuple[str, ...]" = (), life: Any = None, *, skip_moments: bool = False, wrong_ids: bool = False, bad_json: bool = False,
                 labels: "Optional[dict[str, dict]]" = None):
        self.lies, self.life = tuple(lies), life
        self.skip_moments, self.wrong_ids, self.bad_json = skip_moments, wrong_ids, bad_json
        self.labels = labels or {}
        self.calls = 0
        self.log: "list[dict[str, Any]]" = []

    # ── dispatch ──────────────────────────────────────────────────────────────────────────────────────────────
    def __call__(self, messages: list, max_tokens: int) -> str:
        self.calls += 1
        user = str(messages[-1]["content"])
        if self.bad_json:
            return "I am sorry, here is some prose, not JSON."
        if user.startswith("TASK: MOMENTS"):
            out = self._moments(user)
            self.log.append({"task": "moments", "prompt_chars": len(user), "out": len(out)})
            return out
        if user.startswith("TASK: THREADS"):
            out = self._threads(user)
            self.log.append({"task": "threads", "prompt_chars": len(user), "out": len(out)})
            return out
        return "{}"

    # ── stage 2 ───────────────────────────────────────────────────────────────────────────────────────────────
    def _moments(self, prompt: str) -> str:
        lines = [m.groups() for m in (_LINE.match(ln) for ln in prompt.splitlines()) if m]
        moments: "list[dict[str, Any]]" = []
        if not self.skip_moments:
            for alias, _day, text in lines:
                lab = self.labels.get(text)
                if lab is None and not interesting(text):
                    continue
                c = lab or classify(text)
                moments.append({"ids": [alias], "quote": _quote_of(text), "kind": c["kind"], "who": c["who"], "feeling": c["feeling"],
                                "weight": c["weight"], "later": c["later"]})
            moments.sort(key=lambda m: -m["weight"])
            moments = moments[:8]
        if self.lies and self.life is not None and lines:
            moments += self._lies(lines)
        if self.wrong_ids and lines:
            moments.append({"ids": ["m9999"], "quote": lines[0][2], "kind": "other", "who": [], "feeling": "none", "weight": 1, "later": "na"})
        return json.dumps({"moments": moments})

    def _lies(self, lines: "list[tuple]") -> "list[dict[str, Any]]":
        """Moments a careless model would add: a sentence the owner never said, pinned on a real line (so the id is valid and only the QUOTE betrays it)."""
        kinds = {"true": self.life.proposals_true, "fabricated": self.life.proposals_fabricated, "stale": self.life.proposals_stale,
                 "hedged": self.life.proposals_hedged, "said": self.life.proposals_presented_as_said}
        out = []
        alias = lines[0][0]
        for k in self.lies:
            for text in kinds.get(k, []):
                out.append({"ids": [alias], "quote": text, "kind": "person", "who": _names(text), "feeling": "none", "weight": 2, "later": "open"})
        return out

    # ── stage 3 ───────────────────────────────────────────────────────────────────────────────────────────────
    def _threads(self, prompt: str) -> str:
        old = [m.groups() for m in (_OLD.match(ln) for ln in prompt.splitlines()) if m]
        new = [m.groups() for m in (_NEW.match(ln) for ln in prompt.splitlines()) if m]
        keys = []
        for qid, _day, _kind, _feel, quote in new:
            toks = {w for w in _WORD.findall(quote.lower()) if w not in _STOP}
            keys.append((qid, quote, {n.lower() for n in _names(quote)}, toks))
        parent = list(range(len(keys)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                shared_name = keys[i][2] & keys[j][2]
                shared_word = keys[i][3] & keys[j][3] - (keys[i][2] | keys[j][2])
                df = {w: sum(1 for k in keys if w in k[3]) for w in shared_word}
                nameless = not keys[i][2] and not keys[j][2]
                if shared_name or (nameless and any(c <= 3 for c in df.values())):
                    parent[find(i)] = find(j)
        groups: "dict[int, list[int]]" = {}
        for i in range(len(keys)):
            groups.setdefault(find(i), []).append(i)
        ops, touched = [], set()
        for idxs in groups.values():
            qids = [keys[i][0] for i in idxs]
            names = set().union(*[keys[i][2] for i in idxs])
            toks = set().union(*[keys[i][3] for i in idxs])
            hit = next((t for t in old if t[0] not in touched
                        and ((names and names & {n.lower() for n in _names(t[1] + " " + t[2])})
                             or (not names and len(toks & set(_WORD.findall((t[1] + " " + t[2]).lower()))) >= 2))), None)
            newest = keys[idxs[-1]][1]
            if hit:
                touched.add(hit[0])
                ops.append({"op": "update", "thread": hit[0], "moments": qids, "status": "open", "reason": "the same person or matter as the open thread"})
            else:
                ops.append({"op": "create", "title": " ".join(newest.split()[:6]), "moments": qids, "status": "open",
                            "reason": "no open thread is about this person or matter"})
        return json.dumps({"threads": ops, "unchanged": [t[0] for t in old if t[0] not in touched]})


class FakeNightServer:
    """A loopback OpenAI-compatible server around a ``FakeNightBrain`` (``/health``, ``/v1/models``, ``/v1/chat/completions``): the standalone CLI and
    the HTTP path of the pass run against it, no model. ``up=False`` serves 503 (the model-down case)."""

    def __init__(self, brain: Optional[FakeNightBrain] = None, port: int = 0):
        self.brain = brain or FakeNightBrain()
        self.requests = 0
        self.up = True
        self.last_body: "dict[str, Any]" = {}
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):  # noqa: ANN002
                return

            def _send(self, code: int, obj: Any) -> None:
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802
                outer.requests += 1
                self._send(200 if outer.up else 503, {"status": "ok"} if outer.up else {"error": "down"})

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                outer.requests += 1
                outer.last_body = body
                if not outer.up:
                    return self._send(503, {"error": "down"})
                text = outer.brain(body.get("messages") or [], int(body.get("max_tokens") or 0))
                prompt = sum(len(str(m.get("content", ""))) for m in body.get("messages") or []) // 4
                self._send(200, {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                                 "usage": {"prompt_tokens": prompt, "completion_tokens": len(text) // 4}})

        self.srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.port = self.srv.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/v1"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
