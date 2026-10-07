"""ZMB: the ZMA arm - Zoe's CURRENT memory stack (the REAL ``MemoryService``, Z0e's store) with MemPalace integrated on top: one ingest path, one packet, one forget.

Runs the real ``MemoryService`` through the lab driver, so it lives in the zoe-data lane; MemPalace's tool surface is the TEST DOUBLE (``DoubleBackend``) and the brain a scripted
stand-in: this proves the glue and that Z0's floors are still Z0's (the integration never lets a verbatim chunk outrank an authority-ordered row). Synthetic data only; no network,
no model, no Postgres (``ci_safe``). What it cannot say: anything about the real MemPalace's retrieval or a 4B model's tool use (the window's).
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts" / "perf"))

from zmb import mpa_cells  # noqa: E402
from zmb.arms import make_arm  # noqa: E402
from zmb.arms.base import Turn  # noqa: E402
from zmb.arms.zma import BRAIN_TOOLS, INTEGRATION, ShimEmbeddingFunction, ZMAArm, z0_memory_prompt_text  # noqa: E402

USER = "demo_bar_1a2b3c4d"


NO_MODEL = "Z0e needs the on-disk MiniLM model"


@pytest.fixture(params=["scripted", "z0e"])
def zma(request):
    """Both stores under the same glue. ``scripted`` = Z0's bag-of-words lab collection (no model: CI proves the plumbing here); ``z0e`` = Z0e's Chroma + MiniLM, which the
    bench never downloads, so a box without the model on disk SKIPS it (the bench's control-refusal pattern), never errors."""
    try:
        a = mpa_cells.lab_arm("ZMA", z0_embed=request.param == "z0e")
        a.reset(USER)
    except NotImplementedError:
        pytest.skip(NO_MODEL)
    yield a
    a.close()


def _skip_if_z0e_cells_could_not_run(res, z0_embed):
    if z0_embed and any("Z0e needs" in str(r.get("reason", "")) for r in res["cells"] if r["verdict"] == "SKIP"):
        pytest.skip(NO_MODEL)


def test_zma_is_registered_and_declares_both_stacks_capabilities():
    assert make_arm("ZMA").name == "ZMA"
    assert {"conflict_pass", "edges", "exact_words", "multi_hop", "protocol", "observations", "idle_pass", "identities"} <= set(ZMAArm.capabilities)
    assert set(INTEGRATION) == {"one_ingest_path", "one_embedder", "one_packet", "one_forget", "one_protocol", "one_night"}


def test_the_brain_is_offered_two_read_tools_and_rules_one_to_three_only(zma):
    assert BRAIN_TOOLS == ("mempalace_status", "mempalace_search")
    assert [t["function"]["name"] for t in zma.mpa.tool_specs] == list(BRAIN_TOOLS)
    proto = zma.mpa.prompt_parts()["protocol"]
    assert "1. ON WAKE-UP" in proto and "3. IF UNSURE" in proto and "4. AFTER EACH SESSION" not in proto and "kg_supersede" not in proto
    assert not zma.mpa.prompt_parts()["aaak_spec"]


def test_a_user_turn_is_written_once_and_z0_reads_it_from_the_chunk(zma):
    zma.ingest([Turn("User's friend Aldo lives in Bergvik.", "owner_taught"), Turn("My dentist is Dr Okonkwo and the surgery is on Elm Street.", "owner_typed")])
    rows = zma.stats()["rows"]
    z0 = rows[:zma.stats()["tiers"]["z0"]]
    chunks = [r for r in rows if r.get("origin", "").startswith("harness")]
    assert zma.chunks == 2 and len(chunks) == 2
    assert z0 and all(r["user_turn_id"] in zma.mpa._prov for r in z0 if r["status"] == "approved")
    assert not zma.mpa.model.calls                      # no model call on the write path (the owner's design)


@pytest.mark.parametrize("z0_embed", [False, True], ids=["scripted", "z0e"])
def test_the_integration_cells_go_red_with_their_switch_off_and_the_floors_hold(z0_embed):
    res = mpa_cells.run_all("ZMA", "double", controls="all", z0_embed=z0_embed)
    _skip_if_z0e_cells_could_not_run(res, z0_embed)
    s = res["summary"]
    assert s["not_instrumented"] == [] and s["fail"] == [] and s["sanity_fail"] == []
    by = {r["id"]: r for r in res["cells"]}
    for cid, ctl in (("ZMA-W1.one-ingest-path", "one_ingest"), ("ZMA-W2.one-packet", "one_packet"), ("MPA-F1.forget.both-tiers", "forget_verbatim")):
        assert by[cid]["verdict"] == "PASS" and by[cid]["controls_verdicts"][ctl] == "FAIL"


def test_z0s_authority_order_wins_over_a_conflicting_verbatim_line(zma):
    zma.ingest([Turn("User's friend Aldo lives in Bergvik.", "owner_taught")])
    zma.mpa._need().call("mempalace_add_drawer", {"wing": "user", "room": "chat", "content": "User's friend Aldo lives in Oldmere."})
    texts = " ".join(r["text"].lower() for r in zma.recall("where does Aldo live", 8))
    assert "bergvik" in texts and "oldmere" not in texts


def test_one_forget_clears_both_tiers_and_the_ledger_blocks_the_return(zma):
    zma.ingest([Turn("My sister Marisol lives in Perth.", "owner_taught"), Turn("My dentist is Dr Okonkwo and the surgery is on Elm Street.", "owner_taught")])
    msg = zma.forget("Marisol")
    assert "forgotten" in msg.lower()
    retained = [r for r in zma.stats()["rows"] if r["status"] in ("approved", "pending", "disputed") and "marisol" in r["text"].lower()]
    assert not retained
    assert "marisol" not in " ".join(r["text"].lower() for r in zma.recall("tell me about Marisol", 8))
    zma.ingest([Turn("Marisol rang about the lift", "owner_typed")])
    assert zma.chunks == 2                              # the ledger refused the chunk of the forgotten name


def test_the_brain_reads_z0s_packet_and_may_search_for_the_exact_words(zma):
    zma.ingest([Turn("My dentist is Dr Okonkwo and the surgery is on Elm Street.", "owner_taught")])
    tr = zma.converse("who is my dentist")
    assert tr.searched_before_answer and tr.reply
    assert not any(t.name in ("mempalace_add_drawer", "mempalace_kg_supersede") for t in tr.tools)    # the brain never writes in ZMA


def test_the_protocol_cost_is_priced_against_z0s_own_prompt_from_the_committed_sources(zma):
    text = z0_memory_prompt_text()
    assert "recall_memory" in text and len(text) > 1500
    c = zma.protocol_cost(["where does Aldo live"])
    assert c["z0_memory_prompt_tokens"] > 300 and c["zma_added_tokens"] > 400 and c["fits_with_z0_prompt"] and c["slot_tokens"] == 8192


def test_the_shared_embedder_asks_the_loopback_shim_and_refuses_anything_else():
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            return

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(body["input"])
            data = json.dumps({"data": [{"index": i, "embedding": [0.0] * 384} for i in range(len(body["input"]))]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ef = ShimEmbeddingFunction(f"http://127.0.0.1:{srv.server_address[1]}")
        out = ef(["a", "b", "c"])
        assert len(out) == 3 and len(out[0]) == 384 and seen == [["a", "b", "c"]]
    finally:
        srv.shutdown()
        srv.server_close()
    with pytest.raises(ValueError):
        ShimEmbeddingFunction("http://10.0.0.5:11501")


def test_the_forgetting_cells_are_also_reported_under_the_zma_prefix_the_gate_reads():
    res = mpa_cells.run_all("ZMA", "double", only="MPA-F", controls="none")
    ids = {r["id"] for r in res["cells"]}
    assert {"MPA-F1.forget.both-tiers", "ZMA-F1.forget.both-tiers", "MPA-F5.forget.alias-sweep", "ZMA-F5.forget.alias-sweep"} <= ids


@pytest.mark.parametrize("z0_embed", [False, True], ids=["scripted", "z0e"])
def test_z0_outranks_a_conflicting_chunk_and_the_cell_goes_red_without_the_authority_floor(z0_embed):
    res = mpa_cells.run_all("ZMA", "double", only="MPA-A3", controls="all", z0_embed=z0_embed)
    _skip_if_z0e_cells_could_not_run(res, z0_embed)
    r = res["cells"][0]
    assert r["verdict"] == "PASS" and r["controls_verdicts"] == {"authority": "FAIL"}
