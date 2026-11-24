"""The memory scenarios for the call sites that can write a row a MEMBER authored and that only an automatic pass, a model
answer or a database row reaches (``tests/memory_scenarios.py`` runs them with the rest).

Each calls the site's OWN function, so the memory write goes through it, on the same recording collection. What the function
needs from outside is supplied at the narrowest seam and nowhere else: the local model's HTTP reply (``memory_scenarios.Model``:
the judge's pick, the extractor's JSON), the people database (``People``: an in-memory SQLite shaped as the pooled cursor), the day's chat
rows. Never the function under test, never the memory service.

Rows a MEMBER authored are stored first. Invented words and ids only.
"""
from __future__ import annotations

import json

from memory_entry_scenarios import _entry, _on, _Sqlite
from memory_scenarios import ANN, BEN, NEW, OLD, OTHER, Model, World, _set, _setenv


# ── the seams ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
class People(_Sqlite):
    """The people database: the columns the resolvers and the stub writers read and write."""

    def __init__(self, *rows):
        super().__init__()
        self.conn.execute(
            "CREATE TABLE people (id TEXT PRIMARY KEY, user_id TEXT, name TEXT, relationship TEXT, circle TEXT, context TEXT, "
            "visibility TEXT, is_partial INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0, birthday TEXT, "
            "notification_count INTEGER DEFAULT 0)")
        for pid, user, name, rel in rows:
            self.conn.execute("INSERT INTO people (id, user_id, name, relationship) VALUES (?, ?, ?, ?)", (pid, user, name, rel))
        self.conn.commit()

    def roster(self) -> list:
        return [list(r) for r in self.conn.execute("SELECT id, user_id, name, relationship, is_partial FROM people ORDER BY id")]


def _counted_ids() -> None:
    """A new person's id is random: counted here, so the id a relink writes is the same on every run."""
    import uuid

    n = iter(range(1, 10 ** 6))
    _set(uuid, "uuid4", lambda: uuid.UUID(int=next(n)))


def _backdate(w: World, row, days: int) -> None:
    """Make a stored row OLDER (its ``added_at`` / ``added_ts``): seeding only, not a write of any writer, so not in the record."""
    import datetime

    doc, meta = w.col.rows[row.id]
    then = datetime.datetime.utcnow() - datetime.timedelta(days=days)
    meta = dict(meta, added_at=then.isoformat() + "Z")
    if "added_ts" in meta:
        meta["added_ts"] = then.replace(tzinfo=datetime.timezone.utc).timestamp()
    if "valid_from" in meta:
        meta["valid_from"] = then.replace(tzinfo=datetime.timezone.utc).timestamp()
    w.col.rows[row.id] = (doc, meta)


def _wall_shadow() -> None:
    """The authority wall watching, not acting (``ZOE_MEMORY_AUTHORITY=shadow``, a documented setting): what an automatic
    writer does to a member's row when the wall does not stop it."""
    _setenv("ZOE_MEMORY_AUTHORITY", "shadow")


class _NoSupersedeBy:
    """A memory service of a build without ``supersede_by``: every other call IS the real service on the test store. The only
    state in which ``expert_dispatch._ingest_or_supersede`` retires the old row through ``review(archive)``."""

    def __init__(self, svc):
        self._svc = svc

    def __getattr__(self, name):
        if name == "supersede_by":
            raise AttributeError(name)
        return getattr(self._svc, name)


# ── a member acting ──────────────────────────────────────────────────────────────────────────────────────────────────────────
async def _fast_tier_archives_old(w):
    import expert_dispatch

    await w.said("User's dog is called Teddy.")
    await _entry(w, expert_dispatch._ingest_or_supersede(
        _NoSupersedeBy(w.svc), "User's dog is called Teddy Bear, a spaniel.", user_id=ANN, source="voice_fact", session_id=None,
        user_turn_id="turn0002", memory_type="fact", confidence=0.9, tags=["explicit"],
        source_excerpt="my dog is called Teddy Bear, a spaniel", **w.who({}, ANN)))


async def _forget_everything(w):
    """An admin's right-to-be-forgotten for one member: every row of theirs goes, another member's stay. The process has no
    database (as ``tests/conftest.py`` leaves it), so each eraser of a database store has nothing to reach; the file stores they
    rewrite are the test configuration's (``memory_scenarios._isolated`` refuses to run otherwise)."""
    import db_pool
    from routers import memories

    _set(db_pool, "_pool", None)
    _set(db_pool, "_ever_initialised", False)
    _set(memories, "get_memory_service", lambda: w.svc)
    await w.said(OLD)
    await w.said(OTHER)
    await w.said("User's cat is named Womble.", user=BEN)
    _on("ZOE_MEMORY_PHYSICAL_ERASE")
    out = await _entry(w, memories.forget_user(ANN, admin={"user_id": "member-ova", "role": "admin"}), keep=False)
    if isinstance(out, dict) and isinstance(out.get("physical_erase"), dict):
        out = dict(out, physical_erase={k: v for k, v in out["physical_erase"].items() if k != "seconds"})     # (how long it took)
    w.log.append(["entry-returned", out])
    w.log.append(["audit-rows", sorted(
        [str(m.get("action")), str(m.get("actor")), str(m.get("user_id")), str(m.get("before"))] for _d, m in w.audit.rows.values())])


async def _parked_forget_finished(w):
    """A forget parked until the kernel confirmed is finished: the parked rows are hard-erased as the member."""
    import turn_forget

    _on("ZOE_MEMORY_PHYSICAL_ERASE")
    row = await w.said(OLD, excerpt="my dog is named Teddy")
    await w.said(OTHER)
    await _entry(w, turn_forget.finish_parked(ANN, {"rows": [row.id]}))


# ── automatic writers, each over rows the member authored ────────────────────────────────────────────────────────────────────
async def _extractor_new_fact(w):
    """The per-turn extractor over the member's own turn, with nothing stored to reconcile against: a new row of their words."""
    import memory_extractor

    await w.said(OTHER)
    await _entry(w, memory_extractor.extract_and_ingest("my dog is named Rex", "", user_id=ANN))


async def _named_relation_new_fact(w):
    import named_relations

    await w.said(OTHER)
    await _entry(w, named_relations._ingest_user_fact("User has two kids: Mika and Sam.", ANN, "conversation", None,
                                                      "I have two kids, Mika and Sam"))


async def _background_save_fallback(w):
    """The background save when the extractor is unavailable: its own legacy patterns store the member's words."""
    import memory_extractor
    import zoe_agent

    async def unavailable(*_a, **_k):
        raise RuntimeError("extractor unavailable")

    _set(memory_extractor, "extract_and_ingest", unavailable)
    await w.said(OTHER)
    await _entry(w, zoe_agent._background_memory_save("my name is Ann and I live in Perth", "Noted.", ANN))


async def _held_person_beliefs(w):
    """What the member stated about a person that the people graph could not apply, kept as held rows in their name."""
    import memory_authority as ma
    import person_extractor as pe

    await w.said("Kip is User's brother.", memory_type="person", entity_type="person", entity_id="per0001")
    await _entry(w, pe._hold_fact_belief(ANN, "Kip", "Kip likes kites.", origin="conversation", basis="ambiguous_name",
                                         extra={"ambiguous_name": "Kip"}))
    await _entry(w, pe._hold_edge_belief(ANN, "Kip", "Mika", "father", origin="conversation", authority=ma.USER_STATED,
                                         basis="ambiguous_name", extra={"edge_rel_group": "family", "ambiguous_name": "Kip"}))


async def _nightly_conflicts_own_words(w):
    """The nightly conflict pass over two rows the MEMBER stated: the later one ends the earlier one."""
    import memory_supersede

    old = await w.said("User plays the cello.")
    _backdate(w, old, 40)
    await w.said("User gave up the cello.", excerpt="I gave up the cello")
    await _entry(w, memory_supersede.nightly_conflict_pass(w.svc, ANN))


async def _retire_by_claims(w):
    """The claim-key retirement ON: the member's new statement of where they live retires their older one."""
    import memory_supersede
    import structural_claims as sc

    _setenv("ZOE_STRUCTURAL_CLAIMS", "enforce")
    old = await w.said("User lives in Perth.", excerpt="I live in Perth")
    _backdate(w, old, 40)
    new = await w.said("User lives in Hobart.", excerpt="I live in Hobart now")
    claim = sc.Claim("user", "residence", "Hobart", "affirm", "asserted", "current", "I live in Hobart now", "en")
    await _entry(w, memory_supersede.retire_by_claims(w.svc, ANN, [(new, claim)]))


async def _pending_person_link(w):
    """The idle link resolver ON: a fact the member stated about someone not yet a contact is linked once the contact exists."""
    import memory_digest

    _on("ZOE_MEMORY_LINK_RESOLVER_ENABLED")
    await w.said("Kip likes kites.", memory_type="person", entity_type="person_pending", entity_id="slug:kip")
    await w.said(OTHER)
    await _entry(w, memory_digest._resolve_pending_person_links(ANN, db=People(("per0001", ANN, "Kip", "friend"))))


# ── the person extractor's row writer (``person_extractor._ingest_to_mempalace``), as the rule-based reader of the member's turn ──
async def _person_fact_new(w):
    import person_extractor as pe

    await w.said(OTHER)
    await _entry(w, pe._ingest_to_mempalace("Kip likes kites.", ANN, "Kip", None, source="conversation", pattern_type="preference",
                                            source_excerpt="my friend Kip likes kites"))


async def _kip_birthday_rows(w):
    kw = dict(memory_type="person", entity_type="person_pending", entity_id="slug:kip", metadata={"pattern_type": "birthday"})
    first = await w.said("Kip: March 15", excerpt="Kip's birthday is March 15", **kw)
    _backdate(w, first, 40)
    second = await w.said("Kip: 15th of March", excerpt="Kip's birthday is the 15th of March", **kw)
    _backdate(w, second, 30)


def _person_fact_changes_value(origin):
    """The member stated a person's birthday twice; a new value for the same person and kind arrives. Read by rule from the
    member's own turn it replaces the first row (and links it to the contact) and archives the stale second one; attributed to
    the MODEL half of the extractor (``origin``) with no correcting words, the wall refuses it and both rows stay."""
    async def run(w):
        import person_extractor as pe

        await _kip_birthday_rows(w)
        await _entry(w, pe._ingest_to_mempalace(
            "Kip: March 25", ANN, "Kip", "per0001", source="conversation", pattern_type="birthday",
            source_excerpt="Kip's birthday is on March 25", **({"origin": origin} if origin else {})))
    return run


async def _person_fact_extends(w):
    """No fact kind: the shared reconcile finds the member's sparser row about the same person, the richer fact replaces it
    and the row is linked to the contact."""
    import person_extractor as pe

    await w.said("Kip's dog is called Teddy.", excerpt="Kip's dog is called Teddy", memory_type="person",
                 entity_type="person_pending", entity_id="slug:kip")
    await _entry(w, pe._ingest_to_mempalace("Kip's dog is called Teddy Bear, a spaniel.", ANN, "Kip", "per0001",
                                            source="conversation", source_excerpt="Kip's dog is called Teddy Bear, a spaniel"))


async def _named_relations_owned_list(w):
    """"Kip's kids are Mika and Sam": the fact is stored against Kip (not yet a contact), the people graph mints Kip, and the
    fact is linked to the new contact."""
    import named_relations

    _counted_ids()
    db = People()
    await w.said(OTHER)
    await _entry(w, named_relations.apply_named_relations("Kip's kids are Mika and Sam.", user_id=ANN, db=db))
    w.log.append(["people", db.roster()])


# ── the model-driven passes: the model's reply is scripted, everything else is the real pass ─────────────────────────────────
JUDGE = "You keep notes about a person"
TURN_FACTS = "User said: "
DAY_FACTS = "Chat messages (user turns only):"
FEELINGS = "empathetic listener"


def _contradicts(new: str, old: str) -> str:
    return f"fact-contradiction judge|NEW fact:\n{new}\n|EXISTING fact:\n{old}\n"


async def _quote_retire(w):
    """Quote-backed retirement ON: the member says a thing ended, the judge picks their note about it, it is retired."""
    import memory_retire

    Model(w, {JUDGE + "|gave up the cello": {"pick": 1}}).install()
    _setenv("ZOE_QUOTE_RETIRE", "enforce")
    await w.said("User plays the cello.", excerpt="I play the cello")
    await w.said(OTHER)
    await _entry(w, memory_retire.distill_turn(ANN, "I gave up the cello last year.", lane="chat", svc=w.svc))


def _turn_digest(facts):
    """The per-turn digest over the member's own turn: the extractor's facts are scripted."""
    async def run(w):
        import memory_digest

        Model(w, {TURN_FACTS: facts}).install()
        await w.said(OLD, excerpt="my dog is named Teddy")
        await _entry(w, memory_digest.run_turn_digest(
            ANN, "My dog is named Rex now. I also started pottery classes on Mondays.", "Lovely.", session_id="sess0001"))
    return run


class DayOfChat:
    """The day's chat rows, as the pooled cursor returns them to the nightly loader (every other statement finds nothing)."""

    def __init__(self, *turns):
        self.rows = [(text, f"msg{n:04d}", f"2030-01-02T10:{n:02d}:00+00:00", None) for n, text in enumerate(turns, 1)]

    def execute(self, sql, params=()):
        from db_pool import _Cursor, _ExecResult

        rows = self.rows if "FROM chat_messages cm" in sql and "cm.role = 'user'" in sql else []

        async def _run():
            return _Cursor(list(rows), rowcount=len(rows))
        return _ExecResult(_run())

    async def commit(self):
        return None


DAY = ("We finally moved house and I live in Hobart now, the boxes are everywhere.",
       "My dog is called Teddy Bear, a spaniel, and he hates the stairs.",
       "I like the new garden a lot, it gets the sun all afternoon.",
       "I am so relieved the move is over, it took all of last month and most of my patience.")
#: what the extractor "read": a fact the judge will call a contradiction of the member's row, a richer wording of another row
#: of theirs (the shared reconcile's update), a new fact, and one their words do not carry (held)
DAY_REPLY = [{"type": "profile", "fact": "User lives in Hobart.", "quote": "I live in Hobart now"},
             {"type": "profile", "fact": "User's dog is called Teddy Bear, a spaniel.", "quote": "My dog is called Teddy Bear, a spaniel"},
             {"type": "preference", "fact": "User likes the new garden.", "quote": "I like the new garden a lot"},
             {"type": "event", "fact": "User moved house last month.", "quote": "We finally moved house"}]
MOMENT = [{"moment": "User was relieved that the house move was over.", "emotion": "relief", "significance": 3}]


def _a_member_with_no_mode_row() -> None:
    """The member-mode lookup (Postgres) answers "no row": an ordinary household member, for whom a feeling may be kept."""
    import persona_layer

    async def no_row(_uid, db=None):
        return persona_layer.MemberMode()

    _set(persona_layer, "load_member_mode", no_row)


def _night_digest(shadow):
    """The nightly digest over a day in which the member said they moved, with their earlier "lives in Perth" and their dog's
    name stored: the extractor's facts, the contradiction judge's yes and the emotional pass's moment are scripted. The wall
    acting: both rows of the member stay (the contradicting fact is held for them as a dispute). The wall only watching: the
    digest replaces both (its contradiction edit, and its reconcile edit of the dog's row)."""
    async def run(w):
        import memory_digest

        Model(w, {DAY_FACTS: DAY_REPLY, FEELINGS: MOMENT,
                  _contradicts("User lives in Hobart.", "User lives in Perth."): {"contradicts": True, "reason": "two homes"}}).install()
        if shadow:
            _wall_shadow()
        _a_member_with_no_mode_row()
        for text, said in (("User lives in Perth.", "I live in Perth"), ("User's dog is called Teddy.", "my dog is called Teddy")):
            _backdate(w, await w.said(text, excerpt=said), 40)
        await _entry(w, memory_digest.run_memory_digest(ANN, db=DayOfChat(*DAY)))
    return run


def _weekly_consolidation(shadow):
    """The weekly pass over rows the member stated: an identical duplicate (archived, nothing is lost), a pair the judge calls
    a contradiction (the wall acting: both stay; only watching: the older is replaced by the newer text), and a row old and
    unused enough for the decay sweep (the wall acting: kept; only watching: archived). An old inferred row is swept either way."""
    async def run(w):
        import memory_digest

        # (the judge is shown the surviving copy of the duplicate pair, as stored: no full stop)
        Model(w, {_contradicts(NEW, OLD.rstrip(".")): {"contradicts": True, "reason": "one name"}}).install()
        if shadow:
            _wall_shadow()
        for text, days in ((OLD, 800), (OLD.rstrip(".") + " ", 790), (OTHER, 780)):      # (the same words twice, as said twice)
            _backdate(w, await w.said(text), days)
        stale = await w.call("ingest", "User was thinking about a new bike.", user_id=ANN, source="digest", confidence=0.4)
        if stale is not None:
            _backdate(w, stale, 770)
        await w.said(NEW, excerpt="my dog is named Rex")
        await _entry(w, memory_digest.run_weekly_consolidation(ANN))
    return run


def scenarios() -> dict:
    return {
        "entry_member_fast_tier_archives_old": _fast_tier_archives_old,
        "entry_member_forget_everything": _forget_everything,
        "entry_member_parked_forget_finished": _parked_forget_finished,
        "entry_auto_extractor_new_fact": _extractor_new_fact,
        "entry_auto_named_relation_new_fact": _named_relation_new_fact,
        "entry_auto_background_save_fallback": _background_save_fallback,
        "entry_auto_held_person_beliefs": _held_person_beliefs,
        "entry_auto_nightly_conflicts_own_words": _nightly_conflicts_own_words,
        "entry_auto_retire_by_claims": _retire_by_claims,
        "entry_auto_pending_person_link": _pending_person_link,
        "entry_auto_person_fact_new": _person_fact_new,
        "entry_auto_person_fact_changes_value": _person_fact_changes_value(None),
        "entry_auto_person_fact_changes_value_model": _person_fact_changes_value("person_extractor_llm"),
        "entry_auto_person_fact_extends": _person_fact_extends,
        "entry_auto_named_relations_owned_list": _named_relations_owned_list,
        "entry_auto_quote_retire": _quote_retire,
        "entry_auto_turn_digest_change_of_mind": _turn_digest(
            [{"fact": NEW, "type": "profile"}, {"fact": "User started pottery classes on Mondays.", "type": "habit"}]),
        "entry_auto_night_digest": _night_digest(False), "entry_auto_night_digest_wall_shadow": _night_digest(True),
        "entry_auto_weekly_consolidation": _weekly_consolidation(False),
        "entry_auto_weekly_consolidation_wall_shadow": _weekly_consolidation(True),
    }
