"""The installer and setup scripts must agree with docs/CANONICAL.md on the rocks and the ports first boot depends on.

``test_canonical_invariants.py`` locks the rocks in the DOC and in the live code defaults. It never read the scripts a NEW unit is
built with, and those drifted: a setup script still downloads a retired brain, the first-run doc points at a SQLite initialiser, and
the router download lands where nothing looks. A sold unit is built by exactly these scripts, so their drift is the buyer's bug.

Each check reports a SET of offenders and compares it with ``KNOWN_DRIFT`` (a ratchet, the ``test_canonical_invariants`` approach
extended to scripts): a NEW offender fails, and a known one that is FIXED also fails until its entry is deleted, so the list only
shrinks. Every entry is a finding in docs/architecture/first-boot.md. Reads files only: no imports of the service, no network.
"""
from __future__ import annotations

import os
import re

import pytest

pytestmark = pytest.mark.ci_safe

DATA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(DATA))
SETUP = os.path.join(REPO, "scripts", "setup")

#: check -> {offender: why it is drift}. Delete an entry in the commit that fixes it.
KNOWN_DRIFT = {
    "retired_brain_download": {
        "scripts/setup/download_qwen_optimized.sh": "downloads Qwen 2.5 7B, a retired brain (CANONICAL: Gemma 4 E4B-QAT + MTP)",
        "scripts/setup/monitor_qwen_download.sh": "monitors that retired download",
        "scripts/setup/populate_multica.py": "seeds the (paused) Multica board with a runtime whose LLM is Llama-3.2-3B on :8080",
    },
    "first_run_uses_sqlite": {
        "scripts/setup/init_databases.sh": "requires sqlite3 and writes SQLite schemas; install-jetson.sh runs alembic instead",
        "scripts/setup/init_databases.py": "same, in python; prints a demo password and a UI port (3080) nothing serves",
    },
    "router_artifact_dir": {
        "scripts/setup/download_gguf_models.sh": "--with-router downloads the BASE FunctionGemma into ~/models/functiongemma-270m; "
                                                 "CANONICAL stage2_artifact_dir and the unit read ~/models/functiongemma-router",
    },
    "lan_address_default": {
        "services/zoe-data/routers/panel_provision.py": "ZOE_BASE_URL defaults to one LAN address; the panel pairing link is built "
                                                       "from it and no installer or env template sets it",
    },
}


def _read(rel: str) -> str:
    return open(os.path.join(REPO, rel), encoding="utf-8").read()


def _canonical() -> str:
    return _read("docs/CANONICAL.md")


def _canonical_brain() -> tuple:
    """(brain gguf, mtp gguf, models dir) from the CANONICAL rocks table's brain row."""
    row = next(line for line in _canonical().splitlines() if line.startswith("| **Brain (LLM)**"))
    ggufs = re.findall(r"([\w.-]+\.gguf)", row)
    mdir = re.search(r"~/models/([\w.-]+)/", row).group(1)
    brain = next(g for g in ggufs if not g.startswith("mtp-"))
    mtp = next(g for g in ggufs if g.startswith("mtp-"))
    return brain, mtp, mdir


def _canonical_port(pattern: str) -> str:
    m = re.search(pattern, _canonical())
    assert m, pattern
    return m.group(1)


def _shell_var(rel: str, var: str) -> str:
    m = re.search(rf'^{var}="([^"]*)"', _read(rel), re.M)
    assert m, f"{var} not found in {rel}"
    return m.group(1)


def _ratchet(check: str, offenders: set) -> None:
    known = set(KNOWN_DRIFT.get(check, {}))
    new, fixed = offenders - known, known - offenders
    assert not new, f"{check}: NEW drift from docs/CANONICAL.md: {sorted(new)}"
    assert not fixed, f"{check}: fixed, so delete it from KNOWN_DRIFT: {sorted(fixed)}"


# ── the brain rock: what the installer downloads is what the unit loads ───────────────────────────────────────────────────

def test_downloader_fetches_the_canonical_brain_and_drafter():
    brain, mtp, mdir = _canonical_brain()
    rel = "scripts/setup/download_gguf_models.sh"
    assert _shell_var(rel, "BRAIN_FILE") == brain
    assert _shell_var(rel, "MTP_FILE") == mtp
    assert _shell_var(rel, "MODELS_DIR") == "${HOME}/models/" + mdir


def test_installer_stages_models_where_the_downloader_puts_them():
    _brain, _mtp, mdir = _canonical_brain()
    assert _shell_var("scripts/setup/install-jetson.sh", "MODELS_DIR") == "${HOME}/models/" + mdir


def test_llama_unit_loads_the_files_the_downloader_fetched():
    brain, mtp, mdir = _canonical_brain()
    unit = _read("scripts/setup/systemd/llama-server.service")
    assert f"--model %h/models/{mdir}/{brain}" in unit
    assert f"--model-draft %h/models/{mdir}/{mtp}" in unit


def test_no_setup_script_downloads_a_retired_brain():
    retired = re.compile(r"qwen2\.5|qwen-?2\.5|Qwen2\.5|llama-?3|phi-?3|mistral-7b|ollama pull", re.I)
    offenders = set()
    for name in sorted(os.listdir(SETUP)):
        if name.endswith((".sh", ".py")) and retired.search(_read(f"scripts/setup/{name}")):
            offenders.add(f"scripts/setup/{name}")
    _ratchet("retired_brain_download", offenders)


# ── the ports first boot depends on ───────────────────────────────────────────────────────────────────────────────────────

def test_canonical_ports_are_the_ones_the_units_and_installer_use():
    brain = _canonical_port(r"llama-server` on `:(\d+)`")
    tts = _canonical_port(r"sidecar \(port (\d+)\)")
    router = _canonical_port(r'sidecar_port: "(\d+)"')
    assert f"--port {brain}" in _read("scripts/setup/systemd/llama-server.service")
    assert f"KOKORO_SIDECAR_PORT={tts}" in _read("scripts/setup/systemd/kokoro-tts.service")
    assert f"--port {router}" in _read("scripts/setup/systemd/functiongemma-router.service")
    install = _read("scripts/setup/install-jetson.sh")
    assert f"http://localhost:{brain}/health" in install
    assert "http://localhost:8000/health" in install
    assert "--port 8000" in _read("scripts/setup/systemd/zoe-data.service")


def test_readme_service_table_matches_canonical_ports_and_voice():
    brain = _canonical_port(r"llama-server` on `:(\d+)`")
    tts = _canonical_port(r"sidecar \(port (\d+)\)")
    rows = {r.split("|")[1].strip(): r for r in _read("README.md").splitlines() if r.startswith("| ") and r.count("|") >= 5}
    assert f"| {brain} |" in rows["Brain (LLM)"]
    assert "| 8000 |" in rows["API"]
    assert f"| {tts} (loopback) |" in rows["TTS"]
    # CANONICAL: Kokoro is the ONLY voice; the Edge TTS / espeak-ng waterfall runs only under an explicit non-default mode
    assert "then Edge TTS" not in rows["TTS"], "README describes the retired-by-default TTS waterfall as the voice"


# ── first run ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_first_run_path_is_the_postgres_installer():
    offenders = set()
    for rel in ("QUICK-START.md", "README.md"):
        if re.search(r"^\s*\./scripts/setup/init_databases\.sh", _read(rel), re.M):
            offenders.add(rel)
    for rel in ("scripts/setup/init_databases.sh", "scripts/setup/init_databases.py"):
        if re.search(r"sqlite3", _read(rel)):
            offenders.add(rel)
    assert "migrate.sh" in _read("scripts/setup/install-jetson.sh"), "the installer no longer runs the PostgreSQL migrations"
    _ratchet("first_run_uses_sqlite", offenders)


def test_router_download_lands_where_canonical_and_the_unit_look():
    want = _canonical_port(r'stage2_artifact_dir: "~/models/([\w.-]+)"')
    unit = _read("scripts/setup/systemd/functiongemma-router.service")
    assert f"/models/{want}/" in unit
    got = _shell_var("scripts/setup/download_gguf_models.sh", "ROUTER_DIR")
    _ratchet("router_artifact_dir", set() if got == "${HOME}/models/" + want else {"scripts/setup/download_gguf_models.sh"})


def test_first_boot_links_do_not_default_to_one_lan_address():
    rel = "services/zoe-data/routers/panel_provision.py"
    lan = re.search(r'os\.environ\.get\("ZOE_BASE_URL",\s*"https?://(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.', _read(rel))
    _ratchet("lan_address_default", {rel} if lan else set())


def test_known_drift_entries_say_why():
    for check, entries in KNOWN_DRIFT.items():
        for rel, why in entries.items():
            assert os.path.exists(os.path.join(REPO, rel)), f"{check}: {rel} no longer exists: delete the entry"
            assert len(why) > 20, rel
