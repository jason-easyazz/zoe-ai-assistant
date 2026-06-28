"""Canonical invariants — LOCK IN the settled decisions so they can't silently drift.

The repo kept re-deciding the brain model and the voice stack because nothing said,
in one enforced place, *what was locked*. `docs/CANONICAL.md` now declares it, and
this test makes the declaration load-bearing: swapping a rock fails CI, which forces
the change to be a deliberate, reviewed edit (of BOTH the doc and this test) rather
than a quiet config tweak that the next refactor undoes.

If a rock legitimately changes, update the expected value here in the SAME commit —
that keeps the intent explicit and visible in review. See feedback_fixed_models_are_rocks
and project_zoe_voice_live_topology in memory.
"""
import os
import re

import pytest

DATA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # services/zoe-data
REPO = os.path.dirname(os.path.dirname(DATA))  # repo root
CANONICAL = os.path.join(REPO, "docs", "CANONICAL.md")


def _read_repo(path: str) -> str:
    return open(os.path.join(REPO, path), encoding="utf-8").read()


def _compact_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _rocks() -> dict:
    """Parse the machine-readable rocks block out of docs/CANONICAL.md."""
    text = open(CANONICAL, encoding="utf-8").read()
    m = re.search(r"LOCKED-ROCKS.*?```yaml\n(.*?)```", text, re.DOTALL)
    assert m, "LOCKED-ROCKS yaml block missing from docs/CANONICAL.md"
    block = m.group(1)
    try:
        import yaml
        return yaml.safe_load(block)["rocks"]
    except ImportError:
        # dependency-free fallback: flat key: "value" scrape
        out, stack = {}, {}
        for line in block.splitlines():
            mm = re.match(r"^(\s*)([\w]+):\s*(.*)$", line)
            if not mm:
                continue
            indent, key, val = len(mm.group(1)), mm.group(2), mm.group(3).strip().strip('"')
            parent = stack.get(indent - 2, out)
            if not val:
                stack[indent] = parent.setdefault(key, {})
            else:
                parent[key] = val
        return out["rocks"]


# ── The rocks: settled, do not swap (see feedback_fixed_models_are_rocks) ─────
def test_brain_rock_is_gemma4_e4b_with_mtp():
    brain = _rocks()["brain"]
    assert brain["family"] == "Gemma 4", f"brain LLM family drifted: {brain}"
    assert brain["variant"] == "E4B-QAT", f"brain variant drifted off E4B-QAT: {brain}"
    assert brain["drafter"] == "MTP", "MTP speculative drafter dropped from the brain rock"


def test_stt_rock_is_moonshine():
    stt = _rocks()["stt"]
    assert "Moonshine" in stt["name"], f"STT rock drifted off Moonshine: {stt}"


def test_tts_rock_is_kokoro():
    assert "Kokoro" in _rocks()["tts"]["name"], "TTS rock drifted off Kokoro"


# ── The rocks must match the LIVE wiring, not just the doc ────────────────────
def test_moonshine_actually_loaded_in_live_startup():
    """The STT rock has to be wired, not merely declared — guard the loader marker."""
    marker = _rocks()["stt"]["loader_marker"]
    main = open(os.path.join(DATA, "main.py"), encoding="utf-8").read()
    assert marker in main, (
        f"STT loader marker '{marker}' not found in main.py — Moonshine warmup "
        "may have been dropped (whisper is a fallback, never the primary rock)"
    )


def test_brain_rock_wired_in_llama_server_unit():
    """The brain rock has to be wired in the live llama-server unit template."""
    brain = _rocks()["brain"]
    unit = _read_repo("scripts/setup/systemd/llama-server.service")
    exec_start = re.search(
        r"^ExecStart=(.*?)(?=\n[A-Z][A-Za-z]*=|\n\[|\Z)",
        unit,
        re.DOTALL | re.MULTILINE,
    )
    assert exec_start, "llama-server.service is missing the llama-server ExecStart block"
    command = exec_start.group(1)

    model = re.search(r"(?:^|\s)--model\s+([^\\\s]+)", command)
    draft = re.search(r"(?:^|\s)--model-draft\s+([^\\\s]+)", command)
    assert model, "llama-server.service is missing --model for the canonical brain GGUF"
    assert draft, "llama-server.service is missing --model-draft for the MTP drafter GGUF"

    model_path = model.group(1)
    draft_path = draft.group(1)
    model_token = _compact_token(model_path)
    family_token = _compact_token(brain["family"])
    variant_token = _compact_token(brain["variant"])

    assert model_path.endswith(".gguf"), f"brain model is not a GGUF path: {model_path}"
    assert family_token in model_token, (
        f"brain model path does not reference canonical family {brain['family']}: {model_path}"
    )
    assert variant_token in model_token, (
        f"brain model path does not reference canonical variant {brain['variant']}: {model_path}"
    )

    assert brain["drafter"].lower() in draft_path.lower(), (
        f"draft model path does not reference canonical drafter {brain['drafter']}: {draft_path}"
    )
    assert re.search(r"(?:^|\s)--spec-type\s+draft-mtp(?:\s|\\|$)", command), (
        "llama-server.service is missing MTP speculative decoding wiring "
        "(--spec-type draft-mtp)"
    )


def test_kokoro_tts_is_primary_live_voice_engine():
    """The TTS rock has to be primary in the live /api/voice/synthesize waterfall."""
    tts = _rocks()["tts"]
    router = _read_repo("services/zoe-data/routers/voice_tts.py")
    synthesize = re.search(
        r"@router\.post\(\"/synthesize\"\).*?(?=\n\s*@router\.post\()",
        router,
        re.DOTALL,
    )
    assert synthesize, "voice_tts.py is missing the /api/voice/synthesize handler"
    body = synthesize.group(0)
    primary_engine = _compact_token(tts["name"])
    primary_calls = [
        match.start()
        for match in re.finditer(
            rf"_synthesize_{re.escape(primary_engine)}(?:_|\()",
            body.lower(),
        )
    ]

    edge_tts = body.find("_synthesize_edge_tts")
    espeak = body.find("_synthesize_espeak")
    assert primary_calls, (
        f"{tts['name']} is not wired into the live TTS waterfall"
    )
    first_primary = min(primary_calls)
    assert edge_tts != -1, "Edge TTS fallback is missing from the live TTS waterfall"
    assert espeak != -1, "espeak-ng fallback is missing from the live TTS waterfall"
    assert first_primary < edge_tts < espeak, (
        f"live TTS waterfall must keep {tts['name']} primary, then Edge TTS, then espeak-ng"
    )


# ── The cleanup stays clean: no archive graveyard creeps back ─────────────────
def test_no_docs_archive_graveyard():
    """docs/archive was removed (git history keeps it). Retire by removing, not by
    hoarding a graveyard the whole team greps through."""
    assert not os.path.isdir(os.path.join(REPO, "docs", "archive")), (
        "docs/archive reappeared — retire superseded files by deleting them "
        "(git keeps history); do not re-introduce an archive graveyard. See docs/CANONICAL.md"
    )
