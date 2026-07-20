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

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

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


# ── The rocks must match the LIVE DEFAULT config, not just the doc ────────────
# The doc tests above guard CANONICAL.md's text. But a rock can be swapped without
# touching the doc by editing the env-resolution DEFAULT in the live code:
#   - brain  → ZOE_CORE_MODEL_ID default in zoe_core_client.py (~57)
#   - STT    → ZOE_MOONSHINE_ARCH default in routers/voice_tts.py (~2604)
# These guards parse the COMMITTED default (what ships when the env var is unset) and
# fail if it drifts off the canonical rock — no models, no services, no network, so
# they run on GitHub-hosted CI. They assert on the code's default-resolution literal,
# which is exactly the surface a silent swap would edit.
def _data_src(rel_path: str) -> str:
    return open(os.path.join(DATA, rel_path), encoding="utf-8").read()


def _env_default(rel_path: str, var: str) -> str | None:
    """Extract the committed default for an env-resolved setting, supporting
    `os.environ.get("VAR", "default")`, `os.environ.get("VAR") or "default"`,
    and the typed_env accessors (`env_str("VAR", "default")` etc.) that the
    Wave-4 migrations move call sites onto."""
    src = _data_src(rel_path)
    m = re.search(
        rf'os\.environ\.get\(\s*["\']{re.escape(var)}["\']\s*,\s*["\']([^"\']+)["\']', src
    )
    if m:
        return m.group(1)
    m = re.search(
        rf'os\.environ\.get\(\s*["\']{re.escape(var)}["\']\s*\)\s*or\s*["\']([^"\']+)["\']', src
    )
    if m:
        return m.group(1)
    m = re.search(
        rf'env_(?:str|bool|int|float|list)\(\s*["\']{re.escape(var)}["\']\s*,\s*["\']([^"\']+)["\']', src
    )
    return m.group(1) if m else None


def test_brain_live_default_is_canonical_gemma4_e4b_qat():
    """ZOE_CORE_MODEL_ID's committed default must resolve to the Gemma-4 E4B-QAT rock.
    Guards against the exact regression #875 fixed (a stale E2B id sneaking back in)."""
    default = _env_default("zoe_core_client.py", "ZOE_CORE_MODEL_ID")
    assert default, "ZOE_CORE_MODEL_ID default not found in zoe_core_client.py — guard can't verify the brain rock"
    low = default.lower()
    rock = _rocks()["brain"]
    assert "gemma-4" in low, f"brain default '{default}' is not Gemma 4 (rock family: {rock['family']})"
    for tok in rock["variant"].lower().split("-"):  # E4B-QAT → must contain 'e4b' and 'qat'
        assert tok in low, (
            f"brain default '{default}' drifted off the {rock['variant']} rock "
            f"(missing '{tok}'; e.g. a swap back to E2B). Edit CANONICAL.md + this test on purpose."
        )


def test_stt_live_default_arch_is_moonshine_medium():
    """ZOE_MOONSHINE_ARCH's committed default (and the hard-coded fallback) must stay a
    MEDIUM arch — the STT rock is 'Moonshine v2 Medium', so TINY/BASE/SMALL/LARGE is a swap."""
    default = _env_default("routers/voice_tts.py", "ZOE_MOONSHINE_ARCH")
    assert default, "ZOE_MOONSHINE_ARCH default not found in voice_tts.py — guard can't verify the STT rock"
    assert "MEDIUM" in default.upper(), (
        f"Moonshine arch default '{default}' is not a MEDIUM arch — STT rock is "
        f"'{_rocks()['stt']['name']}'; a non-Medium default silently swaps it"
    )
    assert "ModelArch.MEDIUM_STREAMING" in _data_src("routers/voice_tts.py"), (
        "Moonshine fallback arch (mv.ModelArch.MEDIUM_STREAMING) missing from voice_tts.py — "
        "the rock's hard-coded fallback must also stay Medium"
    )


def test_tts_live_waterfall_keeps_kokoro_before_edge_before_espeak():
    """In the live /synthesize waterfall the Kokoro rock must be attempted before the
    Edge cloud fallback, which must precede the espeak-ng last resort."""
    src = _data_src("routers/voice_tts.py")
    start = src.index("async def synthesize(")
    body = src[start:src.index("\nasync def ", start + 1)]  # just the synthesize() route body
    i_kokoro = body.find("_synthesize_kokoro")  # first hit = the Kokoro sidecar attempt
    i_edge = body.find("_synthesize_edge_tts")
    i_espeak = body.find("_synthesize_espeak")
    assert 0 <= i_kokoro < i_edge < i_espeak, (
        f"TTS waterfall order drifted (kokoro@{i_kokoro}, edge@{i_edge}, espeak@{i_espeak}) — "
        "Kokoro (the rock) must precede Edge TTS must precede espeak-ng in /synthesize"
    )
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
    """The TTS rock has to be primary in every live voice synthesis waterfall."""
    tts = _rocks()["tts"]
    router = _read_repo("services/zoe-data/routers/voice_tts.py")
    primary_engine = _compact_token(tts["name"])

    for route in ("/synthesize", "/stream"):
        handler = re.search(
            rf"@router\.post\(\"{re.escape(route)}\"\).*?(?=\n\s*@router\.post\()",
            router,
            re.DOTALL,
        )
        assert handler, f"voice_tts.py is missing the /api/voice{route} handler"
        body = handler.group(0)
        primary_calls = [
            match.start()
            for match in re.finditer(
                rf"_synthesize_{re.escape(primary_engine)}(?:_|\()",
                body.lower(),
            )
        ]

        edge_tts = body.find("_synthesize_edge_tts")
        espeak = body.find("_synthesize_espeak")
        local_service = body.find("_synthesize_local_service")
        assert primary_calls, (
            f"{tts['name']} is not wired into the live TTS waterfall for /api/voice{route}"
        )
        first_primary = min(primary_calls)
        assert local_service != -1, (
            f"local TTS sidecar fallback is missing from the live TTS waterfall for /api/voice{route}"
        )
        assert edge_tts != -1, f"Edge TTS fallback is missing from /api/voice{route}"
        assert espeak != -1, f"espeak-ng fallback is missing from /api/voice{route}"
        assert first_primary < local_service < edge_tts < espeak, (
            f"/api/voice{route} waterfall must keep {tts['name']} primary before "
            "local sidecar, then Edge TTS, then espeak-ng"
        )


# ── The cleanup stays clean: no archive graveyard creeps back ─────────────────
def test_no_docs_archive_graveyard():
    """docs/archive was removed (git history keeps it). Retire by removing, not by
    hoarding a graveyard the whole team greps through."""
    assert not os.path.isdir(os.path.join(REPO, "docs", "archive")), (
        "docs/archive reappeared — retire superseded files by deleting them "
        "(git keeps history); do not re-introduce an archive graveyard. See docs/CANONICAL.md"
    )


# ── Two-stage router: a rock that is ALLOWED TO IMPROVE ──────────────────────
#
# The brain/STT/TTS rocks are locked because they must not change. The router is
# different: the self-train loop (ZOE_ROUTER_SELFTRAIN) exists to mine real
# traffic and promote a better checkpoint, guarded by its ratchet. So pinning a
# checkpoint hash here would fight the design.
#
# What IS locked is the architecture and the contract — the two-stage shape, the
# sidecar seam, the flag semantics, and the artifact paths. Those are what a
# refactor could quietly undo, and until 2026-07-20 nothing enforced them:
# CANONICAL described the router in prose while CI guarded only the other three.


def test_router_rock_is_two_stage_setfit_then_functiongemma():
    """The two-stage SHAPE is the rock. Collapsing it to one stage, or swapping
    either stage's family, must be a deliberate reviewed edit — not a refactor."""
    router = _rocks()["router"]
    assert router["architecture"] == "two-stage", f"router architecture drifted: {router}"
    assert "setfit" in _compact_token(router["stage1"]), f"stage-1 drifted off SetFit: {router}"
    assert "functiongemma" in _compact_token(router["stage2"]), (
        f"stage-2 drifted off FunctionGemma: {router}"
    )


def test_router_checkpoint_is_deliberately_not_pinned():
    """Guards the DISTINCTION, not a value: if someone pins a checkpoint hash they
    have broken the self-train loop's ability to promote, which is the whole point
    of the ratchet. Keep this rock improvable."""
    assert _rocks()["router"]["checkpoint_pinned"].strip().lower().startswith("no"), (
        "router checkpoint got pinned — that fights ZOE_ROUTER_SELFTRAIN's ratchet; "
        "the architecture is the rock, the weights are not"
    )


def test_router_stage1_artifact_is_committed():
    """Stage 1 is small (~1.5 MB) and IS in git — losing it must not be possible.
    Stage 2's GGUF (~291 MB) is deliberately NOT in git; it rebuilds from the
    tracked corpus via labs/functiongemma-finetune/export_gguf.sh."""
    rel = _rocks()["router"]["stage1_artifact"]
    assert os.path.exists(os.path.join(REPO, rel)), f"stage-1 router head missing: {rel}"


def test_router_sidecar_seam_is_intact():
    """The sidecar contract: a dedicated unit on its own port, reached by URL.
    zoe-data must not grow an in-process copy of stage 2."""
    router = _rocks()["router"]
    unit = os.path.join(REPO, "scripts", "setup", "systemd", router["sidecar_service"])
    assert os.path.exists(unit), f"router sidecar unit missing: {unit}"
    assert router["sidecar_port"] in _read_repo(
        os.path.join("scripts", "setup", "systemd", router["sidecar_service"])
    ), f"sidecar unit no longer binds {router['sidecar_port']}"


def test_router_flag_keeps_its_four_stage_rollout():
    """ZOE_ROUTER_HEAD's off|shadow|shadow2|active ladder is how a router change
    reaches production safely. Losing a rung removes the ability to observe a new
    decision without routing on it."""
    src = _data_src("semantic_router.py") + _data_src("router_two_stage.py")
    for rung in ("shadow2", "active"):
        assert rung in src, f"ZOE_ROUTER_HEAD rung '{rung}' vanished from the router modules"
