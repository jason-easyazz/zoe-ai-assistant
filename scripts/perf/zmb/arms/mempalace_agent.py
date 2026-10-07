"""Arm MPA: MemPalace set up and OPERATED the way it is designed to be - by the AGENT - with Zoe's household floors around what the brain writes.

Every earlier measurement used MemPalace passively (a store the Zoe layer writes to: arm MV / the verbatim tier of HM). MemPalace's own design is
different: an MCP-first memory the MODEL operates. The model calls ``mempalace_search`` before it answers, files verbatim words with
``mempalace_add_drawer`` into wings and rooms, keeps the temporal triple store with ``mempalace_kg_add`` / ``mempalace_kg_supersede``, writes a diary
at the end of a session, and is taught all that by a five-rule protocol its status tool returns; hooks wrap the session (start: the wake-up
status; stop: "save now" every 15 messages); an LLM closet pass indexes the drawers at idle. This arm builds exactly that around the clone brain.

    owner turn --gate(classify)--> router bypass (device command: never reaches the brain, never stored)
                       |                \\--> quarantine (unverified / pasted text: filed verbatim by the HARNESS in its own room, no brain)
                       v
              AGENT LOOP (the brain, MemPalace's tools, its protocol in the prompt)  <-- hooks: session start = status wake-up, every 15 messages = save request
                  each tool call: schema-checked, then FLOORS (ledger, identity, authority anchor, frame), then the REAL ``mempalace-mcp`` server
    idle: stop hook (rule 4) -> closet pass (``mempalace.closet_llm`` against the brain, server stopped) -> observations = closet summaries

WHAT THE BRAIN IS GIVEN. Ten of MemPalace's 45 tools (the 45-tool schema is 32 KB, four times the 8k slot): status, search, add_drawer, update_drawer,
kg_query, kg_add, kg_invalidate, kg_supersede, kg_timeline, diary_write: MemPalace's own descriptions and schemas, byte for byte (``mpa_snapshot.json``). The
transport is a FAITHFUL SHIM, not an MCP mount: Flue 2.1.1 can mount a remote MCP server (``useMcpConnection``) but a mounted tool takes ``wing`` /
``added_by`` / ``agent_name`` from the model, and Zoe's rule is that identity is bound in trusted code, never from model args
(``labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts``). So production is one ``defineTool`` per MemPalace tool calling a loopback bridge to the per-user server
(this arm's ``_exec_tool`` is that bridge); the lab drives the SAME server over stdio. The shim changes three things and nothing else: it pins identity
fields, fills ``source_file`` / dates the model left out, and renders results compactly (``_render``).

EXTRA PROMPT COST versus the H arms (which inject nothing): ``prompt_parts()`` returns the token counts: the protocol, the AAAK spec ``status`` hands out, the
memory-rules paragraph of ``shared_brain_rules.md``, the wing / room conventions, the wake-up overview, and the tool schemas. They are reported, not hidden.

THE FLOORS (Zoe layer; the same helpers as the H arms: ``hm_policy.classify`` / ``HashedLedger`` / ``alias_candidates`` / ``frame``), each a named switch so a
negative control can break ONE and the cell that claims it must go red: ``guest_gate`` and speaker classes (who reaches the brain at all), ``router_bypass``,
``tool_floor`` (a write naming a forgotten entity is refused; identity fields are pinned), ``anchor_check`` (a drawer or fact the brain files that is not the
owner's own words is labelled ``model_from_transcript``, rank 0, and can neither retire nor overwrite a user-stated one), ``quarantine_filter`` (recall never
shows unverified / pasted rooms), ``frame`` (verbatim hits are quoted data, instruction-like clauses of non-owner text withheld), ``physical_erase`` and the
ledger on forget (drawers, triples, closets, WAL, and a palace rebuild). Nothing here is imported by the runner until ``make_arm("MPA")`` is asked for.
"""
from __future__ import annotations

import calendar
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

from .base import Arm, IngestReport, Turn
from .hm_policy import (GUEST_IDS, Controls, HashedLedger, MODEL_FROM_TRANSCRIPT, ROOM_QUOTED, ROOM_UNVERIFIED, USER_STATED, alias_candidates, classify, frame, label_for)
from .mpa_bench import DAY_S, DECLINE, RESERVED_ROOMS, SIM_TODAY, BenchSurface, _epoch  # noqa: F401
from .mpa_model import BudgetExhausted, ChatModel, Reply
from .mpa_palace import (INSTALL_HINT, Backend, McpStdioBackend, PalaceUnavailable, ToolError, est_tokens, extra_keys, kg_delete_naming, library_available,
                         load_snapshot, openai_tools, validate_args)
from .mempalace_verbatim import _REBUILD_CHILD, SHARED_EMBEDDER_MODEL_ENV, SHARED_EMBEDDER_URL_ENV, DEFAULT_SHARED_MODEL, _name_pattern, shared_embedder_env

WRITE_TOOLS = frozenset({"mempalace_add_drawer", "mempalace_update_drawer", "mempalace_kg_add", "mempalace_kg_invalidate", "mempalace_kg_supersede", "mempalace_diary_write"})
RECALL_TOOLS = frozenset({"mempalace_search", "mempalace_kg_query", "mempalace_kg_timeline"})
DEMO_USER_RE = re.compile(r"^demo_bar_[0-9a-f]{8}$")

#: the household's device / chatter turns (needles.chatter, the router's job): Zoe's router answers these and they never reach the brain (measured on the live
#: house: about 64% of turns never reach a brain). A regex stand-in for the FunctionGemma router; ``router_bypass`` OFF sends every turn to the brain.
_COMMAND = re.compile(
    r"^\s*(?:turn (?:on|off|the)\b|set (?:a |an )?(?:timer|alarm)\b|pause\b|skip\b|stop (?:the )?timer\b|play\b|dim\b|what'?s the weather\b|what time\b|add .+ to (?:the |my )?shopping list"
    r"|how long until\b|what'?s on my calendar\b|is it going to rain\b|read me the headlines\b|how many\b|can you turn\b|remind me to\b|what'?s \d+ times\b|good morning\b"
    r"|thanks that'?s all\b)", re.I)


@dataclass
class MpaControls:
    """The MPA-specific floors (the shared ones live in ``hm_policy.Controls``). ``MpaControls(anchor_check=False)`` is a negative-control arm."""
    router_bypass: bool = True         # device / chatter turns are answered by the router: they never reach the brain or the palace
    tool_floor: bool = True            # write tools: a forgotten entity is refused, identity fields are pinned, reserved rooms are refused
    anchor_check: bool = True          # what the brain files is labelled by whether it is the owner's own words; a model-class write never retires a user-stated one
    quarantine_filter: bool = True     # search results never include the unverified / quoted rooms
    hooks: bool = True                 # session start = the wake-up status, stop = the save request every ``save_interval`` messages, end = the checkpoint

    @classmethod
    def names(cls) -> "tuple[str, ...]":
        return tuple(f.name for f in fields(cls))

    def off(self, *names: str) -> "MpaControls":
        out = MpaControls(**{f.name: getattr(self, f.name) for f in fields(self)})
        for n in names:
            if n not in self.names():
                raise ValueError(f"unknown MPA control {n!r} (known: {', '.join(self.names())})")
            setattr(out, n, False)
        return out


@dataclass
class Prov:
    """What the harness knows about a drawer / fact the brain filed (the model never sets any of it)."""
    authority_class: str
    speaker: str = ""
    day_offset: int = 0
    turn_id: str = ""
    excerpt: str = ""
    wing: str = ""
    room: str = ""
    anchored: bool = True
    writer: str = "brain"
    seq: int = 0


@dataclass
class ToolRecord:
    name: str
    valid: bool
    errors: "list[str]" = field(default_factory=list)
    extra: "list[str]" = field(default_factory=list)
    refused: str = ""                 # a floor's reason
    ok: bool = True                   # the server answered success
    ms: float = 0.0


@dataclass
class TurnTrace:
    """One owner turn through the agent loop: what the brain did (the numbers the brain-tier cells score)."""
    text: str
    day_offset: int = 0
    model_calls: int = 0
    prompt_tokens: int = 0
    seconds: float = 0.0
    tools: "list[ToolRecord]" = field(default_factory=list)
    reply: str = ""
    searched_before_answer: bool = False
    filter_misses: int = 0            # a wing/room-filtered search returned nothing where the unfiltered one (harness diagnostics, no model) would have
    supersedes: "list[tuple[str, str, str, str]]" = field(default_factory=list)
    checkpoint: bool = False
    seen_text: "list[str]" = field(default_factory=list)       # what the brain was shown by recall tools this turn (the agent's own packet)
    truncated: bool = False


def sim_date(day_offset: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(SIM_TODAY - day_offset * DAY_S))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _is_command(text: str) -> bool:
    return bool(_COMMAND.match(text or ""))


class MemPalaceAgentArm(BenchSurface, Arm):
    name = "MPA"
    capabilities = frozenset({"clock", "identities", "idle_pass", "verbatim", "reader", "exact_words", "multi_hop", "protocol", "observations"})

    def __init__(self, *, model: "Optional[ChatModel]" = None, backend_factory=None, controls: "Optional[Controls]" = None, mpa: "Optional[MpaControls]" = None,
                 ledger: "Optional[HashedLedger]" = None, work_dir: "Optional[str | Path]" = None, aaak: bool = True, save_interval: "Optional[int]" = None,
                 max_tool_iters: int = 4, history_exchanges: int = 3, max_prompt_tokens: int = 6500, wing_mode: str = "free", closet_url: str = "", tool_names: "Optional[tuple[str, ...]]" = None,
                 protocol_rules: "Optional[tuple[int, ...]]" = None, rules_paragraph: bool = True,
                 closet_model: str = "local", closet_scripted: bool = False, embed_url: str = "", tokenizer=None, max_tokens: int = 400):
        if wing_mode not in ("free", "pinned"):
            raise ValueError("wing_mode must be free (the brain files into the wings it chooses: the design) or pinned (ablation: the shim pins the account's wing)")
        self.snap = load_snapshot()
        self.model, self.controls, self.mc = model, controls or Controls(), mpa or MpaControls()
        self.ledger = ledger if ledger is not None else HashedLedger()
        self._backend_factory, self._work_dir, self.aaak = backend_factory, work_dir, aaak
        self.save_interval = int(save_interval or self.snap["save_interval"])
        self.max_tool_iters, self.history_exchanges, self.max_prompt_tokens, self.max_tokens = max_tool_iters, history_exchanges, max_prompt_tokens, max_tokens
        self.wing_mode, self.closet_url, self.closet_model, self.closet_scripted = wing_mode, closet_url.rstrip("/"), closet_model, closet_scripted
        self.embed_url, self.tokenizer = embed_url or os.environ.get(SHARED_EMBEDDER_URL_ENV, ""), tokenizer
        self.tool_names = tuple(tool_names or self.snap["tools"])
        bad = [n for n in self.tool_names if n not in self.snap["tools"]]
        if bad:
            raise ValueError(f"not an agent tool: {', '.join(bad)} (offered: {', '.join(self.snap['tools'])})")
        self.tool_specs = openai_tools(self.snap, list(self.tool_names))
        self.protocol_rules, self.rules_paragraph = protocol_rules, rules_paragraph
        self.backend: "Optional[Backend]" = None
        self._tmp: "Optional[tempfile.TemporaryDirectory]" = None
        self._user, self._account, self._clock = "", "user", 0.0
        self._prov: "dict[str, Prov]" = {}
        self._kg_prov: "dict[tuple[str, str, str], Prov]" = {}
        self._kg_links: "dict[tuple[str, str, str], tuple[str, str, str]]" = {}
        self._side: "list[dict[str, Any]]" = []              # rows the Zoe layer held back (disputed proposals): never in the palace
        self._seq = 0
        self._session: "Optional[dict[str, Any]]" = None
        self.traces: "list[TurnTrace]" = []
        self.writes_refused = 0
        self.routed = 0
        self.hook_calls = 0
        self.checkpoints = 0
        self.closet_stats: "dict[str, Any]" = {}
        self._closets_dirty = False
        self.model_calls = 0
        self.model_seconds = 0.0
        self.prompt_tokens_max = 0
        # extension points the combined arm (HMA) plugs into; all default to nothing, so MPA alone is exactly the agent-operated MemPalace
        self.extra_protocol = ""                        # one more paragraph of the SAME prompt protocol
        self.search_augment = None                      # (query, hits) -> [{text, ...}]: reflections riding in the search result (one tool, one packet)
        self.on_write = None                            # (event dict) -> None: every drawer the arm's writers file (the second tier's one ingest path)
        self.last_forgotten_ids: "list[str]" = []
        self.turn_context = None                        # (text) -> str: what Zoe already knows, appended to the owner's message (ZMA's recall packet)
        self.checkpoint_enabled = True                  # off where the harness, not the brain, files the words (ZMA)

    # ── lifecycle ─────────────────────────────────────────────────────────────────────────────────────────────────
    def _open_backend(self) -> "Backend":
        if self._backend_factory is not None:
            return self._backend_factory(self._root / "palace")
        if not library_available():
            raise NotImplementedError(f"arm MPA: {INSTALL_HINT}")
        env = shared_embedder_env(self.embed_url, os.environ.get(SHARED_EMBEDDER_MODEL_ENV, "") or DEFAULT_SHARED_MODEL) if self.embed_url else {}
        try:
            return McpStdioBackend(self._root / "palace", embed_env=env)
        except PalaceUnavailable as exc:
            raise NotImplementedError(f"arm MPA: {exc}") from None

    def reset(self, user_id: str, **_kw: Any) -> None:
        if not DEMO_USER_RE.match(user_id or ""):
            raise ValueError(f"refusing non-demo identity {user_id!r} (must match {DEMO_USER_RE.pattern})")
        self.close()
        if self._work_dir is None:
            self._tmp = tempfile.TemporaryDirectory(prefix="zmb-mpa-")
            self._root = Path(self._tmp.name)
        else:
            self._root = Path(self._work_dir)
            shutil.rmtree(self._root, ignore_errors=True)
            self._root.mkdir(parents=True, exist_ok=True)
        self.backend = self._open_backend()
        self._user, self._clock, self._seq = user_id, 0.0, 0
        self._prov, self._kg_prov, self._kg_links, self._side, self.traces = {}, {}, {}, [], []
        self._session, self._closets_dirty, self.closet_stats = None, False, {}
        self.ledger = HashedLedger()
        self.writes_refused = self.routed = self.hook_calls = self.checkpoints = self.model_calls = 0
        self.model_seconds, self.prompt_tokens_max = 0.0, 0

    def close(self) -> None:
        if self.backend is not None:
            self.backend.close()
            self.backend = None
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None

    def advance_clock(self, seconds: float) -> None:
        self._clock += float(seconds)

    def set_account_name(self, name: str) -> None:
        """The display name of the ACCOUNT (identity comes from the account, never from the text). It becomes the owner's own wing."""
        self._account = re.sub(r"[^a-z0-9]+", "", (name or "user").lower()) or "user"

    def _need(self) -> "Backend":
        if self.backend is None:
            raise RuntimeError("call reset(user_id) first")
        return self.backend

    # ── the prompt: what the protocol costs ───────────────────────────────────────────────────────────────────────
    def _overview(self) -> str:
        """The wake-up ``status`` the session-start hook makes (a harness call, not a model call)."""
        self.hook_calls += 1
        try:
            st = self._need().call("mempalace_status", {})
        except ToolError as exc:
            return f"(status unavailable: {exc})"
        if "error" in st or not st.get("total_drawers"):
            return "The palace is empty: nothing has been filed yet."
        wings = ", ".join(f"{w} ({n})" for w, n in sorted((st.get("wings") or {}).items())[:12])
        rooms = ", ".join(sorted((st.get("rooms") or {}))[:20])
        return f"{st['total_drawers']} drawers. Wings: {wings}. Rooms: {rooms}."

    def prompt_parts(self, overview: str = "The palace is empty: nothing has been filed yet.", date: str = "2026-10-07") -> "dict[str, str]":
        """The pieces of the system prompt, by name (so the report can price each one). The H arms inject none of this."""
        s = self.snap
        wing = self._account
        proto = s["palace_protocol"]
        if self.protocol_rules:                          # a subset of the five rules (ZMA keeps 1-3: the tools of rules 4-5 are not offered)
            lines = proto.split("\n")
            keep = [ln for ln in lines if not re.match(r"^\d\.", ln) or int(ln[0]) in self.protocol_rules]
            proto = "\n".join(keep)
        return {
            "persona": f"You are Zoe, a warm, brief household voice assistant. Today is {date}. You are talking with a member of the household.",
            "role": "You operate that person's private MemPalace with the tools provided. What you do not file, you will not remember next time.",
            "protocol": proto,
            "aaak_spec": ("AAAK dialect (from mempalace_status):\n" + s["aaak_spec"]) if self.aaak else "",
            "memory_rules": s["memory_rules"] if self.rules_paragraph else "",
            "reflections": self.extra_protocol,
            "conventions": (f'Wing = who or what a memory is about: this person\'s own wing is "{wing}"; a relative or friend gets a wing of their lowercase first name. '
                            "Room = one short topic word (health, family, home, work, calendar, preferences). If unsure of a wing or room, search without filters. "
                            "File the person's own words verbatim; do not file device commands or small talk."),
            "overview": "Palace overview (mempalace_status at session start): " + overview,
        }

    def system_prompt(self, overview: str, date: str) -> str:
        return "\n\n".join(v for v in self.prompt_parts(overview, date).values() if v)


    # ── the gate and the router ───────────────────────────────────────────────────────────────────────────────────
    def decide(self, turn: Turn, user: str):
        return classify(turn.speaker, user, self.controls)

    def ingest(self, turns: "list[Turn]") -> IngestReport:
        return self._ingest_for(self._user, turns)

    def ingest_as(self, identity: str, turns: "list[Turn]") -> IngestReport:
        from .mempalace_verbatim import MemPalaceVerbatimArm
        return self._ingest_for(MemPalaceVerbatimArm._identity_user(identity), turns)

    def _refuse_cross_account(self, user: str) -> None:
        """One palace per account (one backend, one wing): a turn that WOULD be stored for another account cannot be filed here - it would land in this account's palace and be
        absent from the target's. A control-refusal (the cell SKIPs with the reason), never a silent cross-write."""
        if (user or "") != self._user and (user or "").strip().lower() not in GUEST_IDS:      # a guest owns no palace: with the guest gate OFF its words landing HERE is the control's red path
            raise NotImplementedError(f"arm {self.name}: one palace per account - a turn stored for {user!r} cannot be filed in {self._user!r}'s palace (cross-account ingestion is not supported)")

    def _ingest_for(self, user: str, turns: "list[Turn]") -> IngestReport:
        rep = IngestReport()
        prev_day: "Optional[int]" = None
        for t in turns:
            rep.turns += 1
            if t.speaker == "system_writer":                      # a model pass proposes: filed by the harness as an INFERRED drawer, authority-gated, no brain
                if self.controls.guest_gate and (user or "").strip().lower() in ("", "guest", "anonymous", "voice-guest", "voice-daemon"):
                    rep.refused += len(t.proposes) or 1
                    rep.notes.append("guest / unknown principal: owns no memory")
                    continue
                self._refuse_cross_account(user)
                for p in t.proposes:
                    ok = self._file_proposal(user, p, t)
                    rep.written += int(ok)
                    rep.refused += int(not ok)
                continue
            d = self.decide(t, user)
            if not d.store:
                rep.refused += 1
                rep.notes.append(d.reason)
                continue
            self._refuse_cross_account(user)
            if self.controls.ledger_write_check and self.ledger.matches(user, t.text):
                if t.speaker == "owner_taught" and d.authority_class == USER_STATED:
                    self.ledger.release_text(user, t.text)       # an explicit re-teach by the verified person
                else:
                    rep.refused += 1
                    rep.notes.append("names a forgotten entity")
                    continue
            if d.room in (ROOM_UNVERIFIED, ROOM_QUOTED):         # quarantine: the harness files the words verbatim in a reserved room; the brain never sees them
                self._file_quarantine(user, t, d)
                rep.written += 1
                continue
            if self.mc.router_bypass and t.speaker != "owner_taught" and _is_command(t.text):
                self.routed += 1
                continue
            if prev_day is not None and t.day_offset != prev_day:
                self._end_session()
            prev_day = t.day_offset
            tr = self._agent_turn(t.text, t.day_offset, t.speaker)
            rep.written += sum(1 for r in tr.tools if r.name in WRITE_TOOLS and r.ok and not r.refused)
            rep.refused += sum(1 for r in tr.tools if r.refused)
        self.writes_refused += rep.refused
        return rep

    def converse(self, text: str, day_offset: int = 0) -> TurnTrace:
        """One brain turn through the agent loop (the brain-tier cells and the smoke call this)."""
        return self._agent_turn(text, day_offset, "owner_typed")

    # ── sessions and hooks ────────────────────────────────────────────────────────────────────────────────────────
    def _open_session(self, day_offset: int) -> "dict[str, Any]":
        date = sim_date(day_offset)
        overview = self._overview() if self.mc.hooks else "(no wake-up: the session-start hook is off)"
        self._session = {"day": day_offset, "date": date, "system": self.system_prompt(overview, date), "groups": [], "owner": [], "since_save": 0}
        return self._session

    def _end_session(self) -> None:
        """The session-end hook (protocol rule 4): one save request if anything was said since the last one."""
        s = self._session
        if s is None:
            return
        if self.mc.hooks and self.checkpoint_enabled and s["since_save"] > 0 and self.model is not None:
            self._checkpoint()
        self._session = None

    def _checkpoint(self) -> None:
        s = self._session
        if s is None:
            return
        self.checkpoints += 1
        s["since_save"] = 0
        self._agent_turn(self.snap["stop_block_reason"], s["day"], "hook", checkpoint=True)

    # ── the agent loop ────────────────────────────────────────────────────────────────────────────────────────────
    def _history(self, s: "dict[str, Any]") -> "list[dict[str, Any]]":
        groups = s["groups"][-self.history_exchanges:] if self.history_exchanges else []
        while groups and est_tokens(json.dumps([m for g in groups for m in g])) + est_tokens(s["system"]) + est_tokens(json.dumps(self.tool_specs)) > self.max_prompt_tokens:
            groups = groups[1:]
        return [m for g in groups for m in g]

    def _agent_turn(self, text: str, day_offset: int, speaker: str, checkpoint: bool = False) -> TurnTrace:
        if self.model is None:
            raise NotImplementedError("arm MPA: no brain is attached (the agent-operated arm needs the clone brain or the scripted stand-in); pass model=")
        s = self._session if (self._session and self._session["day"] == day_offset) else None
        if s is None:
            self._end_session()
            s = self._open_session(day_offset)
        tr = TurnTrace(text=text, day_offset=day_offset, checkpoint=checkpoint)
        ctx = self.turn_context(text) if (self.turn_context is not None and not checkpoint) else ""
        group: "list[dict[str, Any]]" = [{"role": "user", "content": text + (f"\n\n[What Zoe already knows, authoritative]\n{ctx}" if ctx else "")}]
        if not checkpoint:
            s["owner"].append(text)
            s["since_save"] += 1
        called_recall = False
        for it in range(self.max_tool_iters + 1):
            msgs = [{"role": "system", "content": s["system"]}] + self._history(s) + group
            use_tools = self.tool_specs if it < self.max_tool_iters else []
            try:
                reply: Reply = self.model.complete(msgs, use_tools, max_tokens=self.max_tokens)
            except BudgetExhausted:
                self.traces.append(tr)
                raise
            tr.model_calls += 1
            tr.seconds += reply.seconds
            tr.prompt_tokens = max(tr.prompt_tokens, reply.prompt_tokens)
            self.model_calls += 1
            self.model_seconds += reply.seconds
            self.prompt_tokens_max = max(self.prompt_tokens_max, reply.prompt_tokens)
            if reply.tool_calls and use_tools:
                group.append({"role": "assistant", "content": reply.content or None,
                              "tool_calls": [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["raw"] if c["raw"] else "{}"}} for c in reply.tool_calls]})
                for c in reply.tool_calls:
                    out = self._exec_tool(c, s, tr, speaker, day_offset)
                    called_recall = called_recall or c["name"] in RECALL_TOOLS
                    group.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"], "content": out})
                continue
            tr.reply = reply.content.strip()
            group.append({"role": "assistant", "content": tr.reply})
            break
        else:
            tr.truncated = True
        tr.searched_before_answer = called_recall
        s["groups"].append(group)
        self.traces.append(tr)
        if (not checkpoint and self.mc.hooks and self.checkpoint_enabled and s["since_save"] >= self.save_interval):
            self._checkpoint()
        return tr

    # ── one tool call: validity, floors, the real server, the rendering ───────────────────────────────────────────
    def _exec_tool(self, call: "dict[str, Any]", s: "dict[str, Any]", tr: TurnTrace, speaker: str, day_offset: int) -> str:
        name, args = call["name"], call["arguments"]
        spec = self.snap["tools"].get(name) if name in self.tool_names else None
        rec = ToolRecord(name=name, valid=False)
        tr.tools.append(rec)
        if spec is None:
            rec.errors = [f"unknown tool {name!r}"]
            return json.dumps({"error": f"unknown tool {name!r}; use one of: {', '.join(self.tool_names)}"})
        if call.get("args_error") or args is None:
            rec.errors = [call.get("args_error") or "no arguments"]
            return json.dumps({"error": f"invalid arguments: {rec.errors[0]}"})
        rec.errors = validate_args(spec["input_schema"], args)
        rec.extra = extra_keys(spec["input_schema"], args)
        if name == "mempalace_diary_write" and not (args.get("entry") or args.get("content")):
            rec.errors.append("entry: required (or its alias content)")
        rec.valid = not rec.errors
        if not rec.valid:
            return json.dumps({"error": "invalid arguments: " + "; ".join(rec.errors[:3])})
        args = dict(args)
        if name in WRITE_TOOLS and self.mc.tool_floor:
            why = self._floor(name, args, s)
            if why:
                rec.refused = why
                return json.dumps({"success": False, "error": why})
        args = self._pin(name, args, s)
        t0 = time.monotonic()
        try:
            res = self._need().call(name, args)
        except ToolError as exc:
            rec.ok, rec.ms = False, (time.monotonic() - t0) * 1000.0
            return json.dumps({"error": f"tool failed: {exc}"})
        rec.ms = (time.monotonic() - t0) * 1000.0
        rec.ok = bool(res.get("success", "error" not in res))
        if rec.ok and name in WRITE_TOOLS:
            self._record_write(name, args, res, s, speaker, day_offset, tr)
        return self._render(name, args, res, tr)

    def _floor(self, name: str, args: "dict[str, Any]", s: "dict[str, Any]") -> str:
        blob = " ".join(str(v) for v in args.values() if isinstance(v, (str, int, float)))
        if self.controls.ledger_write_check and self.ledger.matches(self._user, blob):
            return "that names something you asked me to forget; it was not stored"
        if name == "mempalace_add_drawer" and str(args.get("room", "")).lower() in RESERVED_ROOMS:
            return f"the room {args.get('room')!r} is reserved"
        if name == "mempalace_update_drawer":
            # The drawer is changed IN PLACE by the real server: every check runs before the call (``_record_write`` only relabels what is already gone). Two ways to lose or
            # forge an owner's memory: replace its words, or MOVE it (a drawer in quoted / unverified is filtered out of recall; a quoted one moved out becomes recallable as the owner's).
            pv = self._prov.get(str(args.get("drawer_id", "")))
            moved = {k: str(args[k]) for k in ("room", "wing") if args.get(k) not in (None, "")}
            if str(moved.get("room", "")).lower() in RESERVED_ROOMS:
                return f"the room {moved['room']!r} is reserved"
            if pv is not None and ((pv.room in RESERVED_ROOMS and moved.get("room", pv.room) != pv.room) or (self.mc.anchor_check and pv.authority_class == USER_STATED
                                                                                                          and (moved.get("room", pv.room) != pv.room or moved.get("wing", pv.wing) != pv.wing))):
                return "a drawer's place says who said it: a drawer is not moved between rooms or wings by the model"
            if self.mc.anchor_check and args.get("content") and pv is not None and pv.authority_class == USER_STATED and not self._anchored(str(args["content"]), s):
                return "a drawer holding what the owner said is only rewritten with the owner's own words"
        if self.mc.anchor_check and name == "mempalace_kg_add" and not self._anchored_claim(str(args.get("subject", "")), str(args.get("object", "")), s):
            subj, pred, obj = (str(args.get(k, "")).lower() for k in ("subject", "predicate", "object"))
            for t in self._kg_dump():
                other = str(t["object"]).lower()
                pv = self._kg_prov.get((subj, pred, other))
                if str(t["subject"]).lower() == subj and str(t["predicate"]).lower() == pred and other != obj and not t.get("valid_to") and pv is not None and pv.authority_class == USER_STATED:
                    # held back, never filed: a second value beside the owner's is a rival belief the model made up; only kg_supersede with the owner's words changes the owner's
                    self._side.append({"text": f"{args.get('subject')} {str(args.get('predicate')).replace('_', ' ')} {args.get('object')}", "status": "disputed",
                                       "contradicts_id": f"kg:{t['subject']}|{t['predicate']}|{t['object']}".lower()})
                    return "that conflicts with something the owner stated; it was held back (retire the old value with kg_supersede and the owner's words)"
        if self.mc.anchor_check and name in ("mempalace_kg_supersede", "mempalace_kg_invalidate"):
            old = args.get("old_object") if name == "mempalace_kg_supersede" else args.get("object")
            pv = self._kg_prov.get((str(args.get("subject", "")).lower(), str(args.get("predicate", "")).lower(), str(old).lower()))
            backing = args.get("new_object") if name == "mempalace_kg_supersede" else args.get("object")
            if pv is not None and pv.authority_class == USER_STATED and not self._anchored_claim(str(args.get("subject", "")), str(backing), s):
                return "a fact the owner stated is only retired by something the owner said"
        return ""

    def _pin(self, name: str, args: "dict[str, Any]", s: "dict[str, Any]") -> "dict[str, Any]":
        """Identity fields are bound in trusted code, never taken from the model; dates the model left out are the simulated session date."""
        if not self.mc.tool_floor:
            return args
        date = s["date"]
        if name == "mempalace_add_drawer":
            args["added_by"] = f"zoe:{self._account}"
            args.setdefault("source_file", f"session:{date}")
            if self.wing_mode == "pinned":
                args["wing"] = self._account
        elif name == "mempalace_diary_write":
            args["agent_name"] = "zoe"
        elif name == "mempalace_kg_add":
            args.setdefault("valid_from", date)
        elif name == "mempalace_kg_supersede":
            args.setdefault("at", date)
        elif name == "mempalace_kg_invalidate":
            args.setdefault("ended", date)
        elif name == "mempalace_search":
            args["limit"] = min(int(args.get("limit") or 5), 10)
            if self.wing_mode == "pinned":
                args.pop("wing", None)
        return args

    def _emit(self, kind: str, did: str, text: str, cls: str, day: int, wing: str, room: str) -> None:
        if self.on_write is not None:
            self.on_write({"kind": kind, "id": did, "text": text, "class": cls, "day_offset": day, "wing": wing, "room": room, "user": self._user})

    def _anchored(self, text: str, s: "dict[str, Any]") -> bool:
        """Is ``text`` the owner's own words (a substring of what they said this session)? Everything else the brain files is its own composition."""
        t = _norm(text)
        return bool(t) and any(t in _norm(o) for o in s["owner"])

    _OWNER_SUBJECTS = frozenset({"", "user", "owner", "me", "i", "myself"})

    def _anchored_claim(self, subject: str, obj: str, s: "dict[str, Any]") -> bool:
        """Is the CLAIM the owner's, not just its object token? The object and the subject (the owner's own words about themselves need no name) must appear in ONE owner
        utterance as whole words: an unrelated sentence that mentions "Oslo" does not make ``Tove lives_in Oslo`` the owner's statement."""
        o, sub = f" {_norm(obj)} ", _norm(subject)
        if o.strip() == "":
            return False
        for u in s["owner"]:
            n = f" {_norm(u)} "
            if o in n and (sub in self._OWNER_SUBJECTS or f" {sub} " in n):
                return True
        return False

    def _klass_claim(self, subject: str, obj: str, s: "dict[str, Any]") -> "tuple[str, bool]":
        if not self.mc.anchor_check:
            return USER_STATED, True
        ok = self._anchored_claim(subject, obj, s)
        return (USER_STATED if ok else MODEL_FROM_TRANSCRIPT), ok

    def _klass(self, text: str, s: "dict[str, Any]") -> "tuple[str, bool]":
        if not self.mc.anchor_check:
            return USER_STATED, True
        ok = self._anchored(text, s)
        return (USER_STATED if ok else MODEL_FROM_TRANSCRIPT), ok

    def _record_write(self, name: str, args: "dict[str, Any]", res: "dict[str, Any]", s: "dict[str, Any]", speaker: str, day_offset: int, tr: TurnTrace) -> None:
        self._seq += 1
        if name in ("mempalace_add_drawer", "mempalace_diary_write"):
            did = str(res.get("drawer_id") or res.get("entry_id") or "")
            text = str(args.get("content") or args.get("entry") or "")
            cls, anchored = self._klass(text, s) if name == "mempalace_add_drawer" else (MODEL_FROM_TRANSCRIPT, False)
            self._prov[did] = Prov(cls, speaker, day_offset, f"t{self._seq}", text[:300], str(args.get("wing", "")), str(args.get("room", "diary")), anchored, "brain", self._seq)
            if name == "mempalace_add_drawer":
                self._emit("write", did, text, cls, day_offset, str(args.get("wing", "")), str(args.get("room", "")))
        elif name == "mempalace_update_drawer" and str(args.get("drawer_id")) in self._prov and args.get("content"):
            p = self._prov[str(args["drawer_id"])]
            p.authority_class, p.anchored = self._klass(str(args["content"]), s)
            p.excerpt = str(args["content"])[:300]
            self._emit("write", str(args["drawer_id"]), str(args["content"]), p.authority_class, p.day_offset, p.wing, p.room)
        elif name in ("mempalace_kg_add", "mempalace_kg_supersede"):
            new = str(args.get("object") if name == "mempalace_kg_add" else args.get("new_object"))
            key = (str(args["subject"]).lower(), str(args["predicate"]).lower(), new.lower())
            cls, anchored = self._klass_claim(str(args["subject"]), new, s)
            self._kg_prov[key] = Prov(cls, speaker, day_offset, f"t{self._seq}", new, anchored=anchored, seq=self._seq)
            if name == "mempalace_kg_supersede":
                old = (str(args["subject"]).lower(), str(args["predicate"]).lower(), str(args["old_object"]).lower())
                self._kg_links[key] = old
                tr.supersedes.append((str(args["subject"]), str(args["predicate"]), str(args["old_object"]), new))

    def _render(self, name: str, args: "dict[str, Any]", res: "dict[str, Any]", tr: TurnTrace) -> str:
        """The result the brain reads. search: the hits only (text, wing, room, date, similarity), quarantine rooms removed, text framed as quoted data."""
        if name == "mempalace_search":
            hits = [h for h in res.get("results") or []]
            if self.mc.quarantine_filter:
                hits = [h for h in hits if h.get("room") not in RESERVED_ROOMS]
            want = int(args.get("limit") or 5)
            if (args.get("wing") or args.get("room")) and not hits:
                try:
                    free = self._need().call("mempalace_search", {"query": args.get("query", ""), "limit": want})
                    if [h for h in free.get("results") or [] if h.get("room") not in RESERVED_ROOMS]:
                        tr.filter_misses += 1
                except ToolError:
                    pass
            out = []
            for h in hits[:want]:
                p = self._prov.get(str(h.get("drawer_id")))
                cls = p.authority_class if p else USER_STATED
                text = str(h.get("text", ""))
                tr.seen_text.append(text)
                shown = frame(text, authority_class=cls, speaker_label=label_for(cls), date=str(h.get("filed_at", ""))[:10], enabled=self.controls.frame)
                out.append({"text": shown, "wing": h.get("wing"), "room": h.get("room"), "date": str(h.get("filed_at", ""))[:10], "similarity": h.get("similarity")})
            body: "dict[str, Any]" = {"results": out, "count": len(out)}
            if self.search_augment is not None:
                try:
                    extra = self.search_augment(str(args.get("query", "")), hits[:want])
                except Exception:                                    # noqa: BLE001 - one tier failing degrades the packet, it does not fail the turn
                    extra = []
                if extra:
                    body["reflections"] = extra
                    tr.seen_text += [str(x.get("text", "")) for x in extra]
            return json.dumps(body, ensure_ascii=False)
        if name in ("mempalace_kg_query", "mempalace_kg_timeline"):
            facts = res.get("active_facts") if name == "mempalace_kg_query" else res.get("timeline")
            slim = [{"fact": f"{f.get('subject')} {f.get('predicate')} {f.get('object')}", "from": f.get("valid_from"), "to": f.get("valid_to"), "current": f.get("current", f.get("valid_to") is None)}
                    for f in (facts or [])[:20]]
            if name == "mempalace_kg_query":
                slim += [{"fact": f"{f.get('subject')} {f.get('predicate')} {f.get('object')}", "from": f.get("valid_from"), "to": f.get("valid_to"), "current": False}
                         for f in (res.get("historical_facts") or [])[:10]]
            tr.seen_text += [x["fact"] for x in slim]
            return json.dumps({"entity": res.get("entity"), "facts": slim}, ensure_ascii=False)
        keep = {k: v for k, v in res.items() if k in ("success", "error", "drawer_id", "triple_id", "fact", "superseded", "reason", "entry_id", "total_drawers", "wings", "rooms", "ended")}
        return json.dumps(keep or res, ensure_ascii=False)[:1500]

    # ── harness-side writes (no brain) ────────────────────────────────────────────────────────────────────────────
    def _file_quarantine(self, user: str, t: Turn, d) -> None:
        self._seq += 1
        res = self._need().call("mempalace_add_drawer", {"wing": self._account, "room": d.room, "content": t.text, "added_by": f"zoe:{self._account}",
                                                         "source_file": f"quarantine:{sim_date(t.day_offset)}"})
        did = str(res.get("drawer_id") or "")
        self._prov[did] = Prov(d.authority_class, t.speaker, t.day_offset, f"t{self._seq}", t.text[:300], self._account, d.room, True, "harness", self._seq)

    def _attr_clash(self, user: str, text: str) -> "Optional[str]":
        from .hm import attr_of
        a = attr_of(text)
        if not a:
            return None
        for did, p in self._prov.items():
            if p.authority_class == USER_STATED and p.writer == "brain":
                b = attr_of(p.excerpt)
                if b and b[0] == a[0] and b[1] != a[1]:
                    return did
        return None

    def _file_proposal(self, user: str, text: str, t: Turn) -> bool:
        """A model pass's proposed fact: filed by the HARNESS as an ``inferred`` drawer (rank 0), unless it names a forgotten entity or contradicts a user-stated drawer."""
        if self.controls.distiller_skip and self.ledger.matches(user, text):
            return False
        clash = self._attr_clash(user, text) if self.controls.authority else None
        if clash:
            self._side.append({"text": text, "status": "disputed", "contradicts_id": clash})
            return False
        self._seq += 1
        res = self._need().call("mempalace_add_drawer", {"wing": self._account, "room": "inferred", "content": text, "added_by": f"zoe:{self._account}",
                                                         "source_file": f"inferred:{sim_date(t.day_offset)}"})
        did = str(res.get("drawer_id") or "")
        self._prov[did] = Prov(MODEL_FROM_TRANSCRIPT, "system_writer", t.day_offset, f"t{self._seq}", text[:300], self._account, "inferred", False, t.writer or "digest", self._seq)
        if res.get("success"):
            self._emit("write", did, text, MODEL_FROM_TRANSCRIPT, t.day_offset, self._account, "inferred")
        return bool(res.get("success"))














    # ── idle: the stop hook, the closet pass ──────────────────────────────────────────────────────────────────────
    def run_idle_pass(self, transcript: str, proposes: "list[str]", *, judge: bool = True) -> "dict[str, Any]":
        out = {"distilled": 0, "skipped_forgotten": 0, "proposals_dropped": 0, "proposals_unanchored": 0, "model_calls": 0, "closets": 0}
        calls0 = self.model_calls
        self._end_session()
        have_owner = any(p.authority_class == USER_STATED for p in self._prov.values())
        for prop in proposes:
            if self.controls.distiller_skip and self.ledger.matches(self._user, prop):
                out["proposals_dropped"] += 1
            elif not have_owner:
                out["proposals_unanchored"] += 1                # a model output with no owner words behind it has no provenance: not applied
            else:
                t = Turn(prop, "system_writer", writer="digest", proposes=(prop,))
                out["distilled"] += int(self._file_proposal(self._user, prop, t))
        if self.closet_url:
            out["closets"] = self.closet_pass().get("processed", 0)
        out["model_calls"] = self.model_calls - calls0
        return out

    def _venv_python(self) -> str:
        be = self._need()
        return be.python_for_children() if hasattr(be, "python_for_children") else ""

    def _child_env(self) -> "dict[str, str]":
        be = self._need()
        return be._env() if hasattr(be, "_env") else dict(os.environ)

    def closet_pass(self) -> "dict[str, Any]":
        """MemPalace's own LLM closet pass (``mempalace.closet_llm``) against the brain, run as MemPalace documents it (a CLI over the palace) with the server
        STOPPED: two processes must never write one Chroma palace. One model call per source file (one per session: ``source_file`` is ``session:<date>``)."""
        be = self._need()
        py = self._venv_python()
        if not py or not self.closet_url:
            return {"skipped": "no real palace or no closet endpoint"}
        be.stop()
        t0 = time.monotonic()
        try:
            env = {**self._child_env(), "LLM_ENDPOINT": self.closet_url, "LLM_MODEL": self.closet_model, "LLM_KEY": ""}
            r = subprocess.run([py, "-m", "mempalace.closet_llm", "--palace", str(be.path), "--endpoint", self.closet_url, "--model", self.closet_model],
                               capture_output=True, text=True, timeout=1800, env=env)
        finally:
            be.start()
        m = re.search(r"Done\. (\d+) regenerated, (\d+) failed", r.stdout or "")
        tok = re.search(r"Tokens: ([\d,]+) in \+ ([\d,]+) out", r.stdout or "")
        self.closet_stats = {"processed": int(m.group(1)) if m else 0, "failed": int(m.group(2)) if m else 0, "seconds": round(time.monotonic() - t0, 1), "rc": r.returncode,
                             "tokens_in": int(tok.group(1).replace(",", "")) if tok else 0, "tokens_out": int(tok.group(2).replace(",", "")) if tok else 0,
                             "own_model": not self.closet_scripted, "tail": (r.stdout or r.stderr or "")[-300:]}
        self.model_calls += self.closet_stats["processed"] + self.closet_stats["failed"]
        self._closets_dirty = False
        return self.closet_stats




    # ── forgetting: the ledger, then every place the brain's words can be ─────────────────────────────────────────
    def forget(self, entity: str) -> str:
        user = self._user
        self.ledger.add(user, entity)
        n_d = n_k = 0
        self.last_forgotten_ids = []
        if self.controls.forget_verbatim:
            pat = _name_pattern(entity)
            for d in self._drawers():
                if pat.search(d["text"]) or self.ledger.matches(user, d["text"]):
                    self._need().call("mempalace_delete_drawer", {"drawer_id": d["id"]})
                    self._prov.pop(d["id"], None)
                    self.last_forgotten_ids.append(d["id"])
                    n_d += 1
            hit = lambda txt: bool(pat.search(txt)) or self.ledger.matches(user, txt)           # noqa: E731
            n_k = self._erase_kg(hit)
        if self.controls.forget_verbatim and self.controls.physical_erase:
            self._physical_erase(lambda txt: bool(_name_pattern(entity).search(txt)) or self.ledger.matches(user, txt))
        self._side = [s for s in self._side if not self.ledger.matches(user, s["text"])]
        self._closets_dirty = True
        return f"forgotten: {n_d} drawer(s), {n_k} temporal fact(s); the ledger holds only a hash"

    def _erase_kg(self, hit) -> int:
        be = self._need()
        if be.kind == "mempalace-double":
            before = len(be.triples)
            be.triples = [t for t in be.triples if not hit(f"{t['subject']} {t['predicate']} {t['object']}")]
            return before - len(be.triples)
        be.stop()
        try:
            return kg_delete_naming(be.path, hit)
        finally:
            be.start()

    def _physical_erase(self, hit) -> None:
        """What an API delete leaves behind: the text in chroma.sqlite3's free pages / FTS / queue, the closet lines, the write-ahead log. Rebuild the palace from its
        surviving drawers in a CLEAN child process (closets dropped: they are regenerated at the next idle pass) and scrub the WAL."""
        be = self._need()
        if be.kind == "mempalace-double":
            return
        py = self._venv_python()
        be.stop()
        try:
            for f in (Path(be.root) / "cfg").rglob("*.jsonl"):
                keep = [ln for ln in f.read_text(errors="replace").splitlines() if not hit(ln)]
                f.write_text("\n".join(keep) + ("\n" if keep else ""))
            old = be.path
            new = be.root / f"palace-gen{int(time.time())}"
            r = subprocess.run([py, "-c", _REBUILD_CHILD, str(old), str(new)], env=self._child_env(), capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                raise RuntimeError("palace rebuild failed: " + (r.stderr or r.stdout)[-300:])
            for fname in ("knowledge_graph.sqlite3",):
                if (old / fname).is_file():
                    shutil.copy2(old / fname, new / fname)
            shutil.rmtree(old, ignore_errors=True)
            be.path = new
        finally:
            be.start()

    def alias_candidates(self, entity: str) -> "list[str]":
        if not self.controls.alias_sweep:
            return []
        return alias_candidates(entity, [r.get("text", "") for r in self.stats()["rows"]])

    def forget_alias(self, alias: str) -> str:
        return self.forget(alias)

    def residue(self, entity: str) -> int:
        """Files of the whole MPA store (palace, config / WAL, server log) in which the forgotten name (ledger hashes only) still appears."""
        base = Path(self._need().root) if hasattr(self._need(), "root") else None
        if base is None or not base.exists():
            return 0
        return sum(1 for f in base.rglob("*") if f.is_file() and not f.is_symlink() and self.ledger.scan_bytes(self._user, f.read_bytes()))




def mpa_glue_lines() -> int:
    """Non-blank, non-comment lines of the MPA arm's own glue (this file + ``hm_policy.py``): decision-rule G3's Zoe layer for MPA."""
    d = Path(__file__).resolve().parent
    return sum(1 for f in ("mempalace_agent.py", "hm_policy.py") for ln in (d / f).read_text().splitlines() if ln.strip() and not ln.strip().startswith("#"))


__all__ = ["MemPalaceAgentArm", "MpaControls", "Prov", "TurnTrace", "ToolRecord", "mpa_glue_lines", "sim_date"]
