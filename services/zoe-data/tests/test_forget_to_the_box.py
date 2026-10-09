"""Forgetting reaches the box: near spellings, the verbatim transcript, plaintext backups, resurrection.

The owner's rule (2026-10-06): forgotten means forever. Walls, each with its break-the-fix control (the docstring of the test says
what is broken to turn it red; ``tests/test_forget_to_the_box.py`` is run broken once per wall before the PR is opened):

  W1 near spellings   - the ledger matches <= 2 edits (7+ letters), <= 1 (5-6), none (<= 4); split spellings; accents; any script
  W2 the transcript   - chat_messages rows are redacted IN PLACE at forget time (user and assistant turns, titles), by the ledger
                        alone afterwards; a failure is not "forgotten"; the log line carries counts only
  W3 the backups      - an export is redacted by the ledger (no name needed), counts and ids unchanged, and still verifies
  W4 resurrection     - a digest / person / ingest writer cannot re-create the name (or its near spelling) after the 300 s tombstone
  W5 scripts          - ``redact_backups.py`` dry run / apply / refusal; the Flue store; the nightly export applies the ledger

Invented names only (the benchmark's world, the pilot's "Marisol"); no live store, no network, no model (``ci_safe``).
"""
from __future__ import annotations

import asyncio
import gzip
import importlib.util
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

import pytest

import forget_match as fm
import forget_redact
import intent_router
import memory_digest as md
import memory_forgotten as mf
import memory_tombstones
import person_extractor
from forgotten_support import (  # noqa: F401 - fixtures + the F3 inputs
    FRIEND, OTHER, USER, TombstoneClock, ledger_env, no_offers, open_forgotten_db, svc, use_db,
)

pytestmark = pytest.mark.ci_safe

NAME = "Marisol"
STT = ["Marisal", "Marysol", "Marizol", "Marisole", "Marissol", "Maricol", "Marisoul", "Marrisol"]      # the pilot's 8
SPLIT = ["Mari sol", "Mari-sol", "Maris ol"]                                                          # the pilot's 3
ACCENTED = ["Marisól", "MARÍSOL", "Marìsol"]
NEGATIVE = ["Marcel", "Marshal", "Marsala", "Martin", "Marketing"]                    # > 2 edits away


def lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


async def _forget(name: str = NAME, user: str = USER):
    return await intent_router.execute_intent(intent_router.Intent("memory_forget_entity", {"name": name}), user)


# ── W1: near spellings ───────────────────────────────────────────────────────

def _wanted(name: str, h=lambda s: s) -> set:
    joined = fm.joined_key(name)
    k = fm.max_edits(joined)
    return {h(f"near{k}|{key}") for key in fm.probe_keys(joined, k)}


@pytest.mark.parametrize("spelling", STT + SPLIT + ACCENTED)
def test_w1_every_pilot_spelling_and_an_accented_variant_is_a_near_span(spelling):
    text = f"so {spelling} is bringing the cake on Sunday"
    spans = fm.merge(fm.near_spans(text, lambda s: s, _wanted(NAME)))
    assert len(spans) == 1 and text[spans[0][0]:spans[0][1]] == spelling


@pytest.mark.parametrize("word", NEGATIVE)
def test_w1_negative_controls_more_than_two_edits_away_are_not(word):
    assert lev(fm.joined_key(NAME), fm.joined_key(word)) > 2
    assert fm.near_spans(f"so {word} is here", lambda s: s, _wanted(NAME)) == []


def test_w1_the_probe_match_is_exactly_levenshtein_on_a_vocabulary():
    """Not an approximation: over a vocabulary the near test equals ``lev <= k`` for every word (a plain deletion neighbourhood
    over-matched 3x here: 'percival' vs 'special')."""
    vocab = ("marisol marisal marsol marasol maroisol marisolx marisoll amrisol rmaisol marisolle mariosl special percival perceive "
             "imperial priam privy praia pricy bristol marilyn martins marshal mariano dentist tomorrow wednesday marisa maria "
             "marisoles marisoleo marsolis smarisol").split()
    for name in ("marisol", "percival", "priya"):
        k = fm.max_edits(name)
        wanted = _wanted(name)
        for w in vocab:
            assert fm.near_hit(w, 0, lambda s: s, wanted) == (lev(name, w) <= k), (name, w)


def test_w1_a_split_spelling_is_one_edit_tighter():
    """'Mar is sold' joins to 'marissold': 2 edits from 'marisol' - fine for one token, too far for a three-token split."""
    assert fm.near_spans("Mar is sold", lambda s: s, _wanted(NAME)) == []
    assert fm.near_spans("Marisold", lambda s: s, _wanted(NAME))


def test_w1_a_short_name_gets_no_edits_but_an_accent_still_folds():
    wanted = _wanted("Dana")
    assert not fm.near_spans("Dane Dina Dan Danny Diana", lambda s: s, wanted)
    assert fm.near_spans("Daná rang", lambda s: s, wanted)                 # distance 0 after folding
    assert [fm.max_edits(fm.joined_key(n)) for n in ("Leo", "Dana", "Priya", "Teodor", "Marisol")] == [0, 0, 1, 1, 2]


def test_w1_cjk_by_codepoint_a_name_inside_a_run_and_one_edit():
    name = "マリソル"                                                          # 4 kana, no spaces in the sentence
    run = "今日マリゾルに会ったよ"                                              # one codepoint different (voiced)
    assert fm.max_edits(fm.joined_key(name)) == 1
    spans = fm.near_spans(run, lambda s: s, _wanted(name))
    assert spans and any("マリゾル" in run[s:e] or run[s:e] in "マリゾル" for s, e in spans)
    assert not fm.near_spans("今日マリオネットで遊んだ", lambda s: s, _wanted(name))             # a different word
    assert not fm.near_spans("今日ソリマルに会った", lambda s: s, _wanted(name))                  # 3 edits
    hangul = "마리솔"
    assert fm.near_spans("오늘 마리소를 만났다", lambda s: s, _wanted(hangul))                   # Hangul, one substitution


@pytest.mark.asyncio
async def test_w1_the_ledger_catches_the_spellings_on_the_write_guards_only(ledger_env):
    await mf.add(USER, NAME, actor=USER)
    for spelling in STT + SPLIT + ACCENTED:
        assert await mf.matches(USER, f"{spelling} rang about lunch", near=True), spelling
        assert not await mf.matches(USER, f"{spelling} rang about lunch"), spelling      # reads stay exact: nothing is hidden unasked
    for word in NEGATIVE:
        assert not await mf.matches(USER, f"{word} rang about lunch", near=True), word
    assert await mf.matches(USER, "okay Marisol rang")                                       # the exact name, either way
    assert not await mf.matches(OTHER, "Marisal rang", near=True)                           # another member's ledger is untouched
    dump = " ".join(str(v) for r in ledger_env.rows.values() for v in r.values()).lower()
    assert "maris" not in dump and "marys" not in dump and "mari" not in dump               # hashes only, probes included


@pytest.mark.asyncio
async def test_w1_cjk_exact_in_a_run_and_name_pattern(ledger_env):
    await mf.add(USER, "山田太郎", actor=USER)
    assert await mf.matches(USER, "昨日山田太郎さんに会った")                                   # a name inside a run: a window, not a word
    assert not await mf.matches(USER, "昨日田中花子さんに会った")
    assert mf.name_pattern("山田太郎").search("昨日山田太郎さんに会った")
    assert not mf.name_pattern("Dana").search("Danaë and Dananah")                               # Latin keeps its word boundaries


@pytest.mark.asyncio
async def test_w1_a_declined_spelling_passes_and_a_release_drops_the_probes(ledger_env):
    await mf.add(USER, NAME, actor=USER)
    assert await mf.matches(USER, "Marisa rang", near=True)                                  # 2 edits away: held, not erased
    assert await mf.add_distinct(USER, "Marisa")                                              # the owner: "no, that is someone else"
    assert not await mf.matches(USER, "Marisa rang", near=True)
    assert await mf.matches(USER, "Marisal rang", near=True)                                  # only the declined spelling passes
    assert await mf.release(USER, "remember that Marisol lives in Lisbon") == 1
    assert not await mf.matches(USER, "Marisal rang", near=True) and not await mf.matches(USER, "Marisol rang")
    assert not [r for r in ledger_env.rows.values() if r["scope"] == mf.SCOPE_NEAR and r["key_hash"] != mf._Hasher(USER).hash(mf._NEAR_FLAG)]


# ── W4: resurrection ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_w4_after_the_tombstone_a_digest_cannot_recreate_the_name_or_its_spelling(svc, monkeypatch, no_offers):
    clock = TombstoneClock(monkeypatch)
    await svc.ingest(f"User's friend {NAME} lives in Hobart.", user_id=USER, source="voice_fact", confidence=0.9)
    await _forget()
    clock.advance(360)
    assert memory_tombstones.matching_tombstone(USER, f"{NAME} rang") is None            # the 300 s tombstone is gone
    for spelling in (NAME, "Marisal", "Mari sol", "Marisól"):
        assert await svc.ingest(f"User's friend {spelling} is visiting.", user_id=USER, source="digest",
                                anchor_text=f"okay so {spelling} rang about the weekend") is None, spelling
    assert await svc.ingest("User's friend Marcel is visiting.", user_id=USER, source="digest",
                            anchor_text="okay so Marcel rang") is not None                # a different name is stored
    # the person's own explicit teach beats the guard (a verified re-teach releases the entry)
    for spelling in ("Marisal", "Mari sol"):          # ... with no anchor turn either: the proposed fact itself names the spelling
        assert await svc.ingest(f"User's friend {spelling} is visiting at the weekend.", user_id=USER, source="digest") is None, spelling
    assert await svc.ingest(f"User's friend {NAME} lives in Lisbon.", user_id=USER, source="voice_fact", confidence=0.9) is not None


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows


class _TranscriptDb:
    def __init__(self, turns):
        self.turns = turns

    async def execute(self, sql, params=()):
        return _Cur([(t,) for t in self.turns])


@pytest.mark.asyncio
async def test_w4_the_nightly_transcript_loader_holds_a_near_spelling_out(ledger_env):
    await mf.add(USER, NAME, actor=USER)
    kept = await md._load_todays_messages(USER, _TranscriptDb(["Marisal rang about the lift", "the dentist is on Elm Street"]))
    assert "Marisal" not in kept and "dentist" in kept


@pytest.mark.asyncio
async def test_w4_a_person_is_not_recreated_from_an_inferred_mention(ledger_env, monkeypatch):
    await mf.add(USER, NAME, actor=USER)

    async def no_db(_db):
        raise AssertionError("the person extractor reached the database for a forgotten name")
    monkeypatch.setattr(person_extractor, "_ensure_db", no_db)
    assert await person_extractor.apply_person_fact("Marisal", "work", "works at the bakery", user_id=USER, source="conversation") is False
    assert await person_extractor.process_text("Marisol works at the bakery", user_id=USER) == 0
    with pytest.raises(AssertionError):                                                    # a name nobody forgot does reach it
        await person_extractor.apply_person_fact("Marcel", "work", "works at the bakery", user_id=USER, source="conversation")


# ── W2: the transcript ───────────────────────────────────────────────────────

async def _seed_chat(db):
    await db.executescript("""
        INSERT INTO chat_sessions (id, user_id, title) VALUES ('s1', 'demo_bar_00000001', 'Lunch with Marisol'),
                                                              ('s2', 'demo_bar_00000002', 'Lunch with Marisol');
        INSERT INTO chat_messages (id, session_id, role, content, metadata, created_at) VALUES
          ('m1', 's1', 'user', 'okay so Marisol rang and Marisol is coming', NULL, '2026-10-09 10:00:00'),
          ('m2', 's1', 'assistant', 'Great, say hi to marisol for me.', '{"note": "about Marisol"}', '2026-10-09 10:00:05'),
          ('m3', 's1', 'user', 'the dentist is on Elm Street', NULL, '2026-10-09 10:01:00'),
          ('m4', 's1', 'user', 'Marisal sent the photos', NULL, '2026-10-09 10:02:00'),
          ('m5', 's2', 'user', 'my own friend Marisol is different', NULL, '2026-10-09 10:03:00');
    """)
    await db.commit()


async def _chat(db):
    cur = await db.execute("SELECT id, session_id, role, content, metadata, created_at FROM chat_messages ORDER BY id")
    return {r[0]: tuple(r) for r in await cur.fetchall()}


@pytest.fixture
async def chat_db(monkeypatch):
    db = await open_forgotten_db()
    use_db(monkeypatch, db)
    await _seed_chat(db)
    yield db
    await db.close()


@pytest.mark.asyncio
async def test_w2_a_forget_redacts_the_owners_transcript_in_place(svc, no_offers, chat_db, caplog):
    """Break: drop the ``forget_redact.on_forget`` call in ``memory_forget_entity`` -> the rows keep the name."""
    caplog.set_level(logging.INFO)
    before = await _chat(chat_db)
    await _forget()
    after = await _chat(chat_db)
    assert after["m1"][3] == "okay so [forgotten] rang and [forgotten] is coming"             # the span, never the message
    assert after["m2"][3] == "Great, say hi to [forgotten] for me." and "forgotten" in after["m2"][4] and "Marisol" not in after["m2"][4]
    assert after["m3"] == before["m3"]                                                       # nothing else touched
    assert after["m4"][3] == before["m4"][3]                                                 # a near spelling is the owner's call, not rewritten
    assert after["m5"] == before["m5"]                                                       # another member's words about their own Marisol
    for k in before:                                                                          # ids, sessions, roles and timestamps keep
        assert (before[k][0], before[k][1], before[k][2], before[k][5]) == (after[k][0], after[k][1], after[k][2], after[k][5])
    titles = {r[0]: r[1] for r in await (await chat_db.execute("SELECT id, title FROM chat_sessions")).fetchall()}
    assert titles == {"s1": "Lunch with [forgotten]", "s2": "Lunch with Marisol"}
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("FORGET_REDACT")]
    assert line == ["FORGET_REDACT user=demo_bar_00000001 rows=3 spans=5"]                    # counts only
    assert "marisol" not in caplog.text.lower().replace("lunch with", "")


@pytest.mark.asyncio
async def test_w2_a_redaction_that_fails_is_not_a_confirmed_forget(svc, no_offers, chat_db, monkeypatch):
    async def boom(*_a, **_k):
        raise ValueError("db down")
    monkeypatch.setattr(forget_redact, "redact_transcripts", boom)
    reply = await _forget()
    assert "can't say it's forgotten yet" in reply and "Marisol" not in reply
    assert await mf.matches(USER, "Marisol rang")                                              # the ledger still holds it


@pytest.mark.asyncio
async def test_w2_the_nightly_sweep_redacts_by_the_ledger_alone(ledger_env, chat_db, monkeypatch):
    """No name anywhere: a row saved AFTER the forget (the assistant's reply) is redacted from the ledger's hashes; a near spelling is not."""
    await mf.add(USER, NAME, actor=USER)
    await chat_db.execute("INSERT INTO chat_messages (id, session_id, role, content, created_at) VALUES "
                          "('m6', 's1', 'assistant', 'I have forgotten Marisol.', datetime('now'))")
    await chat_db.commit()
    since_hours = 24 * 365 * 10
    rows, spans = await forget_redact.sweep_transcripts(USER, hours=since_hours, db=chat_db)
    got = await _chat(chat_db)
    assert (rows, spans) == (3, 4) and got["m6"][3] == "I have forgotten [forgotten]."
    assert got["m4"][3] == "Marisal sent the photos" and got["m5"][3] == "my own friend Marisol is different"
    assert (await forget_redact.sweep_transcripts(USER, hours=since_hours, db=chat_db)) == (0, 0)      # idempotent
    await chat_db.execute("INSERT INTO chat_messages (id, session_id, role, content, created_at) VALUES "
                          "('m7', 's1', 'assistant', 'and again Marisol', datetime('now'))")
    await md._load_todays_messages(USER, chat_db)                                             # the digest reads the day: the hook redacts first
    assert (await _chat(chat_db))["m7"][3] == "and again [forgotten]"


def test_w2_the_in_process_turn_marks_are_dropped():
    import exact_words
    import time
    exact_words._marks[USER] = ("what did I say about Marisol", time.monotonic())
    assert forget_redact.clear_turn_caches(USER) >= 1 and USER not in exact_words._marks


# ── W3: the backups ──────────────────────────────────────────────────────────

def _export_payload():
    docs = {"a": f"User's friend {NAME} lives in Hobart.", "b": "the dentist is on Elm Street", "c": "Marisal brings the cake",
            "d": f"User's friend {NAME} is my own"}
    recs = [{"id": "a", "document": docs["a"], "metadata": {"user_id": USER, "note": f"about {NAME}"}},
            {"id": "b", "document": docs["b"], "metadata": {"user_id": USER}},
            {"id": "c", "document": docs["c"], "metadata": {"wing": USER}},
            {"id": "d", "document": docs["d"], "metadata": {"user_id": OTHER}}]
    return {"exported_at": "2026-10-09T00:00:00Z", "total_records": 4, "collection_counts": {"mempalace_drawers": 4},
            "collections": {"mempalace_drawers": recs}}


@pytest.mark.asyncio
async def test_w3_an_export_is_redacted_by_the_ledger_with_counts_and_ids_unchanged(ledger_env):
    """Break: have ``redact_export`` return without touching the records -> the name stays in the file."""
    await mf.add(USER, NAME, actor=USER)
    payload = _export_payload()
    red = await forget_redact.load_redactor(near=False)
    assert forget_redact.verify_export(payload) == []
    assert forget_redact.redact_export(payload, red) == 2
    recs = {r["id"]: r for r in payload["collections"]["mempalace_drawers"]}
    assert recs["a"]["document"] == "User's friend [forgotten] lives in Hobart." and recs["a"]["metadata"]["note"] == "about [forgotten]"
    assert recs["c"]["document"] == "Marisal brings the cake"                                  # a near spelling: left for the owner
    assert recs["d"]["document"].count(NAME) == 1                                              # another member's record: their ledger
    assert forget_redact.verify_export(payload) == [] and payload["total_records"] == 4        # the backup-verify check still passes
    dump = {"ids": ["a", "b"], "documents": [f"x {NAME}", "y"], "metadatas": [{"user_id": USER}, {"user_id": USER}], "embeddings": [[0.1], [0.2]]}
    assert forget_redact.redact_export(dump, red) == 1 and dump["documents"][0] == "x [forgotten]" and forget_redact.verify_export(dump) == []
    assert forget_redact.verify_export({"total_records": 9, "collections": {"c": []}, "collection_counts": {"c": 0}})   # a broken export is caught


@pytest.mark.asyncio
async def test_w3_the_redactor_without_a_salt_refuses(monkeypatch):
    monkeypatch.delenv(mf.SALT_ENV, raising=False)
    with pytest.raises(RuntimeError):
        await forget_redact.load_redactor()


# ── W5: the scripts ──────────────────────────────────────────────────────────

REPO = Path(__file__).resolve().parents[3]


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / "maintenance" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.asyncio
async def test_w5_redact_backups_dry_run_apply_and_the_refusal(ledger_env, tmp_path):
    """Break: make ``redact_file`` write before verifying -> the unverifiable file is rewritten."""
    await mf.add(USER, NAME, actor=USER)
    rb = _script("redact_backups")
    red = await forget_redact.load_redactor(near=False)
    gz, plain, broken = tmp_path / "memory-export-1.json.gz", tmp_path / "x.json", tmp_path / "broken.json"
    gz.write_bytes(gzip.compress(json.dumps(_export_payload()).encode()))
    plain.write_text(json.dumps({"ids": ["a"], "documents": [f"hello {NAME}"], "metadatas": [{"user_id": USER}]}))
    bad = _export_payload()
    bad["total_records"] = 99
    broken.write_text(json.dumps(bad))
    snap = {p: p.read_bytes() for p in (gz, plain, broken)}
    assert rb.redact_file(gz, red, apply=False)["spans"] == 2 and gz.read_bytes() == snap[gz]          # dry run changes nothing
    r = rb.redact_file(gz, red, apply=True)
    assert r == {"file": gz.name, "spans": 2, "changed": True, "verify": "ok"}
    assert gzip.decompress(gz.read_bytes()).decode().count(NAME) == 1 and oct(gz.stat().st_mode & 0o777) == "0o600"
    assert rb.redact_file(plain, red, apply=True)["changed"] and "[forgotten]" in plain.read_text()
    r = rb.redact_file(broken, red, apply=True)
    assert r["changed"] is False and "total_records" in r["verify"] and broken.read_bytes() == snap[broken]   # never rewritten if it would not verify
    assert rb.redact_file(gz, red, apply=True)["spans"] == 0                                    # idempotent


@pytest.mark.asyncio
async def test_w5_the_flue_store_is_redacted_json_aware(ledger_env, tmp_path):
    await mf.add(USER, NAME, actor=USER)
    rb = _script("redact_backups")
    red = await forget_redact.load_redactor(near=False)
    db = tmp_path / "zoe-brain.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE flue_conversation_stream_batches (path TEXT, seq INTEGER, data TEXT)")
    con.execute("CREATE TABLE flue_conversation_fold_checkpoints (path TEXT PRIMARY KEY, data TEXT)")
    msg = json.dumps([{"role": "user", "content": f"tell \"{NAME}\" hi\n"}, {"role": "assistant", "content": "ok"}])
    con.execute("INSERT INTO flue_conversation_stream_batches VALUES ('p', 1, ?)", (msg,))
    con.execute("INSERT INTO flue_conversation_fold_checkpoints VALUES ('p', ?)", (msg,))
    con.commit()
    con.close()
    assert rb.redact_flue_db(db, red, apply=False)["spans"] == 2 and NAME in sqlite3.connect(db).execute("SELECT data FROM flue_conversation_stream_batches").fetchone()[0]
    assert rb.redact_flue_db(db, red, apply=True)["spans"] == 2
    cell = sqlite3.connect(db).execute("SELECT data FROM flue_conversation_stream_batches").fetchone()[0]
    assert json.loads(cell)[0]["content"] == 'tell "[forgotten]" hi\n' and NAME not in cell
    assert rb.main(["--flue-db", str(db), "--apply", "--dir", str(tmp_path)]) == 2             # the brain must be stopped first


def _chroma_like(path: Path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE collections (id TEXT, name TEXT);
        CREATE TABLE segments (id TEXT, scope TEXT, collection TEXT);
        CREATE TABLE embeddings (id INTEGER PRIMARY KEY, segment_id TEXT, embedding_id TEXT, seq_id INTEGER);
        CREATE TABLE embedding_metadata (id INTEGER, key TEXT, string_value TEXT, int_value INTEGER, float_value REAL, bool_value INTEGER);
        INSERT INTO collections VALUES ('c1', 'mempalace_drawers');
        INSERT INTO segments VALUES ('sg', 'METADATA', 'c1');
        INSERT INTO embeddings VALUES (1, 'sg', 'a', 1);
        INSERT INTO embedding_metadata VALUES (1, 'chroma:document', 'User''s friend Marisol lives in Hobart.', NULL, NULL, NULL);
        INSERT INTO embedding_metadata VALUES (1, 'user_id', 'demo_bar_00000001', NULL, NULL, NULL);
    """)
    con.commit()
    con.close()


@pytest.mark.asyncio
async def test_w5_the_nightly_export_applies_the_ledger_before_writing(ledger_env, tmp_path, monkeypatch):
    """Break: skip ``_apply_forget_ledger`` in ``export`` -> the written file names her."""
    await mf.add(USER, NAME, actor=USER)
    ex = _script("export_memory_store")
    chroma = tmp_path / "chroma.sqlite3"
    _chroma_like(chroma)
    red = await forget_redact.load_redactor(near=False)

    async def _noop():
        return None

    async def _red(**_k):
        return red
    import db_pool
    monkeypatch.setattr(db_pool, "init_pool", _noop)
    monkeypatch.setattr(db_pool, "close_pool", _noop)
    monkeypatch.setattr(forget_redact, "load_redactor", _red)
    out = await asyncio.to_thread(ex.export, str(chroma), str(tmp_path / "out"), compress=False, keep=0)   # it runs its own event loop
    written = json.loads(Path(out).read_text())
    assert written["forget_ledger"] == "applied" and written["forget_spans_redacted"] == 1
    assert NAME not in Path(out).read_text() and written["total_records"] == 1 and forget_redact.verify_export(written) == []

    async def _down(**_k):
        raise RuntimeError("no database")
    monkeypatch.setattr(forget_redact, "load_redactor", _down)       # the ledger cannot load: the backup is still made, and says so
    out2 = await asyncio.to_thread(ex.export, str(chroma), str(tmp_path / "out2"), compress=False, keep=0)
    assert json.loads(Path(out2).read_text())["forget_ledger"].startswith("skipped") and NAME in Path(out2).read_text()
