"""The speaker verdict a TYPED turn carries: "if you are signed in, you are signed in" (owner decision, 2026-11-15).

A voice turn's verdict comes from the voice speaker gate (``routers/voice_tts._speaker_verdict``): True = the voice was accepted,
False = rejected, None = no verdict. A typed turn has no voice to score, so before this module it carried NO verdict, and the memory
claim validator's speaker rule (``memory_claims_validate.rule_16_speaker``: only ``True`` passes; None abstains) could never pass
for anything typed.

``typed_verdict`` gives a TYPED turn from a REAL, signed-in household member a positive verdict, tagged with its SOURCE so a reader
can tell it from a voice verification. Everything else gets NO verdict (None, never False): a guest has not failed a check, there
was no check.

"Signed in" means, in code, ALL of:
  * the principal passes ``panel_principal.is_member`` - a non-guest role, a non-guest id, not an auth-outage (``auth_degraded``)
    principal, not the legacy ``ZOE_UNAUTHENTICATED_ROLE=family-admin`` header-less promotion. A panel DEVICE TOKEN resolves to a
    guest-class principal and a kiosk guest session is a guest, so neither qualifies;
  * it was NOT produced by the ``X-Internal-Token`` + ``X-Zoe-User-Id`` override (``auth.resolve_acting_user`` stamps
    ``internal_override``): that proves a trusted caller ASSERTS an id (the Telegram bridge, the QA/probe harness), not that the
    person signed in. Such a turn abstains;
  * the lane is the typed ``chat`` channel and not the voice-mode header (spoken text through the chat endpoint has no speaker gate).

The verdict says WHO (the account), never that the account is an adult: a signed-in child's typed turn also carries it. The child
safety floor does not read it (its source of truth is the account's role / minor flag, ``child_safety_floor._who``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

#: ``chat_messages.metadata.speaker_source`` of a typed, signed-in turn.
TYPED_SIGNED_IN = "typed_signed_in"
#: the only channel a typed turn travels on (``routers/chat._resolve_channel``: "telegram" is the internal-override lane)
TYPED_CHANNEL = "chat"


@dataclass(frozen=True)
class Verdict:
    verified: bool
    source: str


def typed_verdict(user: Optional[Mapping[str, Any]], *, channel: str = TYPED_CHANNEL, voice_mode: bool = False) -> Optional[Verdict]:
    """A positive verdict for a typed turn from a signed-in member, else None (no verdict). NEVER raises, never returns a negative."""
    try:
        if voice_mode or (channel or "").strip().lower() != TYPED_CHANNEL:
            return None
        u = user or {}
        if u.get("internal_override") or u.get("panel_id"):
            return None
        import panel_principal

        if not panel_principal.is_member(u):
            return None
        return Verdict(True, TYPED_SIGNED_IN)
    except Exception:  # noqa: BLE001 - not knowing is "no verdict"
        return None


def from_metadata(metadata: Any) -> Optional[Verdict]:
    """The verdict a persisted ``chat_messages.metadata`` carries (a dict or its JSON): False -> Verdict(False, "voice_gate"),
    True with a source -> that source, anything else -> None. A bare ``True`` without a source is not read as a verdict."""
    try:
        meta = metadata
        if isinstance(meta, (str, bytes)):
            import json

            meta = json.loads(meta)
        if not isinstance(meta, dict):
            return None
        v = meta.get("speaker_verified")
        if v is False:
            return Verdict(False, str(meta.get("speaker_source") or "voice_gate"))
        if v is True and meta.get("speaker_source"):
            return Verdict(True, str(meta["speaker_source"]))
    except Exception:  # noqa: BLE001
        pass
    return None


def for_validator(metadata: Any) -> Optional[bool]:
    """The value for ``memory_claims_validate.Context.speaker_verified``: True / False / None (abstain)."""
    v = from_metadata(metadata)
    return None if v is None else v.verified
