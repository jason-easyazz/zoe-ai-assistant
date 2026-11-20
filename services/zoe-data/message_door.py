"""The Zoe OS door for MESSAGES (``ZOE_OS_MESSAGES=kernel``; default off; docs/knowledge/zoe-os-lookups-messages-door.md).

A MEMBER asking Zoe to send something is asked first, at the three places that exist today: the intercom (their words on the
house's panels), the brain's panel toast, and "send the sign-in link to my phone" (their own linked Telegram chat). A refusal
is final: nothing is sent. A kernel that is down or slow = the send proceeds, ``not_checked``.

EXCEPT a send to an address that is NOT a known contact or channel of the household (``EXTERNAL``): never sent unchecked, a
kernel that cannot be asked means NO. No sender of that kind exists today; the rule is here for the first one.

Zoe's OWN notifications (a reminder firing, an alarm, a proactive nudge, a change notice, the day-review digest) are
automatic: they do not call this module, and are neither asked nor recorded.

ON THE WIRE: the channel class (the verb), the acting member's id, and the recipient's member id when the recipient is a
known person. NEVER the words, a phone number, a chat id or an address.
"""
from __future__ import annotations

from typing import Any, Optional

import kernel_door
import os_shadow

AREA = "messages"
PANEL, TELEGRAM, PUSH, EXTERNAL = "send.panel", "send.telegram", "send.push", "send.external"


class MessageRefused(kernel_door.Refused):
    """The kernel refused this send (or could not be asked about one to an outside address). FINAL: nothing was sent."""

    area = AREA
    lines = {
        "adults_only": "That one needs a grown-up, so I haven't sent it.",
        "not_owner": "I can't send that from here, so I haven't.",
        "unknown_person": "I don't know who is asking, so I haven't sent that.",
        "unavailable": "I can't check whether I may send that right now, so I haven't.",
    }
    fallback = "I'm not allowed to send that."


def stamps_now() -> Optional[dict]:
    """First statement of a member-driven sender, before anything is awaited (None while the door is off)."""
    return kernel_door.stamps_now(AREA)


async def ask(channel: str, actor: Any, *, recipient: Any = None, stamps_at: Optional[dict]) -> Optional[kernel_door.Decision]:
    """Before a member's message is sent. None: not asked (door off). Raises ``MessageRefused`` on a kernel "no", and for
    ``EXTERNAL`` whenever the kernel did not say yes. ``recipient``: a member id, or None (the panels; an outside address)."""
    if not os_shadow.area_on(AREA):
        return None
    who = str(actor or "")
    panel = not who or kernel_door.is_panel(who)
    to = str(recipient or "")
    decision, reason, ask_no, stamps = await os_shadow.ask_door(
        AREA, channel, "" if panel else who, panel=panel, target=None,
        owner=None if (not to or kernel_door.is_panel(to)) else to, household=False,
        stamps_at=stamps_at if stamps_at is not None else os_shadow.stamp_snapshot())
    if decision == "refuse":
        raise MessageRefused(reason)
    if channel == EXTERNAL and decision != "allow":
        raise MessageRefused("unavailable")
    return kernel_door.Decision(decision, reason, ask_no, stamps=stamps, area=AREA)


def sent(asked: Optional[kernel_door.Decision], ok: bool) -> None:
    """How the send ended (never raises, never waits)."""
    kernel_door.wrote(asked, "done" if ok else "failed")
