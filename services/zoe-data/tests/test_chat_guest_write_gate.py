"""Chat requires sign-in for personal-data intents, like voice (2026-07-13).

Anonymous /api/chat/ callers get user_id='guest'; before this gate their
'add a dentist appointment' executed and wrote a guest-owned family-visible
event straight onto the household calendar. Voice already challenges for
who+PIN (skybridge_intent_requires_identity: calendar/lists/people); chat
now refuses with a sign-in instruction instead.
"""
import pytest

pytestmark = pytest.mark.ci_safe

from intent_router import Intent, execute_intent


@pytest.mark.parametrize("name,slots", [
    ("calendar_create", {"title": "dentist", "date": "2026-07-20", "time": "14:00"}),
    ("list_add", {"item": "bread", "list_type": "shopping"}),
    ("list_show", {"list_type": "shopping"}),
    ("reminder_create", {"message": "van", "time": "16:00"}),
    ("memory_remember", {"fact": "x"}),
])
@pytest.mark.parametrize("guest", ["guest", "", "voice-guest", "guest-abc123"])
async def test_guest_identities_are_gated(name, slots, guest):
    out = await execute_intent(Intent(name, slots), user_id=guest)
    assert out is not None and "sign in" in out.lower() or "who you are" in out.lower()


async def test_non_personal_intents_pass_for_guest():
    # weather/time/timers stay household-open — only personal data is gated.
    out = await execute_intent(Intent("acknowledgement", {}), user_id="guest")
    assert out is None or "who you are" not in (out or "")
