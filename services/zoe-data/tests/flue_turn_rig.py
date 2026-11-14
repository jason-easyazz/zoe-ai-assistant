"""Synthetic Flue brain-turn rig: the REAL ``_run_flue_brain_streaming_turn`` assembly with every context block stubbed.

Not a test module (no ``test_`` prefix). ``outbound(...)`` runs one turn up to the moment the wire message is built and
returns that message; nothing reaches a sidecar, a database or a model. Every block is a plain string the caller picks
(synthetic data only - this repo is public), and an optional per-block delay makes the pre-POST chain measurable.

Used by ``test_flue_prepost.py`` (the FLUE_PREPOST / PANEL_KV_MISS instruments) and the prompt-stability tests.
"""
from __future__ import annotations

import asyncio

import zoe_flue_client as zfc

# block name -> (patch target module attr). Names match zoe_flue_client._PREPOST_BLOCKS.
BLOCK_NAMES = ("verify", "recall", "continuity", "hop", "offer", "list_share", "risk", "every_turn", "fastpath", "bridge",
               "notes", "identity", "named_guest", "now_card", "register", "manner", "persona")


class _Stop(Exception):
    pass


class _Plan:
    def __init__(self, block: str) -> None:
        self.block = block
        self.reply = ""
        self.status = "stub"


def install(monkeypatch, blocks: "dict[str, str] | None" = None, *, delay_s: "dict[str, float] | None" = None,
            hop_placement: str = "block", continuity_turn: bool = False) -> dict:
    """Stub every block source. ``blocks[name]`` is that block's text ('' = none). Returns the capture dict."""
    import conversation_notes
    import flue_fastpath_context
    import named_guests
    import personalisation_hop
    import recall_every_turn
    import register_requests
    import voice_session_bridge

    blocks = dict(blocks or {})
    delay_s = dict(delay_s or {})
    calls: list[str] = []

    def stub(name):
        async def _f(*_a, **_k):
            calls.append(name)
            d = delay_s.get(name, 0.0)
            if d:
                await asyncio.sleep(d)
            return blocks.get(name, "")
        return _f

    async def verify(*_a, **_k):
        calls.append("verify")
        d = delay_s.get("verify", 0.0)
        if d:
            await asyncio.sleep(d)
        b = blocks.get("verify", "")
        return _Plan(b) if b else None

    async def register_apply(*_a, **_k):
        calls.append("register_apply")
        return None

    async def spec(*_a, **_k):
        return None

    monkeypatch.setattr(zfc, "_verify_plan", verify)
    monkeypatch.setattr(zfc, "_recall_context_block", stub("recall"))
    monkeypatch.setattr(zfc, "_continuity_context_block", stub("continuity"))
    monkeypatch.setattr(zfc, "is_continuity_turn", lambda *_a, **_k: continuity_turn)
    monkeypatch.setattr(zfc, "_hop_context_block", stub("hop"))
    monkeypatch.setattr(personalisation_hop, "placement", lambda: hop_placement)
    monkeypatch.setattr(zfc, "_pending_offer_block", stub("offer"))
    monkeypatch.setattr(zfc, "_list_share_block", stub("list_share"))
    monkeypatch.setattr(zfc, "_risk_cue_block", stub("risk"))
    monkeypatch.setattr(recall_every_turn, "block_for", stub("every_turn"))
    monkeypatch.setattr(flue_fastpath_context, "block_for", stub("fastpath"))
    monkeypatch.setattr(voice_session_bridge, "block_for", stub("bridge"))
    monkeypatch.setattr(conversation_notes, "block_for", stub("notes"))
    monkeypatch.setattr(zfc, "_identity_context_block", stub("identity"))
    monkeypatch.setattr(named_guests, "brain_block", stub("named_guest"))
    monkeypatch.setattr(zfc, "_now_card_context_block", stub("now_card"))
    monkeypatch.setattr(register_requests, "apply", register_apply)
    monkeypatch.setattr(zfc, "_register_context_block", stub("register"))
    monkeypatch.setattr(zfc, "_manner_context_block", stub("manner"))
    monkeypatch.setattr(zfc, "_persona_context_block", stub("persona"))
    monkeypatch.setattr(zfc, "_speculative_turn_for_wire", spec)
    monkeypatch.setattr(zfc, "_household_locale_line", lambda: "")
    monkeypatch.delenv("ZOE_WRITE_CLAIM_CORRECTION", raising=False)   # the REAL _claim_correction_line runs: "" unless a test notes one

    cap: dict = {"calls": calls}
    real_wire = zfc._wire_version

    def capture(outbound):
        cap["msg"] = outbound
        return b"{}"

    def wire():
        if "msg" in cap:
            raise _Stop   # the payload is built and FLUE_PREPOST logged: stop before any transport
        return real_wire()

    monkeypatch.setattr(zfc, "_request_payload", capture)
    monkeypatch.setattr(zfc, "_wire_version", wire)
    return cap


async def outbound(cap: dict, message: str, session_id: str = "voice-panel-rig", uid: str = "rig-member",
                   **kwargs) -> str:
    """Run one REAL turn assembly; return the outbound wire message."""
    cap.pop("msg", None)
    cap["calls"].clear()
    try:
        async for _ in zfc._run_flue_brain_streaming_turn(message, session_id, uid, **kwargs):
            pass
    except _Stop:
        pass
    return cap["msg"]
