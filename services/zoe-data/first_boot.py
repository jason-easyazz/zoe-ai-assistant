"""first_boot - what a working household still needs, in order (pure; unwired, see docs/architecture/first-boot.md).

The owner's rule for a sold unit: the buyer never types a command. Zoe has to KNOW what setup is missing and offer it by voice,
physical steps are spoken from the panel, and anything that needs typing or an account sign-in is a QR finished on the phone. This
module is the knowledge: given a snapshot of the unit (``Facts``), it answers "what is missing, in order" deterministically, and
which ONE step Zoe should offer next. ``first_boot_probe`` reads the snapshot from the tables that already hold each answer (it
invents no parallel state); ``lexicons_data/first_boot_<lang>.json`` holds the words; ``labs/first-boot/`` drives both.

Each item's state is one of:

  done       the system already says so (read, never copied)
  missing    not done, and every item it depends on is done: Zoe may offer it
  waiting    not done, but an item it depends on is not done yet (``blocked_by`` names it)
  deferred   not done, and the owner said "later" (or "no") inside the quiet period: skipped by ``next_step``, so one "no"
             never stalls the rest of the flow
  unknown    the read failed. Never treated as "no": Zoe does not offer to set up what she may already have
  n/a        does not apply to this household (e.g. no child account exists)

Tiers: ``blocking`` (the household is not WORKING without it: a deferral is heard and skipped, but ``ready`` stays False and the
item comes back after its quiet period), ``deferrable`` (needed, may be put off explicitly; a deferral counts as settled),
``optional`` (offered only when asked, or as a panel card after every blocking and deferrable item is settled).

Never speech on its own: ``nudge`` answers for a moment the CALLER already has (a turn closing, the panel drawing a card, the person
asking "what's left?"). There is no timer here and nothing that speaks first (VISION principle 10).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

BLOCKING = "blocking"
DEFERRABLE = "deferrable"
OPTIONAL = "optional"
TIERS = (BLOCKING, DEFERRABLE, OPTIONAL)

DONE, MISSING, WAITING, DEFERRED, UNKNOWN, NA = "done", "missing", "waiting", "deferred", "unknown", "n/a"

#: the moments a caller may ask ``nudge`` about. ``asked`` = the person asked what is left (deferred items are named too).
SURFACES = ("turn_close", "panel_card", "asked")


@dataclass(frozen=True)
class Item:
    id: str
    tier: str
    needs: tuple = ()
    #: where the answer is READ (``first_boot_probe`` implements exactly this; the drift between the two is a test)
    source: str = ""


#: The ordered checklist. Order is the order Zoe walks it on first boot; ``needs`` only ever names an EARLIER item.
ITEMS: tuple = (
    Item("owner_account", BLOCKING, (),
         "auth_users: an active admin role (auth.is_admin_role) whose password_hash is set (not NULL / SETUP_REQUIRED)"),
    Item("forget_secret", BLOCKING, (),
         "memory_forgotten.configured(): ZOE_FORGET_LEDGER_SALT set, 16+ chars"),
    Item("panel_paired", BLOCKING, ("owner_account",),
         "panels row with an unrevoked, unexpired device_tokens row (routers/panel_auth.py; NULL expiry never expires)"),
    Item("panel_named", BLOCKING, ("panel_paired",),
         "panels.name is not the default (the panel id / Panel-<code>) and panels.room_id resolves in rooms "
         "(routers/panel_config.py _load_room)"),
    Item("time_and_place", BLOCKING, ("owner_account",),
         "ZOE_TIMEZONE explicitly set and valid (time_utils.zoe_timezone) and household_locale.country() is not empty"),
    Item("owner_pin", BLOCKING, ("owner_account",),
         "passcodes: an is_active row for the owner (zoe-auth core/passcode.py)"),
    Item("owner_voice", BLOCKING, ("owner_pin", "panel_paired"),
         "enrollment_state.get_state(owner).voice_enrolled (speaker_profiles, acting model, consented)"),
    Item("first_win", DEFERRABLE, ("panel_paired",),
         "the owner has an active reminder or a list that is not deleted (reminders, lists)"),
    Item("phone_linked", DEFERRABLE, ("owner_account",),
         "the owner's push_subscriptions, user_preferences.prefs.telegram_id (user_prefs.read_prefs) or an unrevoked "
         "phone_devices row"),
    Item("backup_target", DEFERRABLE, ("owner_account",),
         "the off-box backup config exists (ZOE_OFFBOX_CONFIG_DIR/offbox.env, scripts/maintenance/zoe_offbox.py)"),
    Item("more_members", OPTIONAL, ("owner_account",),
         "auth_users: an active account other than the owner (guests and synthetic ids excluded)"),
    Item("child_marked", OPTIONAL, ("owner_account",),
         "every child-role account (manner_block.CHILD_ROLES) has member_modes.minor = 1; n/a when there is no child account"),
    Item("calendar", OPTIONAL, ("phone_linked",),
         "connected_accounts: a kind='calendar' row with status='active' for the owner"),
    Item("music", OPTIONAL, ("phone_linked",),
         "connected_accounts: a kind='music' row with status='active' (music_setup hands off by QR)"),
)
BY_ID = {i.id: i for i in ITEMS}


@dataclass(frozen=True)
class Facts:
    """A snapshot of the unit. ``None`` in any field = the read failed (unknown). One bool per item id, plus the deferrals.

    ``child_marked`` is ``None`` for unknown and the string ``"n/a"`` when there is no child account."""

    owner_account: Optional[bool] = None
    forget_secret: Optional[bool] = None
    panel_paired: Optional[bool] = None
    panel_named: Optional[bool] = None
    time_and_place: Optional[bool] = None
    owner_pin: Optional[bool] = None
    owner_voice: Optional[bool] = None
    phone_linked: Optional[bool] = None
    backup_target: Optional[bool] = None
    first_win: Optional[bool] = None
    more_members: Optional[bool] = None
    child_marked: object = None
    calendar: Optional[bool] = None
    music: Optional[bool] = None
    #: item ids the owner put off ("later") whose quiet period has not ended (first_boot_probe reads enrollment_offers)
    deferred: frozenset = field(default_factory=frozenset)


@dataclass(frozen=True)
class Status:
    id: str
    tier: str
    state: str
    blocked_by: Optional[str] = None


def _raw(facts: Facts, item_id: str):
    return getattr(facts, item_id)


def evaluate(facts: Facts) -> tuple:
    """Every item's ``Status``, in checklist order. Pure and deterministic."""
    out: list = []
    done: dict = {}
    for item in ITEMS:
        raw = _raw(facts, item.id)
        if raw == NA:
            state, blocker = NA, None
        elif raw is True:
            state, blocker = DONE, None
        elif raw is None:
            state, blocker = UNKNOWN, None
        else:
            blocker = next((n for n in item.needs if not done.get(n)), None)
            if blocker is not None:
                state = WAITING
            elif item.id in facts.deferred:
                state = DEFERRED
            else:
                state = MISSING
        done[item.id] = state in (DONE, NA)
        out.append(Status(item.id, item.tier, state, blocker))
    return tuple(out)


def missing(facts: Facts, *, include_optional: bool = False) -> tuple:
    """What is missing, in order: every item not done / n/a (unknown and waiting included, so nothing is silently skipped).
    Optional items only when asked for."""
    return tuple(s for s in evaluate(facts)
                 if s.state not in (DONE, NA) and (include_optional or s.tier != OPTIONAL))


def ready(facts: Facts) -> bool:
    """A WORKING household: every blocking item done, every deferrable item done or explicitly deferred."""
    for s in evaluate(facts):
        if s.tier == BLOCKING and s.state != DONE:
            return False
        if s.tier == DEFERRABLE and s.state not in (DONE, DEFERRED):
            return False
    return True


def next_step(facts: Facts) -> Optional[Status]:
    """The ONE item Zoe should offer now on first boot: the first ``missing`` blocking item, else the first missing deferrable
    one. None when nothing is actionable (all done, or only unknown / waiting / deferred / optional left)."""
    rows = evaluate(facts)
    for tier in (BLOCKING, DEFERRABLE):
        for s in rows:
            if s.tier == tier and s.state == MISSING:
                return s
    return None


def nudge(facts: Facts, surface: str) -> Optional[Status]:
    """Any day, not just first boot: the item worth mentioning at a moment the caller ALREADY has. ``turn_close`` and
    ``panel_card`` never name a deferred item (the owner said later); ``asked`` does, and also reaches optional items once the
    rest is settled. Unknown is never named. An unrecognised surface names nothing."""
    if surface not in SURFACES:
        return None
    step = next_step(facts)
    if step is not None:
        return step
    rows = evaluate(facts)
    if surface == "asked":
        for s in rows:
            if s.state == DEFERRED:
                return s
    if surface in ("asked", "panel_card") and ready(facts):
        for s in rows:
            if s.tier == OPTIONAL and s.state == MISSING:
                return s
    return None


def summary(facts: Facts) -> dict:
    """The counts a report or a panel card reads: how many items in each state, and whether the household is ready."""
    counts: dict = {}
    for s in evaluate(facts):
        counts[s.state] = counts.get(s.state, 0) + 1
    nxt = next_step(facts)
    return {"ready": ready(facts), "counts": counts, "next": nxt.id if nxt else None,
            "missing": [s.id for s in missing(facts)]}
