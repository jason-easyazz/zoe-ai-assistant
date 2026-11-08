"""Forget completeness across the DERIVED layers (Samantha initiative roadmap item 15; owner rule "forgotten means forever").

``forget_reach.py`` + ``test_forget_reach_inventory.py`` hold the inventory: which STORE the forget path erases. This probe asks
the other half: after a member forgets a name, does any layer still SERVE it? A copy or a derivative of a fact can live in a
digest, a night-mind thread, a day-review candidate, an exact-words quote, a person profile, the relationship graph, a contact
offer, the palace, the voice recall packet, the user-model card... Each layer below is driven through its REAL serve function over
in-process fakes (no live DB, no network, no model) in three arms:

  forgotten     the ledger entry AND the layer's own erase hook (what the forget path runs): the name is never served, and the
                control name is (the layer still works - an empty layer would pass for the wrong reason)
  erase_missed  the ledger entry only (the erase did not land: a failed cascade step, a pending retry, a row derived afterwards
                by a writer that missed the guard). A layer with a SERVE-TIME wall still withholds the name; a layer without one
                serves it. Which is which is RECORDED in ``LAYERS[...].serve_wall`` and asserted both ways, so adding a wall
                (or losing one) forces this table to change. The single-wall layers are in docs/knowledge/open-problems.md.
  control       the layer's forget hooks switched off (no erase, serve wall disabled): the name IS served - the instrument sees
                a leak when there is one.

Layers the probe cannot drive in-process (the Flue sidecar's own caches and transcript store, the llama prefix/KV cache) are
``UNPROBED`` with a reason and must appear in the open-problems entry; ``now_card.py`` (#2281) has its probe row, and
``test_now_card_is_probed_once_it_exists`` keeps it there.

Invented names only (``Dana`` is the forgotten friend, ``Leo`` the control): the repo is public.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import aiosqlite
import pytest

import memory_forget_cascade
import memory_forgotten as mf
import memory_tombstones
from forgotten_support import (  # noqa: F401 - fixtures
    FRIEND, USER, ledger_env, no_offers, open_forgotten_db, svc, use_db,
)

pytestmark = pytest.mark.ci_safe

KEEP = "Leo"
SVC_DIR = Path(__file__).resolve().parents[1]
REPO = SVC_DIR.parents[1]


def run(coro):
    return asyncio.run(coro)


async def _ledger(name: str = FRIEND) -> None:
    """The durable half of "forget Dana" (written first by the real handler, on every exit path). No tombstone: the probe is
    about what holds AFTER the 300 s tombstone has gone."""
    assert await mf.add(USER, name, actor=USER) is True
    memory_tombstones.clear_all()


def _names(served: str, name: str) -> bool:
    return re.search(rf"\b{re.escape(name)}\b", served or "", re.IGNORECASE) is not None


@dataclass
class Layer:
    name: str
    serve_wall: bool                # True: a serve-time ledger check withholds the name even when the erase did not land
    erase_hook: str                 # what the forget path runs to erase this layer ("" = none: the serve wall is the only wall)
    serves: str                     # the serve function the probe calls


LAYERS: dict[str, Layer] = {l.name: l for l in (
    Layer("exact_words", True, "exact_words.erase_entity", "exact_words.lookup"),
    Layer("night_mind", True, "night_mind.erase_entity", "night_mind.snapshot (cold and cached)"),
    Layer("digest_transcript", True, "", "memory_digest._load_todays_messages"),
    Layer("memories_wall", True, "intent_router memory_forget_entity sweep",
          "provenance_answers.gather (what do you know about me / the Memories wall / memories_voice)"),
    Layer("palace_reads", False, "intent_router memory_forget_entity sweep (archive + physical erase)",
          "MemoryService._metadata_read / list_by_status / get"),
    Layer("recall_packet", True, "", "recall_every_turn.select (the voice/brain recall packet)"),
    Layer("person_profile", True, "memory_forget_cascade people/edges", "person_profile.compose + render"),
    Layer("relationship_graph", False, "memory_forget_cascade people/edges", "zoe_memory_compose._fetch_relational"),
    Layer("user_model_card", True, "memory_forget_cascade card", "user_model_card.load_card_block"),
    Layer("portrait", False, "memory_forget_cascade portrait", "user_portrait.load_portrait"),
    Layer("open_loops", False, "memory_forget_cascade open_loops", "panel_review_cards.member_loops (the panel / spoken loops)"),
    Layer("proactive_candidates", False, "memory_forget_cascade proactive_candidates", "proactive.pull.pending_items (the raise)"),
    Layer("contact_offers", True, "pending_suggestions.resolve_person_offers_by_name",
          "pending_suggestions load_for_prompt / surface / list_* / surfaced_person_offers"),
    Layer("day_review", True, "omi_day_review.erase_entity", "omi_day_review.list_candidates / get_candidate"),
    Layer("self_review", True, "self_review.erase_entity (cascade step self_review)", "self_review.load_reviews (the owner's --show)"),
    Layer("now_card", True, "now_card.erase_entity (intent_router memory_forget_entity)", "now_card._live (card_for)"),
)}

#: Derived layers the probe cannot drive in-process. Each key must appear in the open-problems "forget-completeness" entry.
UNPROBED: dict[str, str] = {
    "flue_user_model_cache": "the Flue sidecar caches the user-model card in-process (labs/flue-zoe-brain-2x/src/user-model.ts, "
                             "ZOE_USER_MODEL_TTL_MS 300 s, keeps the last entry on a failed refresh); nothing invalidates it on forget",
    "flue_conversation_store": "the sidecar's own transcript store (ZOE_BRAIN_DB) is reached only by the offline redact_backups.py "
                               "with the brain stopped; no runtime forget path touches it",
    "llama_prefix_kv": "the llama-server prompt/KV cache holds the same history and is never flushed on forget",
    "relationship_free_text": "people.notes / context, person_relationships.notes and person_activities of OTHER live people are "
                              "served by zoe_memory_compose._fetch_relational with no ledger check and are not erased by the cascade",
    "member_register": "the per-member speaking register (alembic 0068): rows are enum-mapped settings (length / formality / humour / pace) and "
                       "short sanitised liked-topic labels the member stated, rendered as one short line; delete_user erases every row "
                       "(forget_reach REACHED) but an entity forget does not scrub a name out of a liked-topic label",
    "commitments_sweep": "commitments._names_forgotten fails OPEN on a ledger outage at sweep time (the cascade erase is the only wall then)",
}


# ── the layers ───────────────────────────────────────────────────────────────

class Probe:
    """One layer: ``seed`` a derivative naming FRIEND and one naming KEEP, ``erase`` (the layer's forget hook), ``served`` (the
    real serve function's text), ``disable`` (the serve wall off)."""

    async def setup(self, monkeypatch, request) -> None: ...
    async def seed(self) -> None: ...
    async def erase(self) -> None: ...
    async def served(self) -> str: ...
    def disable(self, monkeypatch) -> None: ...
    async def teardown(self) -> None: ...


class ExactWords(Probe):
    async def setup(self, monkeypatch, request):
        import exact_words as xw
        self.xw = xw
        monkeypatch.delenv(xw.ENV, raising=False)
        xw.set_backend(xw.MemoryBackend())

    async def seed(self):
        now = time.time()
        assert await self.xw.index_turn(USER, f"I told {FRIEND} the dinner is on Thursday at the harbour.", said_at=now - 60)
        assert await self.xw.index_turn(USER, f"I told {KEEP} the garage door is fixed and he can come round.", said_at=now - 30)

    async def erase(self):
        await self.xw.erase_entity(USER, FRIEND)

    async def served(self):
        hits = await self.xw.lookup(USER, f"what exactly did I say to {FRIEND} about the dinner")
        hits += await self.xw.lookup(USER, f"what exactly did I tell {KEEP} about the garage door")
        return "\n".join(h.text for h in hits)

    def disable(self, monkeypatch):
        async def keep_all(_uid, rows, **_k):
            return list(rows), 0

        async def never(*_a, **_k):
            return False
        monkeypatch.setattr(mf, "keep_unforgotten", keep_all)
        monkeypatch.setattr(mf, "matches", never)

    async def teardown(self):
        self.xw.set_backend(None)


class NightMind(Probe):
    """The cached read too: the snapshot is primed BEFORE the forget and read through the 90 s cache after it."""

    async def setup(self, monkeypatch, request):
        import night_mind as nm
        import night_store as ns
        self.nm, self.ns = nm, ns
        monkeypatch.setenv(nm.ENV, "serve")
        ns.set_backend(ns.MemoryBackend())
        nm._reset_state()

    async def seed(self):
        be = self.ns.get_backend()
        for tid, who, quote in (("t-a", FRIEND, f"{FRIEND} is moving to the coast next month and I will miss her"),
                                ("t-b", KEEP, f"{KEEP} finally finished the garden fence this weekend")):
            await be.put_thread({"id": tid, "user_id": USER, "title": f"{who}'s news", "status": "open", "first_day": "2026-11-01",
                                 "last_day": "2026-11-04", "mentions_n": 2, "weight_max": 3})
            await be.put_observation({"id": f"o-{tid}", "user_id": USER, "thread_id": tid, "turn_id": f"u-{tid}", "quote": quote,
                                      "who": who, "weight": 3, "day": "2026-11-04", "said_at": time.time(), "state": "current"})
        await self.nm.snapshot(USER, use_cache=True)            # primed: the cache holds the quote naming FRIEND

    async def erase(self):
        await self.nm.erase_entity(USER, FRIEND)

    async def served(self):
        out = []
        for cached in (True, False):
            _t, obs = await self.nm.snapshot(USER, use_cache=cached, all_threads=True)
            out += [o["quote"] for o in obs]
        return "\n".join(out)

    def disable(self, monkeypatch):
        async def never(*_a, **_k):
            return False
        monkeypatch.setattr(self.nm, "_shielded", never)

    async def teardown(self):
        self.nm._reset_state()
        self.ns.set_backend(None)


class DigestTranscript(Probe):
    """The nightly digest / night mind / open-loops source: the day's turns. Its erase is the in-place transcript redaction
    (``forget_redact``, probed in test_forget_to_the_box); the loader's own wall is what this layer probes."""

    async def setup(self, monkeypatch, request):
        import memory_digest as md
        from test_forget_durable_ledger import _TranscriptDb
        self.md = md
        self.db = _TranscriptDb([f"so {FRIEND} rang about the weekend and we will sort the shopping later",
                                 f"I picked up tea with {KEEP} and sorted the garden shed out"])

    async def served(self):
        return await self.md._load_todays_messages(USER, self.db)

    def disable(self, monkeypatch):
        async def passthrough(_uid, items, _reader):
            return list(items)
        monkeypatch.setattr(self.md, "_skip_forgotten_turns", passthrough)


async def _forget_handler(name: str = FRIEND):
    import intent_router
    return await intent_router.execute_intent(intent_router.Intent("memory_forget_entity", {"name": name}), USER)


class _PalaceBacked(Probe):
    """Rows in the palace (the ``svc`` fixture: the real MemoryService over the where-honouring Chroma fake); the erase is the
    REAL forget handler's sweep."""

    async def setup(self, monkeypatch, request):
        self.svc = request.getfixturevalue("svc")
        request.getfixturevalue("no_offers")
        monkeypatch.setenv("ZOE_MEMORY_PHYSICAL_ERASE", "0")       # the fake collection has no sqlite file to scrub

    async def seed(self):
        self.ids = []
        for text in (f"User's friend {FRIEND} lives in Hobart.", f"User's friend {KEEP} plays the cello."):
            ref = await self.svc.ingest(text, user_id=USER, source="voice_fact", confidence=0.9)
            assert ref is not None
            self.ids.append(ref.id)

    async def erase(self):
        reply = await _forget_handler()
        assert reply and "couldn't" not in reply.lower(), reply


class MemoriesWall(_PalaceBacked):
    async def served(self):
        import provenance_answers as pa
        known = await pa.gather(USER, svc=self.svc)
        items = list(known.items) + [i for lst in known.private.values() for i in lst]
        return "\n".join([i.text for i in items] + [p[0] for p in known.people])

    def disable(self, monkeypatch):
        import provenance_answers as pa

        async def keep_all(_uid, items, _text_of):
            return list(items)
        monkeypatch.setattr(pa, "_drop_forgotten", keep_all)


class PalaceReads(_PalaceBacked):
    async def served(self):
        rows = [r.text for r in self.svc._metadata_read(USER, 50)]
        rows += [r.text for r in await self.svc.list_by_status(user_id=USER, status="approved")]
        for mem_id in self.ids:                                       # the by-id read (provenance, cards) - no status filter of its own
            ref = await self.svc.get(mem_id)
            if ref is not None and str((ref.metadata or {}).get("status") or "approved") == "approved":
                rows.append(ref.text)
        return "\n".join(rows)

    def disable(self, monkeypatch):
        pass                                                         # no serve wall: the erase is the only one


class RecallPacket(Probe):
    async def setup(self, monkeypatch, request):
        import memory_provenance
        import recall_every_turn as rt
        import restraint
        from test_recall_every_turn import FakeSvc, row
        self.rt = rt
        for k in ("ZOE_RECALL_EVERY_TURN", "ZOE_TRAFFIC_CLASS", "ZOE_RESTRAINT"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("ZOE_RECALL_EVERY_TURN_HARNESS", "1")      # USER is a bar (harness-class) id

        async def no_mutes(_uid):
            return []
        monkeypatch.setattr(restraint, "list_mutes", no_mutes)
        monkeypatch.setattr(memory_provenance, "is_off_record", lambda *_a, **_k: False)
        rt._reset_state()
        self.svc = FakeSvc([row("m_dana_0001", f"User's friend {FRIEND} walks the kelpie on the beach every morning.", uid=USER),
                            row("m_leo_00002", f"User's friend {KEEP} walks the greyhound on the beach every evening.", uid=USER)])

    async def served(self):
        out = []
        for msg in (f"Did {FRIEND} walk the kelpie on the beach", f"Did {KEEP} walk the greyhound on the beach"):
            self.rt._reset_state()
            if not self.rt.eligible(USER, msg, False):
                await self.rt._build(USER, self.svc)
            out += list((await self.rt.select(USER, msg, svc=self.svc)).texts)
        return "\n".join(out)

    def disable(self, monkeypatch):
        async def never(*_a, **_k):
            return False
        monkeypatch.setattr(self.rt, "_forgotten", never)

    async def teardown(self):
        self.rt._reset_state()
        await self.rt.drain()


_PROFILE_DDL = """
CREATE TABLE people (id TEXT PRIMARY KEY, user_id TEXT, name TEXT, relationship TEXT, circle TEXT, birthday TEXT, how_we_met TEXT,
    deleted INTEGER DEFAULT 0, is_partial INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT, last_contacted_at TEXT);
CREATE TABLE person_important_dates (id TEXT PRIMARY KEY, person_id TEXT, user_id TEXT, label TEXT, date_type TEXT, month INTEGER,
    day INTEGER, year INTEGER, reminder_days_before INTEGER NOT NULL DEFAULT 7);
CREATE TABLE person_gift_ideas (id TEXT PRIMARY KEY, person_id TEXT, user_id TEXT, description TEXT, status TEXT DEFAULT 'idea',
    source TEXT, created_at TEXT);
CREATE TABLE person_relationships (id TEXT PRIMARY KEY, user_id TEXT, person_a_id TEXT, person_b_id TEXT, rel_a_to_b TEXT,
    rel_b_to_a TEXT, valid_to TEXT);
"""


class PersonProfile(Probe):
    """"Tell me about Tove": a profile of a LIVE person carries the forgotten one through a relation, a gift idea and how they met."""

    async def setup(self, monkeypatch, request):
        import person_profile as pp
        self.pp = pp
        self.db = await aiosqlite.connect(":memory:")
        await self.db.executescript(_PROFILE_DDL)

    async def seed(self):
        ins = self.db.execute
        await ins("INSERT INTO people (id, user_id, name, relationship, how_we_met) VALUES ('p-tove', ?, 'Tove Arden', 'friend', ?)",
                  (USER, f"through {FRIEND} at the climbing gym"))
        await ins("INSERT INTO people (id, user_id, name, relationship) VALUES ('p-dana', ?, ?, 'friend')", (USER, FRIEND))
        await ins("INSERT INTO people (id, user_id, name, relationship) VALUES ('p-leo', ?, ?, 'friend')", (USER, KEEP))
        await ins("INSERT INTO person_relationships VALUES ('e1', ?, 'p-tove', 'p-dana', 'sister', 'sister', NULL)", (USER,))
        await ins("INSERT INTO person_relationships VALUES ('e2', ?, 'p-tove', 'p-leo', 'colleague', 'colleague', NULL)", (USER,))
        await ins("INSERT INTO person_gift_ideas VALUES ('g1', 'p-tove', ?, ?, 'idea', 'manual', '2026-01-01')",
                  (USER, f"a frame for the photo with {FRIEND}"))
        await self.db.commit()

    async def erase(self):
        # the cascade's people + edges steps (the real SQL lives in memory_forget_cascade; mirrored on this table's columns)
        await self.db.execute("UPDATE people SET deleted = 1 WHERE user_id = ? AND name = ?", (USER, FRIEND))
        await self.db.execute("UPDATE person_relationships SET valid_to = '2026-11-05' WHERE person_b_id = 'p-dana'")
        await self.db.commit()

    async def served(self):
        class _NoRows:
            async def list_by_entity(self, *_a, **_k):
                return []
        prof = await self.pp.compose(self.db, USER, "p-tove", svc=_NoRows())
        assert prof is not None
        return "\n".join([prof.how_we_met, self.pp.render(prof, voice=False)] + [o for o, _r in prof.relations] + list(prof.gifts))

    def disable(self, monkeypatch):
        async def never(*_a, **_k):
            return False
        monkeypatch.setattr(self.pp, "_forgotten", never)

    async def teardown(self):
        await self.db.close()


class _CascadeBacked(Probe):
    """Rows in the derived Postgres tables (in-memory SQLite: the people graph migrations + the derived tables + the REAL 0038
    ledger table); the erase is the REAL ``memory_forget_cascade.cascade_forget``. ``disable`` skips this layer's cascade step."""

    step = ""

    async def setup(self, monkeypatch, request):
        self.db = await open_forgotten_db()
        use_db(monkeypatch, self.db)
        self.monkeypatch = monkeypatch

    async def erase(self):
        res = await memory_forget_cascade.cascade_forget(USER, FRIEND, db=self.db)
        assert not res.failed, res.failed

    def disable(self, monkeypatch):
        real = memory_forget_cascade._step
        step = self.step

        async def skip(label, fn, res=None):
            return 0 if label == step else await real(label, fn, res)
        monkeypatch.setattr(memory_forget_cascade, "_step", skip)

    async def teardown(self):
        await self.db.close()

    async def rows(self, sql, params=()):
        async with self.db.execute(sql, params) as cur:
            return [tuple(r) for r in await cur.fetchall()]


class Portrait(_CascadeBacked):
    step = "portrait"

    async def seed(self):
        await self.db.execute("INSERT INTO user_portraits (user_id, portrait_text) VALUES (?, ?)",
                              (USER, f"Loves the garden. Close to {FRIEND}, who visits often, and to {KEEP} from work."))
        await self.db.commit()

    async def served(self):
        import user_portrait
        text = await user_portrait.load_portrait(USER, db=self.db)
        return text or f"(no portrait) {KEEP}"          # the next dreaming pass rebuilds it from rows without the name


class UserModelCard(_CascadeBacked):
    step = "card"

    async def setup(self, monkeypatch, request):
        await super().setup(monkeypatch, request)
        import user_model_card as umc
        self.umc = umc
        await self.db.execute("ALTER TABLE user_model_cards ADD COLUMN version TEXT")
        await self.db.commit()

        async def all_live(_uid, ids):
            return set(ids)
        monkeypatch.setattr(umc, "_live_ids", all_live)        # every source row still approved: the erase-missed case

    async def seed(self):
        card = {"name": "", "style": "", "items": [["people", f"Friend {FRIEND} visits on Sundays", "m1"],
                                                    ["people", f"Colleague {KEEP} plays chess on Fridays", "m2"]]}
        text = self.umc.render_card(card)
        await self.db.execute("INSERT INTO user_model_cards (user_id, card_json, card_text, version) VALUES (?, ?, ?, 'v1')",
                              (USER, json.dumps(card), text))
        await self.db.commit()

    async def served(self):
        out = await self.umc.load_card_block(USER, build_missing=False)
        return out["text"] or f"(no card) {KEEP}"         # rebuilt nightly from rows without the name

    def disable(self, monkeypatch):
        super().disable(monkeypatch)

        async def none(*_a, **_k):
            return set()
        monkeypatch.setattr(self.umc, "_forgotten_items", none)


class OpenLoops(_CascadeBacked):
    step = "open_loops"

    async def setup(self, monkeypatch, request):
        await super().setup(monkeypatch, request)
        import db_compat
        await self.db.execute("ALTER TABLE open_loops ADD COLUMN created_at TEXT DEFAULT '2026-11-04T10:00:00Z'")
        await self.db.commit()
        db = self.db

        @contextlib.asynccontextmanager
        async def compat():
            yield db
        monkeypatch.setattr(db_compat, "get_compat_db", compat)

    async def seed(self):
        for t in (f"Ring {FRIEND} back about the weekend", f"Return {KEEP}'s drill"):
            await self.db.execute("INSERT INTO open_loops (user_id, loop_text, follow_up_hint) VALUES (?, ?, '')", (USER, t))
        await self.db.commit()

    async def served(self):
        import panel_review_cards
        return "\n".join(lp["text"] for lp in await panel_review_cards.member_loops(USER, wall=False))


class ProactiveCandidates(_CascadeBacked):
    step = "proactive_candidates"

    async def setup(self, monkeypatch, request):
        await super().setup(monkeypatch, request)
        for col in ("source_ref TEXT DEFAULT ''", "salience REAL DEFAULT 1", "expires_at TEXT DEFAULT '9999-12-31T00:00:00Z'",
                    "cooldown_until TEXT", "surfaced_count INTEGER DEFAULT 0"):
            await self.db.execute(f"ALTER TABLE proactive_candidates ADD COLUMN {col}")
        await self.db.commit()

    async def seed(self):
        for i, t in enumerate((f"{FRIEND} is visiting on Saturday", f"{KEEP}'s birthday is next week")):
            await self.db.execute("INSERT INTO proactive_candidates (id, user_id, text, hint) VALUES (?, ?, ?, '')", (f"c{i}", USER, t))
        await self.db.commit()

    async def served(self):
        from proactive import pull
        return "\n".join(f"{i.text} {i.hint}" for i in await pull.pending_items(self.db, USER))


class RelationshipGraph(_CascadeBacked):
    step = "people"

    async def seed(self):
        for pid, name in (("p-dana", FRIEND), ("p-leo", KEEP), ("p-tove", "Tove")):
            await self.db.execute("INSERT INTO people (id, user_id, name, deleted, visibility) VALUES (?, ?, ?, 0, 'private')",
                                  (pid, USER, name))
        for rid, b in (("e1", "p-dana"), ("e2", "p-leo")):
            await self.db.execute(
                "INSERT INTO person_relationships (id, user_id, person_a_id, person_b_id, rel_type, rel_a_to_b, rel_b_to_a, rel_group, "
                "created_at, updated_at, valid_from) VALUES (?, ?, 'p-tove', ?, 'friend', 'Friend', 'Friend', 'personal', "
                "'2026-01-01', '2026-01-01', '2026-01-01')", (rid, USER, b))
        await self.db.commit()

    async def served(self):
        import zoe_memory_compose as zmc
        self.db.row_factory = aiosqlite.Row
        try:
            rel = await zmc._fetch_relational(self.db, USER)
        finally:
            self.db.row_factory = None
        names = [p["name"] for p in rel["people"]] + [f"{r['name_a']} {r['name_b']}" for r in rel["relationships"]]
        return "\n".join(names)

    def disable(self, monkeypatch):
        real = memory_forget_cascade._step

        async def skip(label, fn, res=None):
            return 0 if label in ("people", "edges") else await real(label, fn, res)
        monkeypatch.setattr(memory_forget_cascade, "_step", skip)


class _PgShim:
    """``pending_suggestions`` speaks asyncpg (``$1``, ``fetch``); this runs it on aiosqlite."""

    def __init__(self, db):
        self._db = db

    @staticmethod
    def _tr(sql):
        return re.sub(r"\$\d+", "?", sql)

    async def execute(self, sql, *args):
        await self._db.execute(self._tr(sql), args)
        await self._db.commit()

    async def fetch(self, sql, *args):
        async with self._db.execute(self._tr(sql), args) as c:
            rows = await c.fetchall()
        await self._db.commit()
        return rows

    async def fetchrow(self, sql, *args):
        rows = await self.fetch(sql, *args)
        return rows[0] if rows else None


class ContactOffers(Probe):
    """"Add Dana to your contacts?" - including the forget that found no memory rows (the handler returned before withdrawing)."""

    async def setup(self, monkeypatch, request):
        import pending_suggestions as ps
        self.ps = ps
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript("""
            CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, role TEXT);
            CREATE TABLE people (id TEXT PRIMARY KEY, user_id TEXT, name TEXT, deleted INTEGER DEFAULT 0);
            CREATE TABLE pending_suggestions (id TEXT PRIMARY KEY, user_id TEXT, session_id TEXT, action_type TEXT, description TEXT,
                list_type TEXT, when_hint TEXT, amount_hint TEXT, offer_phrase TEXT, pre_filled_slots TEXT, created_at TEXT,
                turns_elapsed INTEGER DEFAULT 0, expire_after_turns INTEGER DEFAULT 6, resolved INTEGER DEFAULT 0);""")

        @contextlib.asynccontextmanager
        async def ctx():
            yield _PgShim(self.db)
        monkeypatch.setattr(ps, "get_db_ctx", ctx)

    async def seed(self):
        offers = [{"action_type": "person_create", "description": f"Add {n} to contacts", "offer_phrase": f"Add {n} to your contacts?",
                   "pre_filled_slots": {"name": n}} for n in (f"{FRIEND} Whitfield", KEEP)]
        assert await self.ps.store_suggestions(USER, "s1", offers) == 2
        await self.db.execute("UPDATE pending_suggestions SET turns_elapsed = 1")    # both already surfaced once
        await self.db.commit()

    async def erase(self):
        await self.ps.resolve_person_offers_by_name(USER, FRIEND)     # exact-name withdrawal: "Dana Whitfield" is not "Dana"

    async def served(self):
        ps = self.ps
        out = [await ps.load_for_prompt(USER, "s1")]
        out += [o["offer_phrase"] for o in await ps.surface_pending_contacts_for_prompt(USER)]
        out += [o["offer_phrase"] for o in await ps.list_pending_contacts(USER)]
        out += [o["offer_phrase"] for o in await ps.surfaced_person_offers(USER)]
        out += [o["offer_phrase"] for o in await ps.list_active(USER, "s1")]
        return "\n".join(out)

    def disable(self, monkeypatch):
        async def nothing_forgotten(_uid):
            return lambda *_t: False
        monkeypatch.setattr(self.ps, "_forgotten_filter", nothing_forgotten)

    async def teardown(self):
        await self.db.close()


class DayReview(Probe):
    async def setup(self, monkeypatch, request):
        import omi_day_review as dr
        from test_omi_day_review import REAL_FORGOTTEN
        self.dr = dr
        self.env = request.getfixturevalue("env")                      # migrations 0045 + 0054 on SQLite, get_compat_db patched
        monkeypatch.setattr(dr, "_forgotten", REAL_FORGOTTEN)          # the env fixture stubs the ledger out; the probe needs it

    async def seed(self):
        for i, (who, what) in enumerate(((FRIEND, "pick up the cake"), (KEEP, "return the ladder"))):
            self.env.db.conn.execute(
                "INSERT INTO omi_day_candidates (id, review_id, user_id, day_local, kind, payload, evidence, dedup_key, status, created_at) "
                "VALUES (?, 'r1', ?, '2026-11-04', 'follow_up', ?, ?, ?, 'pending', '2026-11-04T10:00:00Z')",
                (f"c{i}", USER, json.dumps({"text": f"Ask {who} to {what}"}),
                 json.dumps([{"line": i, "recording_id": "rec", "quote": f"{who} said {what}"}]), f"k{i}"))
        self.env.db.conn.commit()

    async def erase(self):
        await self.dr.erase_entity(USER, mf.name_pattern(FRIEND), strict=True)

    async def served(self):
        cands = await self.dr.list_candidates(USER, status="all")
        one = await self.dr.get_candidate(USER, "c0")
        return "\n".join(json.dumps(c) for c in cands + ([one] if one else []))

    def disable(self, monkeypatch):
        async def never(*_a, **_k):
            return False
        monkeypatch.setattr(self.dr, "_forgotten", never)


class SelfReview(Probe):
    """The nightly self-review's private records (~/.zoe/self-review/<night>.self-review.jsonl): the owner's flagged turns verbatim."""

    NIGHT = "2026-11-04"

    async def setup(self, monkeypatch, request):
        import self_review as sr
        self.sr = sr
        monkeypatch.setenv("ZOE_SELF_REVIEW_DIR", str(request.getfixturevalue("tmp_path") / "self-review"))

    async def seed(self):
        recs = [{"night": self.NIGHT, "turn_id": f"m{i}", "user_id": USER, "role": "owner", "policy": "full", "ts": 1.0e9 + i,
                 "channel": "voice", "flags": ["rephrase_retry"], "text": f"did {who} call about the {what}", "reply": "I could not find that.",
                 "review": {"cause": "the request was not understood", "fix": "router_training", "note": ""}}
                for i, (who, what) in enumerate(((FRIEND, "dinner"), (KEEP, "garage door")))]
        self.sr._write_private(self.sr.records_path(self.NIGHT), "".join(json.dumps(r) + "\n" for r in recs))

    async def erase(self):
        await self.sr.erase_entity(USER, mf.name_pattern(FRIEND), strict=True)

    async def served(self):
        return "\n".join(json.dumps(r) for r in await self.sr.load_reviews(self.NIGHT))

    def disable(self, monkeypatch):
        async def keep_all(_uid, rows, **_k):
            return list(rows), 0
        monkeypatch.setattr(mf, "keep_unforgotten", keep_all)


class NowCard(Probe):
    """The nightly per-member now card (``now_card``): a stored card whose date lines name FRIEND and KEEP. The erase is the real
    ``now_card.erase_entity`` (the memory_forget_entity handler's step); the serve is ``now_card._live``, the check every served card
    passes (strict ledger wall + mutes). ``disable`` switches the ledger wall off."""

    class _Store:
        def __init__(self):
            self.row = None

        async def read(self, uid):
            return self.row

        async def remove(self, uid):
            n, self.row = (1 if self.row else 0), None
            return n

    async def setup(self, monkeypatch, request):
        import now_card as nc
        import restraint
        self.nc = nc
        self.store = self._Store()
        prev = nc.set_store(self.store)
        monkeypatch.setattr(nc, "_store", self.store)
        self._prev = prev

        async def no_mutes(_uid):
            return []
        monkeypatch.setattr(restraint, "list_mutes_strict", no_mutes)
        self.monkeypatch = monkeypatch

    async def seed(self):
        lines = [["date", f"{FRIEND}'s birthday is on Sunday", "calendar:1"], ["date", f"{KEEP}'s dentist is on Friday", "calendar:2"]]
        card = {"lines": lines}
        self.store.row = (json.dumps(card), "\n".join(t for _k, t, _r in lines), "v1", "2026-11-05", "2026-11-05T03:00:00Z")

    async def erase(self):
        await self.nc.erase_entity(USER, FRIEND)

    async def served(self):
        if self.store.row is None:
            return f"(no card) {KEEP}"                     # rebuilt nightly from rows without the name
        lines = json.loads(self.store.row[0])["lines"]
        kept = await self.nc._live(USER, lines)
        return "\n".join(t for _k, t, _r in kept) or f"(no card) {KEEP}"

    def disable(self, monkeypatch):
        async def never(*_a, **_k):
            return False
        monkeypatch.setattr(self.nc, "_forgotten", never)

    async def teardown(self):
        self.nc.set_store(self._prev)


PROBES: dict[str, Callable[[], Probe]] = {
    "exact_words": ExactWords, "night_mind": NightMind, "digest_transcript": DigestTranscript, "memories_wall": MemoriesWall,
    "palace_reads": PalaceReads, "recall_packet": RecallPacket, "person_profile": PersonProfile,
    "relationship_graph": RelationshipGraph, "user_model_card": UserModelCard, "portrait": Portrait, "open_loops": OpenLoops,
    "proactive_candidates": ProactiveCandidates, "contact_offers": ContactOffers, "day_review": DayReview,
    "self_review": SelfReview, "now_card": NowCard,
}


async def _drive(probe: Probe, monkeypatch, request, arm: str) -> str:
    await probe.setup(monkeypatch, request)
    try:
        await probe.seed()
        assert _names(await probe.served(), FRIEND), "the seed never reached the layer: the probe would pass for the wrong reason"
        if arm == "control":
            probe.disable(monkeypatch)
        await _ledger()
        if arm == "forgotten":
            await probe.erase()
        return await probe.served()
    finally:
        await probe.teardown()


# the env fixture of the day-review suite, made available here (pytest resolves it by name)
from test_omi_day_review import env  # noqa: E402,F401


@pytest.mark.parametrize("layer", sorted(LAYERS))
def test_forgotten_the_name_is_served_by_no_layer(layer, monkeypatch, request):
    served = run(_drive(PROBES[layer](), monkeypatch, request, "forgotten"))
    assert not _names(served, FRIEND), f"{layer} serves a forgotten name after the forget: {served!r}"
    assert _names(served, KEEP), f"{layer} serves nothing at all - the probe cannot tell a wall from a broken layer: {served!r}"


@pytest.mark.parametrize("layer", sorted(LAYERS))
def test_erase_missed_the_serve_wall_is_what_the_table_says(layer, monkeypatch, request):
    """The erase did not land (a failed cascade step, a pending retry, a row derived afterwards). ``serve_wall=True``: the layer
    still withholds the name. ``serve_wall=False``: it serves it - a known single-wall layer (open-problems). Either way the
    recorded table must be true, so a wall added or lost shows up here."""
    served = run(_drive(PROBES[layer](), monkeypatch, request, "erase_missed"))
    if LAYERS[layer].serve_wall:
        assert not _names(served, FRIEND), f"{layer}: recorded as serve-walled, but it serves the forgotten name: {served!r}"
    else:
        assert _names(served, FRIEND), (f"{layer}: recorded as erase-only, but it now withholds the name on its own - "
                                        "flip serve_wall to True and drop it from the open-problems line")


@pytest.mark.parametrize("layer", sorted(LAYERS))
def test_control_the_layers_forget_hooks_off_the_name_is_served(layer, monkeypatch, request):
    """Negative control: the layer's serve wall disabled and its erase not run - the probe must SEE the leak."""
    served = run(_drive(PROBES[layer](), monkeypatch, request, "control"))
    assert _names(served, FRIEND), f"{layer}: with its forget hooks off the probe still sees no leak - the instrument is blind"


# ── the registry stays honest ────────────────────────────────────────────────

def test_every_layer_has_a_probe_and_nothing_is_both_probed_and_unprobed():
    assert set(PROBES) == set(LAYERS)
    assert not set(UNPROBED) & set(LAYERS)


def _open_problems_entry() -> str:
    text = (REPO / "docs" / "knowledge" / "open-problems.md").read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if "forget-completeness" in ln]
    assert lines, "docs/knowledge/open-problems.md has no forget-completeness entry"
    return "\n".join(lines)


def test_single_wall_and_unprobed_layers_are_ledgered_in_open_problems():
    entry = _open_problems_entry()
    missing = [k for k in sorted(UNPROBED) if f"`{k}`" not in entry]
    missing += [k for k, l in sorted(LAYERS.items()) if not l.serve_wall and f"`{k}`" not in entry]
    assert not missing, f"not in the open-problems forget-completeness entry: {missing}"
    stale = [k for k, l in LAYERS.items() if l.serve_wall and f"`{k}`" in entry]
    assert not stale, f"serve-walled now, drop from the open-problems entry: {stale}"


def test_now_card_is_probed_once_it_exists():
    """PR #2281 adds ``now_card.py`` (a nightly per-member card in the cached prefix). When it lands it must join ``LAYERS``."""
    if (SVC_DIR / "now_card.py").exists():
        assert "now_card" in LAYERS, "now_card.py exists: add a probe row (and drop it from UNPROBED)"
    else:
        assert "now_card" in UNPROBED


def test_member_register_gets_a_decision_once_it_exists():
    """PR #2297 adds ``member_register.py`` (a per-member speaking register after the cached prefix, with its own forget path in
    ``MemoryService.delete_user``). When it lands, decide here: a probe row if it can carry member words, else an ``UNPROBED`` reason."""
    if (SVC_DIR / "member_register.py").exists():
        assert "member_register" in LAYERS or "member_register" in UNPROBED, "member_register.py exists: probe it or ledger why not"


# ── narrower walls the table does not cover ──────────────────────────────────

def test_a_provenance_quote_is_vetted_on_its_excerpt_not_just_the_row_text(monkeypatch):
    """A row whose text does not name the friend but whose spoken excerpt does ("I told Dana I like pottery") is not quoted."""
    import provenance_answers as pa
    from memory_service import MemoryRef

    ref = MemoryRef(id="m1", text="User likes pottery.", metadata={
        "status": "approved", "user_id": USER, "source": "voice_fact", "source_excerpt": f"I told {FRIEND} I like pottery"})

    class _Svc:
        async def get(self, _id):
            return ref

    async def go():
        await _ledger()
        return await pa.evidence_for_row(USER, "m1", svc=_Svc())
    ev = run(go())
    assert ev.refusal == pa.FORGOTTEN_REPLY and not _names(ev.quote, FRIEND)
    # control: the row text alone (what was checked before) does not name the friend
    assert not _names(ref.text, FRIEND) and _names(pa._row_words(ref), FRIEND)


def test_a_person_fact_naming_a_forgotten_name_in_its_value_is_not_written(monkeypatch):
    """"Tove visits Dana every week": the fact is ABOUT Tove, but it names the forgotten friend - nothing is opened, nothing written."""
    import person_extractor as pe

    opened = []

    async def no_db(db):
        opened.append(db)
        return None, False
    monkeypatch.setattr(pe, "_ensure_db", no_db)

    async def go():
        await _ledger()
        named = await pe.apply_person_fact("Tove", "activity", f"visits {FRIEND} every week", user_id=USER, source="conversation")
        plain = await pe.apply_person_fact("Tove", "activity", "swims every week", user_id=USER, source="conversation")
        return named, plain
    named, plain = run(go())
    assert named is False and len(opened) == 1          # only the plain fact reached the database (control)
    assert plain is False                               # no database in this probe: nothing written either way


def test_contact_offers_fail_closed_on_a_ledger_outage(monkeypatch, request):
    """An unreadable ledger serves no contact offer (and stores none) - never one that may name a forgotten person."""
    probe = ContactOffers()

    async def go():
        await probe.setup(monkeypatch, request)
        try:
            await probe.seed()

            class _Down:
                async def active_hashes(self, *_a):
                    raise ConnectionError("pool lost")
            mf.set_backend(_Down())
            served = await probe.served()
            stored = await probe.ps.store_suggestions(USER, "s1", [{"action_type": "person_create", "offer_phrase": "Add Mo?",
                                                                   "description": "", "pre_filled_slots": {"name": "Mo"}}])
            return served, stored
        finally:
            await probe.teardown()
    served, stored = run(go())
    assert not _names(served, FRIEND) and not _names(served, KEEP) and stored == 0


def test_the_cached_night_mind_snapshot_rechecks_a_forget(monkeypatch, request):
    """Before: a snapshot cached before the forget served the quote for up to 90 s when the erase did not land."""
    probe = NightMind()

    async def go():
        await probe.setup(monkeypatch, request)
        try:
            await probe.seed()                                        # primes the cache
            memory_tombstones.add(USER, FRIEND)                       # the fast path only: no erase, no invalidate
            _t, obs = await probe.nm.snapshot(USER, use_cache=True, all_threads=True)
            return "\n".join(o["quote"] for o in obs)
        finally:
            memory_tombstones.clear_all()
            await probe.teardown()
    served = run(go())
    assert not _names(served, FRIEND) and _names(served, KEEP)
