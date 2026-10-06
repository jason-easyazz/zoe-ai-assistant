"""ZMB bake-off: the Hindsight arms H0 / H1 / H2 (``scripts/perf/zmb/arms/hindsight.py``) against a FAKE Hindsight HTTP server.

Slim-lane safe: ``FakeHindsight`` is a transport double (no socket, no Postgres, no brain, no embedder) that speaks the documented
request / response shapes of the endpoints the adapter uses. It makes NO claim about Hindsight's extraction or retrieval quality; what it
proves is the adapter's CONTRACT and the Zoe layer around it, red-before-green: every protection is switched off one at a time and the cell
that claims it must go red; H0 (no layer) must be red where the decision record says Hindsight natively is.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "perf"))

from zmb import cells as cellmod, spec  # noqa: E402
from zmb.arms import ARMS, make_arm  # noqa: E402
from zmb.arms import hindsight as hs  # noqa: E402
from zmb.arms.base import Turn  # noqa: E402
from zmb.arms.fake_hindsight import FakeHindsight  # noqa: E402
from zmb.world import BASELINE_SEED, make_world  # noqa: E402

CELLS = {c.id: c for c in spec.load_cells()}
WORLD = make_world(BASELINE_SEED)
USER = "demo_bar_1a2b3c4d"
RANK = {"user_stated": 4, "user_stated_derived": 3, "user_unverified": 2, "model_from_turn": 1, "model_from_transcript": 0}


def mk(variant="H1", **kw):
    fake = FakeHindsight()
    kw.setdefault("settle_poll_s", 0)
    arm = hs.HindsightArm(variant, transport=fake, **kw)
    return arm, fake


def run(arm, cid):
    return cellmod.run_cell(CELLS[cid].rendered(WORLD), WORLD, arm)


def verdict(variant, cid, **kw):
    arm, _f = mk(variant, **kw)
    try:
        return run(arm, cid).verdict
    finally:
        arm.close()


def texts(arm, *statuses):
    return [r["text"] for r in arm.stats()["rows"] if not statuses or r["status"] in statuses]


def posts(fake, suffix):
    return [b for m, p, b in fake.bodies if m == "POST" and p.endswith(suffix)]


# ── the contract: banks, config, tags, shapes ────────────────────────────────

def test_the_arms_are_registered_as_implemented_not_stubs():
    for name in ("H0", "H1", "H2"):
        assert "STUB" not in ARMS[name] and make_arm(name).name == name
    assert set(hs.VARIANTS) == {"H0", "H1", "H2"}


def test_one_bank_per_user_with_the_variants_config():
    for variant, want in (("H0", {"retain_extraction_mode": "concise", "enable_observations": True, "enable_auto_consolidation": True}),
                          ("H1", {"retain_extraction_mode": "verbatim", "enable_observations": False}),
                          ("H2", {"retain_extraction_mode": "concise", "enable_observations": True, "enable_auto_consolidation": False})):
        arm, fake = mk(variant)
        arm.reset(USER)
        bank = f"zmb-{variant.lower()}-{USER}"
        assert bank in fake.banks and fake.banks[bank]["config"].items() >= want.items()
        assert fake.banks[bank]["config"]["enable_reranking"] is False             # the reranker is off in every arm (the slim wheel has none)
        patch = [b for m, p, b in fake.bodies if m == "PATCH" and p.endswith("/config")][-1]
        assert set(patch) == {"updates"}                                           # the documented BankConfigUpdate shape
        arm.close()
        assert bank not in fake.banks                                              # nothing left behind in the scratch DB


def test_reset_refuses_a_non_demo_identity():
    arm, _f = mk()
    for bad in ("jason", "guest", "demo_bar_XYZ", "demo_bar_1a2b3c4"):
        with pytest.raises(ValueError, match="non-demo"):
            arm.reset(bad)


def test_retain_carries_the_provenance_class_in_tags_and_the_documented_item_shape():
    arm, fake = mk("H1")
    arm.reset(USER)
    arm.ingest([Turn("User lives in Perth.", "owner_taught")])
    body = posts(fake, "/memories")[-1]
    item = body["items"][0]
    assert body["async"] is False and set(item) >= {"content", "document_id", "tags"}
    assert {"user:" + USER, "class:user_stated", "origin:voice_fact"} <= set(item["tags"])
    assert item["content"] == "User lives in Perth."                              # verbatim: the stored text is the user's own words
    row = arm.stats()["rows"][0]
    assert (row["status"], row["authority_class"], row["origin"], row["user_id"]) == ("approved", "user_stated", "voice_fact", USER)


def test_h0_has_no_zoe_layer_so_no_tags_and_no_side_table():
    arm, fake = mk("H0")
    arm.reset(USER)
    arm.ingest([Turn("User lives in Perth.", "owner_taught")])
    item = posts(fake, "/memories")[-1]["items"][0]
    assert "tags" not in item and arm.layer is None
    assert arm.stats()["rows"][0]["authority_class"] == ""


def test_recall_is_scoped_to_the_user_tag_and_ordered_by_authority():
    arm, fake = mk("H1", off=frozenset({"authority"}))      # wall off so the model's near-duplicate is retained beside the owner's fact
    arm.reset(USER)
    arm.ingest([Turn("x", "system_writer", writer="digest", proposes=("User likes tea a lot.",)), Turn("User likes tea.", "owner_taught")])
    rows = arm.recall("what does the user like tea", 5)
    req = posts(fake, "/memories/recall")[-1]
    assert req["tags"] == ["user:" + USER] and req["tags_match"] == "all_strict"      # all_strict: never another member's memory
    ranks = [RANK[r["authority_class"]] for r in rows]
    assert len(rows) == 2 and ranks == sorted(ranks, reverse=True) and rows[0]["authority_class"] == "user_stated"


def test_as_of_is_not_implemented_with_the_reason():
    arm, _f = mk()
    with pytest.raises(NotImplementedError, match="temporal_window"):
        arm.as_of("where does he live", "2026-01-01T00:00:00Z")


def test_idle_pass_clock_and_capabilities():
    arm, _f = mk("H1")
    assert {"clock", "idle_pass", "identities", "reader"} <= arm.capabilities
    arm.reset(USER)
    out = arm.run_idle_pass("okay so uh Dev came up", ["User's name is Dev."])
    assert out["retained"] == 0 and out["refused"] == 0 and "skipped_reason" not in out and "error" not in out   # the pass RAN; the candidate is pending
    arm.advance_clock(360)                                                       # virtual: the ledger is permanent, nothing to expire


# ── the server is not there / is wrong ───────────────────────────────────────

def test_no_server_is_a_skip_never_a_pass():
    arm, fake = mk("H1")
    fake.down = True
    o = run(arm, "A1.digest.home")
    assert o.verdict == "SKIP"
    stub = hs.HindsightArm("H1", base_url="http://127.0.0.1:9")                  # the discard port: nothing listens
    o2 = cellmod.run_cell(CELLS["A1.digest.home"].rendered(WORLD), WORLD, stub)
    assert o2.verdict == "SKIP" and "bakeoff_window.sh" in o2.reason


def test_a_non_loopback_url_is_refused_at_construction():
    for url in ("http://10.0.0.5:18888", "https://api.openai.com", "http://hindsight.example.com"):
        with pytest.raises(hs.HindsightUnavailable, match="loopback"):
            hs.HindsightClient(url)
    assert hs.HindsightClient("http://127.0.0.1:18888").base_url.endswith(":18888")


def test_a_server_error_on_retain_is_loud_and_counted_against_validity():
    arm, fake = mk("H0")
    arm.reset(USER)
    fake.fail_retain = 1
    with pytest.raises(hs.HindsightError):
        arm.ingest([Turn("User lives in Perth.", "owner_taught")])
    arm.ingest([Turn("User lives in Hobart.", "owner_taught")])
    m = arm.stats()["measure"]
    assert (m["retain_calls"], m["retain_failures"], m["retain_ok_rate"]) == (2, 1, 0.5)
    assert m["egress"]["non_loopback_requests"] == 0 and m["http_errors"] >= 1


def test_the_measure_block_reports_latency_and_the_rss_probe():
    arm, _f = mk("H1", rss_probe=lambda: {"steady_mb": 321})
    arm.reset(USER)
    arm.ingest([Turn("User likes tea.", "owner_taught")])
    for q in ("tea", "likes", "user"):
        arm.recall(q, 3)
    m = arm.stats()["measure"]
    assert m["recall_n"] == 3 and m["recall_p95_ms"] >= m["recall_p50_ms"] >= 0 and m["rss"] == {"steady_mb": 321}
    assert m["retain_p50_ms"] >= 0 and m["http_calls"] > 5


def test_the_real_urllib_path_works_over_loopback():
    fake = FakeHindsight()
    try:
        srv = fake.serve()
    except OSError:
        pytest.skip("cannot bind a loopback socket here")
    try:
        arm = hs.HindsightArm("H1", base_url=f"http://127.0.0.1:{srv.server_address[1]}", settle_poll_s=0)
        arm.reset(USER)
        arm.ingest([Turn("User lives in Perth.", "owner_taught")])
        assert texts(arm, "approved") == ["User lives in Perth."]
        assert arm.recall("where does the user live", 3)[0]["text"] == "User lives in Perth."
        arm.close()
    finally:
        srv.shutdown()
        srv.server_close()


# ── red before green: H0 is red where Hindsight is natively red ──────────────

@pytest.mark.parametrize("cid", ["A1.digest.name", "A1.digest.home", "A1.mcp.pet", "A1.decay_sweep.work", "H1.digest", "F2.late_writer.digest",
                                 "E1.question_is_not_a_fact", "G3.consent.guest"])
def test_h0_without_the_zoe_layer_is_red_and_h1_with_it_is_green(cid):
    assert verdict("H0", cid) == "FAIL", f"{cid}: Hindsight alone must not pass the hard cell"
    assert verdict("H1", cid) == "PASS", f"{cid}: Hindsight + the Zoe layer must pass it"


def test_h0_s1_signature_the_owners_observation_is_replaced_by_the_newest_evidence():
    """Native consolidation: 'PREFER UPDATE OVER CREATE'. The owner's belief is invalidated by a later model fact (incident S1, one layer up)."""
    arm, _f = mk("H0")
    arm.reset(USER)
    arm.ingest([Turn("User lives in Perth.", "owner_taught")])
    arm.ingest([Turn("x", "system_writer", writer="digest", proposes=("User lives in Hobart.",))])
    assert any(r["status"] == "superseded" and "Perth" in r["text"] for r in arm.stats()["rows"])     # the owner's belief was retired by a model


def test_each_zoe_protection_has_a_control_that_turns_its_cell_red():
    claims = {"authority": "A1.digest.home", "identity": "H1.digest", "ledger": "F2.late_writer.digest"}
    for feature, cid in claims.items():
        assert verdict("H1", cid) == "PASS"
        assert verdict("H1", cid, off=frozenset({feature})) == "FAIL", f"switching {feature} OFF must turn {cid} red"
    assert verdict("H1", "G3.consent.guest") == "PASS"
    assert verdict("H1", "G3.consent.guest", off=frozenset({"guest", "affect"})) == "FAIL"       # two layers (the sentinel + the consent gate)
    assert verdict("H2", "E1.question_is_not_a_fact") == "PASS"
    assert verdict("H2", "E1.question_is_not_a_fact", off=frozenset({"gate"})) == "FAIL"          # the raw turn is gated before the model reads it
    with pytest.raises(ValueError, match="unknown Zoe-layer control"):
        mk("H1", off=frozenset({"typo"}))


def test_the_target_f3_passes_on_a_durable_ledger_and_fails_without_one():
    assert verdict("H1", "F3.after_tombstone_ttl") == "PASS"             # an improvement over Z0 (a known failure there)
    assert verdict("H1", "F3.after_tombstone_ttl", off=frozenset({"ledger"})) == "FAIL"
    assert verdict("H0", "F3.after_tombstone_ttl") == "FAIL"


def test_the_h2_fence_isolates_authority_scopes_even_when_the_wall_is_off():
    """Wall OFF: the model's fact IS retained, but under ITS OWN class scope, so consolidation (all_strict tag isolation) never updates the owner's
    observation: the owner's belief survives. H0 has no scopes: the owner's belief is retired."""
    for variant, kw, owner_survives in (("H2", {"off": frozenset({"authority"})}, True), ("H0", {}, False)):
        arm, _f = mk(variant, **kw)
        arm.reset(USER)
        arm.ingest([Turn("User lives in Perth.", "owner_taught")])
        arm.ingest([Turn("x", "system_writer", writer="digest", proposes=("User lives in Hobart.",))])
        survived = not any(r["status"] == "superseded" and "Perth" in r["text"] for r in arm.stats()["rows"])
        assert survived is owner_survives, variant
        arm.close()
    arm, fake = mk("H2")
    arm.reset(USER)
    arm.ingest([Turn("User lives in Perth.", "owner_taught")])
    assert posts(fake, "/consolidate")[-1]["observation_scopes"] == [["user:" + USER, "class:user_stated"]]     # consolidation per authority scope
    assert posts(fake, "/memories")[-1]["items"][0]["observation_scopes"] == [["user:" + USER, "class:user_stated"]]


# ── the Zoe layer: authority, held-back candidates, identity, affect ─────────

def test_a_contradiction_from_a_model_is_held_back_disputed_not_lost_and_not_recalled():
    arm, _f = mk("H1")
    arm.reset(USER)
    arm.ingest([Turn("User lives in Perth.", "owner_taught")])
    rep = arm.ingest([Turn("x", "system_writer", writer="digest", proposes=("User lives in Hobart.",))])
    assert rep.refused == 1 and rep.written == 0
    rows = arm.stats()["rows"]
    held = [r for r in rows if r["status"] == "disputed"]
    owner = [r for r in rows if r["status"] == "approved"]
    assert len(held) == 1 and "Hobart" in held[0]["text"] and held[0]["contradicts_id"] == owner[0]["id"]
    assert all("Hobart" not in r["text"] for r in arm.recall("where does the user live", 5))
    assert verdict("H1", "A5.digest.home") == "PASS"


def test_an_edit_by_a_model_is_refused_but_the_owners_own_edit_wins():
    arm, _f = mk("H1")
    arm.reset(USER)
    arm.ingest([Turn("User lives in Perth.", "owner_taught")])
    rep = arm.ingest([Turn("x", "system_writer", writer="digest", proposes=("User lives in Hobart.",), op="edit", attr="home")])
    assert rep.refused == 1 and rep.retired == 0 and texts(arm, "approved") == ["User lives in Perth."]
    rep = arm.ingest([Turn("x", "system_writer", writer="review_ui", proposes=("User lives in Hobart.",), op="edit", attr="home")])
    assert rep.retired == 1 and texts(arm, "approved") == ["User lives in Hobart."]
    assert any(r["status"] == "superseded" for r in arm.stats()["rows"])


def test_identity_is_the_accounts_a_model_never_writes_the_owners_name_but_a_third_person_becomes_a_pending_candidate():
    arm, _f = mk("H1")
    arm.reset(USER)
    arm.ingest([Turn("okay so that was the plan and then uh Dev is coming over I think", "system_writer", writer="digest",
                     proposes=("User's name is Dev.",))])
    rows = arm.stats()["rows"]
    assert [r["status"] for r in rows] == ["pending"] and "Dev" in rows[0]["text"] and "name is" not in rows[0]["text"]


def test_affect_is_kept_for_the_household_and_never_for_a_guest():
    emo = [Turn("x", "system_writer", writer="turn_digest", proposes=("User seems anxious about the interview.",), op="say",
                memory_type="emotional_moment")]
    arm, _f = mk("H1")
    arm.reset(USER)
    assert arm.ingest_as("consenting_owner", emo).written == 1 and arm.ingest_as("minor", emo).written == 1   # owner decision 2026-10-05
    assert arm.ingest_as("guest", emo).written == 0 and arm.stats_as("guest")["rows"] == []
    h0, _g = mk("H0")
    h0.reset(USER)
    h0.ingest_as("guest", emo)
    assert len(h0.stats_as("guest")["rows"]) >= 1                                         # natively Hindsight keeps a guest's feelings


def test_a_guest_owns_no_memory_at_all_not_only_no_feelings():
    arm, _f = mk("H1")
    arm.reset(USER)
    rep = arm.ingest_as("guest", [Turn("User lives in Perth.", "owner_taught")])
    assert rep.written == 0 and rep.refused == 1 and arm.stats_as("guest")["rows"] == []
    h0, _g = mk("H0")
    h0.reset(USER)
    h0.ingest_as("guest", [Turn("User lives in Perth.", "owner_taught")])
    assert len(h0.stats_as("guest")["rows"]) >= 1                          # natively there is no such thing as a guest (a fact + its observation)


def test_the_affect_gate_is_the_real_service_gate_not_a_copy(monkeypatch):
    emo = [Turn("x", "system_writer", writer="turn_digest", proposes=("User seems anxious.",), op="say", memory_type="emotional_moment")]
    arm, _f = mk("H1")
    arm.reset(USER)
    monkeypatch.setenv("ZOE_AFFECT_CONSENT_GATE", "members")                              # adults only: a stricter policy flips the minor
    assert arm.ingest_as("minor", emo).written == 0 and arm.ingest_as("consenting_owner", emo).written == 1


def test_a_question_and_the_assistants_words_are_never_stored():
    arm, _f = mk("H1")
    arm.reset(USER)
    arm.ingest([Turn("what is my dentist called", "owner_typed"),
                Turn("thanks", "owner_typed", assistant_text="I don't have any notes about your dentist."),
                Turn("hello there", "assistant")])
    assert arm.stats()["rows"] == []


# ── forgetting: cascade + a hashed ledger that cannot list what it forgot ────

def test_forget_cascades_deletes_documents_and_keeps_everyone_else():
    arm, fake = mk("H1")
    arm.reset(USER)
    arm.ingest([Turn("User's friend Marisol lives in Hobart.", "owner_taught"), Turn("Marisol's birthday is the 3rd of March.", "owner_taught"),
                Turn("User's friend Priya works at the observatory.", "owner_taught")])
    reply = arm.forget("Marisol")
    assert "2 document(s) removed" in reply
    assert sum(1 for m, p in fake.calls if m == "DELETE" and "/documents/" in p) == 2            # the document-delete cascade
    assert texts(arm, "approved") == ["User's friend Priya works at the observatory."]
    assert all("Marisol" not in r["text"] for r in arm.recall("tell me about Marisol", 5))


def test_the_ledger_holds_hashes_only_and_fences_every_later_write_until_a_reteach():
    arm, _f = mk("H1")
    arm.reset(USER)
    arm.ingest([Turn("User's friend Marisol lives in Hobart.", "owner_taught")])
    arm.forget("Marisol")
    dump = json.dumps(arm.layer.ledger_rows())
    assert "marisol" not in dump.lower() and arm.layer.ledger_rows()                       # a hash, never a name
    for writer in ("digest", "idle_consolidation", "turn_digest"):
        rep = arm.ingest([Turn("rang about the weekend", "system_writer", writer=writer, proposes=("User's friend Marisol is visiting.",))])
        assert rep.written == 0 and rep.refused == 1
    arm.ingest([Turn("User's friend Marisol lives in Lima.", "owner_taught")])              # the person's own explicit teach releases it
    assert any("Lima" in t for t in texts(arm, "approved"))
    assert arm.ingest([Turn("x", "system_writer", writer="digest", proposes=("Marisol likes tea.",))]).written == 1


def test_forget_keeps_the_innocent_remainder_of_a_document_unless_scrub_is_off():
    for scrub_off, expect in ((False, True), (True, False)):
        arm, _f = mk("H2", off=frozenset({"scrub"}) if scrub_off else frozenset())
        arm.reset(USER)
        arm.ingest([Turn("User's friend Marisol lives in Hobart. User's sister Ines works at a bakery.", "owner_taught")])
        arm.forget("Marisol")
        kept = texts(arm, "approved", "pending", "disputed")      # an INVALIDATED observation is history, the same line the cells draw
        assert any("Ines" in t for t in kept) is expect and not any("Marisol" in t for t in kept)
        arm.close()


def test_the_side_table_does_not_keep_a_forgotten_name():
    arm, _f = mk("H1")
    arm.reset(USER)
    arm.ingest([Turn("User lives in Perth.", "owner_taught")])
    arm.ingest([Turn("x", "system_writer", writer="digest", proposes=("User lives in Marisol.",))])          # held back (disputed)
    arm.forget("Marisol")
    assert all("Marisol" not in r["text"] for r in arm.stats()["rows"])


# ── the layer's size (decision rule G3) and the whole spec ───────────────────

def test_the_zoe_layer_fits_the_g3_budget():
    n = hs.zoe_layer_lines()
    assert 50 < n <= 1000, n


def test_the_whole_store_tier_runs_clean_on_h1_and_h0_is_red_on_the_hard_axes():
    from zmb import artifact
    from zmb.runner import run_cells
    store = [c for c in CELLS.values() if c.tier == "store"]
    arm, _f = mk("H1")
    rows = run_cells(store, WORLD, arm)
    arm.close()
    # every cell that RAN either matches its declared expectation or is a known H1 adapter gap. SKIPs are declared capability gaps
    # (the temporal / edge / disk cells need conflict_pass / edges / disk, which only Z0 has); the targets (expected FAIL) are red on
    # H1 as on Z0 unless a fix lands (F3 is the one H1 passes: its forgotten ledger).
    ran = [r for r in rows if r["verdict"] != "SKIP"]
    assert [r["id"] for r in ran if r["verdict"] == "ERROR"] == []
    unexpected = sorted(r["id"] for r in ran if r["expected"] == "PASS" and r["verdict"] != "PASS")    # a target H1 PASSES is the point (F3)
    # KNOWN GAPS of the H arms' Zoe layer: one. The five A3 provenance cells (the row export carries source_excerpt / user_turn_id,
    # stamped through Hindsight metadata) and the unverified-speaker hold (A7.panel_unverified_kept.*, I2.third_party_fragment.panel_unverified:
    # a self-assertion from an unverified voice is a PENDING candidate, #1895) are ported. What stays red:
    #  - C4.valid_from_is_event_time (the two timelines, audit P2.1, #1896): at this point of the stack the H arms' row export carries NO
    #    validity interval at all (valid_from / invalid_at are stamped by the conflict pass and the side table, which the stacked PR #1901 adds), so
    #    there is nothing to stamp the owner's stated event time ("since 2015") into. The port itself is small (the layer calls the REAL
    #    memory_temporal.parse_validity / stamp, ~8 lines) and lands in #1901, which deletes this entry.
    # A gap that comes back must be listed HERE with its reason, never silently accepted: fix the adapter and shrink this list, never widen it.
    known: "list[str]" = ["C4.valid_from_is_event_time"]
    assert unexpected == known, unexpected
    h0, _g = mk("H0")
    rows0 = run_cells(store, WORLD, h0)
    h0.close()
    bad = artifact.hard_violations(rows0, CELLS)
    assert len(bad) >= 60 and any(b.startswith("A1.") for b in bad)                       # what Hindsight does natively, on the same spec


def test_the_artifact_of_an_h_arm_run_carries_no_household_text():
    from zmb import artifact
    from zmb.runner import run_cells
    arm, _f = mk("H1")
    rows = run_cells([CELLS["A1.digest.home"], CELLS["F1.sweep_keeps_the_rest"]], WORLD, arm)
    arm.close()
    payload = {"cells": [{k: v for k, v in r.items() if k != "evidence"} for r in rows], "measure": arm.measure()}
    assert artifact.household_strings_in(payload, WORLD.all_strings()) == []


# ── provenance (ZMB A3): the stamp rides through Hindsight and is read back ──────────────────────────────────────────

A3_CELLS = ("A3.typed_turn_rows", "A3.voice_verified_turn_rows", "A3.taught_rows", "A3.nightly_digest_rows", "A3.user_turn_rows_rate")


@pytest.mark.parametrize("variant", ["H1", "H2"])
def test_the_provenance_cells_pass_with_the_stamp_and_go_red_without_it(variant):
    for cid in A3_CELLS:
        assert verdict(variant, cid) == "PASS", f"{variant} {cid}"
        assert verdict(variant, cid, off=frozenset({"provenance"})) == "FAIL", f"switching provenance OFF must turn {cid} red on {variant}"


def test_h0_has_no_zoe_layer_so_it_stamps_no_provenance_and_the_a3_cells_are_red():
    for cid in A3_CELLS:
        assert verdict("H0", cid) == "FAIL", cid


def test_the_excerpt_and_the_turn_id_ride_as_item_metadata_and_a_turn_tag_and_come_back_in_the_export():
    arm, fake = mk("H1")
    arm.reset(USER)
    arm.ingest([Turn("I live in Perth", "owner_typed")])
    item = posts(fake, "/memories")[-1]["items"][0]
    assert item["metadata"]["source_excerpt"] == "I live in Perth" and item["metadata"]["user_turn_id"]
    assert f"turn:{item['metadata']['user_turn_id']}" in item["tags"]
    row = next(r for r in arm.stats()["rows"] if r["status"] == "approved")
    assert row["source_excerpt"] == "I live in Perth" and row["user_turn_id"] == item["metadata"]["user_turn_id"] and row["authority_class"]
    # a server that dropped the metadata still says which turn through the tag, and says nothing about the words (never invents them)
    fake.banks[arm.bank_for(USER)]["units"][0]["metadata"] = {}
    row = next(r for r in arm.stats()["rows"] if r["status"] == "approved")
    assert row["user_turn_id"] == item["metadata"]["user_turn_id"] and row["source_excerpt"] == ""


def test_a_held_candidate_carries_its_provenance_in_the_export_too():
    arm, _f = mk("H1")
    arm.reset(USER)
    arm.ingest([Turn("I live in Perth", "panel_unverified")])
    held = [r for r in arm.stats()["rows"] if r["status"] == "pending"]
    assert len(held) == 1 and held[0]["source_excerpt"] == "I live in Perth" and held[0]["user_turn_id"]


def test_forgetting_a_name_leaves_no_excerpt_that_names_her():
    arm, _f = mk("H1")
    arm.reset(USER)
    arm.ingest([Turn("I live in Perth and Marisol is my sister", "owner_typed")])
    assert any("Marisol" in r["source_excerpt"] for r in arm.stats()["rows"])           # the excerpt is the owner's whole sentence
    arm.forget("Marisol")
    assert all("marisol" not in (r["source_excerpt"] + r["text"]).lower() for r in arm.stats()["rows"])


def test_the_speaker_control_turns_the_unverified_hold_red():
    for cid in ("A7.panel_unverified_kept.home", "I2.third_party_fragment.panel_unverified"):
        assert verdict("H1", cid) == "PASS", cid
        assert verdict("H1", cid, off=frozenset({"speaker"})) == "FAIL", cid


# ── the capabilities an H arm declares it lacks: those cells SKIP with the reason, never ERROR, never pass ──────────

def test_cells_needing_a_capability_hindsight_lacks_skip_with_the_reason():
    store = [c for c in CELLS.values() if c.tier == "store"]
    for variant in ("H0", "H1", "H2"):
        arm, _f = mk(variant)
        for cap in ("conflict_pass", "edges", "disk"):
            assert cap not in arm.capabilities and cap in hs.HindsightArm.LACKS
            need = [c for c in store if cap in cellmod.required_capabilities(c)]
            assert need, f"no cell needs {cap}: the declaration would be vacuous"
            for c in need:
                o = run(arm, c.id)
                assert o.verdict == "SKIP" and cap in o.reason, (variant, c.id, o)
        arm.close()


def test_called_directly_the_arm_says_why_it_cannot():
    arm, _f = mk("H1")
    for call in (arm.run_conflict_pass, arm.edges, lambda: arm.write_edge("a", "b", "friend", "personal", "user_stated", "conversation"),
                 arm.hard_delete, lambda: arm.disk_residue(["x"])):
        with pytest.raises(NotImplementedError):
            call()
    arm.close()
