"""Offline coverage for `scripts/maintenance/requirements_drift_check.py`.

The drift check itself can only tell the truth on the Jetson (that is where the
zoe-data packages are installed), so this suite does NOT assert anything about
the live environment. It asserts the COMPARATOR — the instrument — because a
detector that silently matches nothing would report "no drift" forever and be
indistinguishable from a healthy box.

Every positive assertion here is paired with a negative control: the detector is
shown going red on input it must reject, not merely green on input it accepts
(`[[feedback_verify_your_instruments]]`).

Stdlib only (no pip, no network, no live host) — safe on the slim GitHub lane.
"""
from __future__ import annotations

import importlib.util
import re
from importlib.metadata import version
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
_TOOL = ROOT / "scripts" / "maintenance" / "requirements_drift_check.py"
_REQUIREMENTS = ROOT / "services" / "zoe-data" / "requirements.txt"


def _load_tool():
    spec = importlib.util.spec_from_file_location("requirements_drift_check", _TOOL)
    assert spec and spec.loader, f"cannot load {_TOOL}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


drift = _load_tool()


def _verdicts(text: str) -> dict[str, str]:
    return {f.name: f.verdict for f in drift.check(text)}


# ── version comparison ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "have,operator,want,expected",
    [
        ("0.34.0", "==", "0.32.0", False),   # the live uvicorn drift
        ("0.34.0", "==", "0.34.0", True),
        ("0.28.1", "==", "0.28.0", False),   # the live httpx drift
        ("14.1", "==", "14.0", False),       # the live websockets drift
        ("2.0.51", ">=", "2.0", True),
        ("1.13.0", ">=", "1.18.5", False),
        ("3.3.1", "!=", "3.6.0", True),
        ("1.7.2", "~=", "1.7", True),
        ("2.0.0", "~=", "1.7", False),
    ],
)
def test_satisfies(have: str, operator: str, want: str, expected: bool) -> None:
    assert drift.satisfies(have, operator, want) is expected


def test_numpy_2_ceiling_is_actually_enforced() -> None:
    """`numpy<2` guards both canonical rocks — the checker MUST catch a 2.x install.

    A NumPy 2 install is a C-ABI break for kokoro, moonshine-voice, chroma-hnswlib,
    ctranslate2, soundfile, soxr and Resemblyzer. `mempalace + chromadb` alone
    resolves to numpy 2.2.6 (measured 2026-08-02), so this is a live failure mode,
    not a hypothetical one.
    """
    assert drift.satisfies("2.2.6", "<", "2") is False   # must be caught
    assert drift.satisfies("1.26.4", "<", "2") is True   # must not false-positive
    assert drift.satisfies("2.0.0", "<", "2") is False   # boundary: 2.0.0 is NOT < 2

    # End-to-end through check(), using a name that is installed everywhere this
    # suite runs, so the verdict is deterministic rather than runner-dependent.
    have = version("pytest")
    assert _verdicts(f"pytest<{have}\n") == {"pytest": "MISMATCH"}
    assert _verdicts(f"pytest<={have}\n") == {"pytest": "match"}


def test_unparseable_version_is_uncheckable_not_silently_ok() -> None:
    """An unreadable version must NOT be treated as satisfied."""
    assert drift.parse_version("not-a-version") is None
    assert drift.satisfies("not-a-version", ">=", "1.0") is None


# ── end-to-end classification, with negative controls ───────────────────────

def test_matching_pin_reports_match() -> None:
    text = f"pytest=={version('pytest')}\n"
    assert _verdicts(text) == {"pytest": "match"}


def test_negative_control_drifted_pin_is_detected() -> None:
    """NEGATIVE CONTROL: the detector must go RED on a pin that disagrees.

    Without this, a comparator that returned "match" unconditionally would make
    every other assertion in this file pass while detecting nothing.
    """
    text = "pytest==0.0.0\n"
    findings = drift.check(text)
    assert [f.verdict for f in findings] == ["MISMATCH"]
    assert findings[0].is_drift is True
    assert findings[0].installed == version("pytest")


def test_absent_package_is_drift_but_optional_marker_downgrades_it() -> None:
    absent = "zoe-definitely-not-installed-xyz>=1.0\n"
    assert _verdicts(absent) == {"zoe-definitely-not-installed-xyz": "MISSING"}
    assert drift.check(absent)[0].is_drift is True

    marked = "zoe-definitely-not-installed-xyz>=1.0  # drift-optional: degrades\n"
    assert _verdicts(marked) == {"zoe-definitely-not-installed-xyz": "missing-optional"}
    assert drift.check(marked)[0].is_drift is False


def test_optional_marker_never_excuses_a_version_mismatch() -> None:
    """`drift-optional` means "may be absent", NOT "may be any version"."""
    text = "pytest==0.0.0  # drift-optional: degrades\n"
    findings = drift.check(text)
    assert [f.verdict for f in findings] == ["MISMATCH"]
    assert findings[0].is_drift is True


def test_extras_and_unpinned_and_markers_parse() -> None:
    text = (
        "# a comment\n"
        "\n"
        "-r other.txt\n"
        "uvicorn[standard]==0.34.0\n"
        "segno\n"
        "aiosqlite==0.22.1 ; python_version >= '3.8'\n"
    )
    parsed = {name: spec for name, spec, _ in drift.iter_requirements(text)}
    assert parsed == {
        "uvicorn": "==0.34.0",
        "segno": "",
        "aiosqlite": "==0.22.1",
    }
    assert _verdicts("segno\n")["segno"] in {"unpinned", "MISSING"}


# ── direct-URL pins (`name @ URL`) ──────────────────────────────────────────
# requirements-py312.txt pins the CPU torch build by wheel URL. A URL carries no
# comparison operator, so before #1706 it fell through to "unpinned" — a CUDA
# torch, or any other build, reported clean (Greptile/Codex, #1706).

_WHEEL = "https://download.pytorch.org/whl/cpu/torch-2.14.0%2Bcpu-cp312-cp312-manylinux_2_28_aarch64.whl"


def test_wheel_url_version_is_read_from_the_filename() -> None:
    assert drift.wheel_url_version(_WHEEL) == "2.14.0+cpu"
    assert drift.wheel_url_version("https://x/pkg-1.0.tar.gz") is None      # sdist: no version claim
    assert drift.wheel_url_version("git+https://x/pkg.git@v1") is None


def test_direct_url_pin_matches_only_the_exact_artifact(monkeypatch) -> None:
    have = version("pytest")
    url = f"https://files.example/pytest-{have}-py3-none-any.whl"
    monkeypatch.setattr(drift, "installed_direct_url", lambda name: url)
    assert _verdicts(f"pytest @ {url}\n") == {"pytest": "match"}


def test_negative_control_direct_url_pin_catches_another_version(monkeypatch) -> None:
    """NEGATIVE CONTROL: a different version must be MISMATCH, not "unpinned"."""
    url = "https://files.example/pytest-0.0.0-py3-none-any.whl"
    monkeypatch.setattr(drift, "installed_direct_url", lambda name: url)
    findings = drift.check(f"pytest @ {url}\n")
    assert [f.verdict for f in findings] == ["MISMATCH"]
    assert findings[0].is_drift is True


def test_negative_control_direct_url_pin_catches_an_index_install(monkeypatch) -> None:
    """Same version, but installed from an index (no direct_url.json) or from a
    different URL: the pin promised an exact ARTIFACT, so that is drift too — it
    is how a same-numbered CUDA build would slip past a version-only check."""
    have = version("pytest")
    url = f"https://files.example/pytest-{have}-py3-none-any.whl"
    monkeypatch.setattr(drift, "installed_direct_url", lambda name: None)
    assert _verdicts(f"pytest @ {url}\n") == {"pytest": "MISMATCH"}
    monkeypatch.setattr(drift, "installed_direct_url", lambda name: url.replace("example", "other"))
    assert _verdicts(f"pytest @ {url}\n") == {"pytest": "MISMATCH"}


def test_direct_url_compares_the_decoded_url_not_the_raw_string(monkeypatch) -> None:
    """uv can record PEP 610 `direct_url.json` with the path percent-DECODED
    (`torch-2.14.0+cpu-…`) while the manifest carries `%2B`. Same artifact, so it
    must match — `--check` treats any MISMATCH as fatal (Codex, #1706)."""
    have = version("pytest")
    manifest = f"https://files.example/pytest-{have}%2Bx-py3-none-any.whl"
    recorded = f"https://FILES.example/pytest-{have}+x-py3-none-any.whl"
    monkeypatch.setattr(drift, "installed_version", lambda name: f"{have}+x")
    monkeypatch.setattr(drift, "installed_direct_url", lambda name: recorded)
    assert _verdicts(f"pytest @ {manifest}\n") == {"pytest": "match"}
    # negative control: normalisation must not blur a genuinely different artifact
    monkeypatch.setattr(drift, "installed_direct_url",
                        lambda name: recorded.replace("+x-py3", "+y-py3"))
    assert _verdicts(f"pytest @ {manifest}\n") == {"pytest": "MISMATCH"}


def test_py312_manifest_torch_line_is_a_checked_url_pin() -> None:
    """Vacuity guard on the real manifest: the torch line must parse as a URL pin."""
    text = (ROOT / "services" / "zoe-data" / "requirements-py312.txt").read_text(encoding="utf-8")
    specs = {name: spec for name, spec, _ in drift.iter_requirements(text)}
    assert specs["torch"].startswith("@ "), specs.get("torch")
    assert drift.wheel_url_version(specs["torch"][1:].strip()) == "2.14.0+cpu"


# ── extraneous distributions (installer-owned venvs) ───────────────────────
# For a venv BUILT from a manifest, a distribution nothing asks for is drift too:
# `uv pip install -r` never removes, so a leftover `webrtcvad` (the sdist that
# cannot import on setuptools 84) would otherwise be certified by --check
# (Codex, #1706). Allowed = the manifest's dependency closure (extras and markers
# honoured) + the --no-deps names, which are allowed but NOT walked.

_FAKE = {
    "fastapi": ["starlette>=0.40", "pydantic>=2"],
    "starlette": ["anyio>=3"],
    "anyio": [],
    "pydantic": ["pydantic-core==2.0", "email-validator ; extra == 'email'"],
    "pydantic-core": [],
    "email-validator": [],
    "uvicorn": ["click", "uvloop ; extra == 'standard' and sys_platform != 'win32'"],
    "click": [],
    "uvloop": [],
    "resemblyzer": ["webrtcvad>=2.0.10", "typing"],
    "webrtcvad": [],
    "typing": [],
}


def _extraneous(text: str, no_deps=(), installed=None) -> list[str]:
    dists = {k: _FAKE.get(k, []) for k in (installed or _FAKE)}
    return sorted(f.name for f in drift.extraneous(text, no_deps, dists=dists))


def test_closure_honours_extras_and_skips_unrequested_ones() -> None:
    installed = ["fastapi", "starlette", "anyio", "pydantic", "pydantic-core",
                 "uvicorn", "click", "uvloop"]
    assert _extraneous("fastapi==1\nuvicorn[standard]==1\n", installed=installed) == []
    # without the extra, uvloop is not asked for by anything
    assert _extraneous("fastapi==1\nuvicorn==1\n", installed=installed) == ["uvloop"]


def test_negative_control_leftover_distribution_is_extraneous() -> None:
    """NEGATIVE CONTROL: a stale package must be reported by NAME, as drift."""
    installed = ["fastapi", "starlette", "anyio", "pydantic", "pydantic-core", "webrtcvad"]
    found = drift.extraneous("fastapi==1\n", (), dists={k: _FAKE[k] for k in installed})
    assert [(f.name, f.verdict, f.is_drift) for f in found] == [("webrtcvad", "EXTRANEOUS", True)]


def test_no_deps_names_are_allowed_but_not_walked() -> None:
    """resemblyzer is installed --no-deps, so ITS declared deps (webrtcvad,
    typing) are exactly what must still be flagged."""
    installed = ["fastapi", "starlette", "anyio", "pydantic", "pydantic-core",
                 "resemblyzer", "webrtcvad", "typing"]
    assert _extraneous("fastapi==1\n", ["resemblyzer"], installed=installed) == ["typing", "webrtcvad"]
    assert _extraneous("fastapi==1\n", [], installed=installed) == ["resemblyzer", "typing", "webrtcvad"]


def test_names_are_compared_canonically() -> None:
    dists = {"pydantic-core": [], "pydantic": ["Pydantic_Core==2.0"]}
    assert drift.extraneous("pydantic==2\n", (), dists=dists) == []


def _fake_dist(tmp_path, name: str, files: dict[str, bool]):
    """A dist-info whose RECORD lists ``files``; True = present on disk."""
    from importlib.metadata import PathDistribution
    info = tmp_path / f"{name}-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n")
    rows = [f"{name}-1.0.dist-info/METADATA,,", f"{name}-1.0.dist-info/RECORD,,"]
    for rel, present in files.items():
        rows.append(f"{rel},,")
        if present:
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_text("x")
    (info / "RECORD").write_text("\n".join(rows) + "\n")
    return PathDistribution(info)


def test_distribution_with_files_missing_is_damaged(tmp_path) -> None:
    """Measured on #1706: `uv pip sync` removing the stale `webrtcvad` sdist also
    deleted `webrtcvad.py` + `_webrtcvad*.so`, which `webrtcvad-wheels` installs
    at the SAME paths. The survivor's metadata still says installed, so neither a
    version check nor sync notices. Python 3.12's `Distribution.files` silently
    drops missing paths, so RECORD must be read directly."""
    ok = _fake_dist(tmp_path, "intact", {"intact.py": True, "__pycache__/intact.cpython-312.pyc": False})
    hit = _fake_dist(tmp_path, "webrtcvad_wheels", {"webrtcvad.py": False, "_webrtcvad.so": True})
    assert drift.damaged_distributions([ok, hit]) == {"webrtcvad-wheels": ["webrtcvad.py"]}
    # negative control: all files present (pyc caches are not required) -> clean
    assert drift.damaged_distributions([ok]) == {}


# ── the parser must actually see the tracked file ───────────────────────────

def test_tracked_requirements_file_parses_completely() -> None:
    """Vacuity guard: every `==` pin in the real file must be picked up.

    The tracked file is 90% prose comments. A regex that quietly skipped its
    requirement lines would make the Jetson lane report a clean environment
    regardless of the truth — the exact failure this whole change is about.
    """
    text = _REQUIREMENTS.read_text(encoding="utf-8")
    parsed = list(drift.iter_requirements(text))
    assert len(parsed) >= 30, f"only parsed {len(parsed)} requirements — parser is broken"

    # Count `==` pins in the raw file (ignoring comment lines) and match the parse.
    raw_exact = [
        line for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#") and "==" in line.split("#", 1)[0]
    ]
    parsed_exact = [name for name, spec, _ in parsed if "==" in spec]
    assert len(parsed_exact) == len(raw_exact), (
        f"raw file has {len(raw_exact)} exact pins, parser found {len(parsed_exact)}"
    )


def test_onnxruntime_is_declared() -> None:
    """`voice_vad.py` / `voice_turn.py` import it directly — it must be declared.

    It was a load-bearing undeclared voice-path dependency until 2026-08-06.
    """
    names = {name for name, _, _ in drift.iter_requirements(_REQUIREMENTS.read_text())}
    assert "onnxruntime" in names

    importers = [
        p for p in (ROOT / "services" / "zoe-data").glob("*.py")
        if re.search(r"^\s*import onnxruntime", p.read_text(encoding="utf-8"), re.MULTILINE)
    ]
    assert importers, "no module imports onnxruntime — drop the pin instead of keeping it"
