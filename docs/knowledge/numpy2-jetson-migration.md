---
type: knowledge
status: WIP — prep-only, checkpointed mid-research at reboot
topic: numpy-2 migration on the Jetson Orin NX (JetPack 6.2.1 / CUDA 12.6)
created: 2026-08-04
---

# numpy-2 Jetson migration — prep notes (WIP)

**Status: WIP.** Deliverables 1 (premise check) and the box survey are verified.
Deliverable **2 (bump matrix) is DERIVED AS OF 2026-08-10** — not "complete". Deliverables
3 (build plan) and 4 (window runbook) are still INCOMPLETE. Resume from "Open items" at the
bottom, which records which items the inventory has already closed.

> **Why the matrix carries an as-of date instead of a COMPLETE marker.** It is a matrix over
> package versions on a live box and over a manifest that other PRs edit, so it goes stale on
> somebody else's merge — and it did, twice, within a week of being written (#1651 and #1656).
> "COMPLETE" tells a resuming operator *not to re-derive it*, which is precisely the wrong
> instruction for a document with that decay property. **Re-derive before acting on it**; the
> derivation is one command and is named in the matrix section below.

## Verdict (deliverable 1) — PREBUILT numpy-2-COMPATIBLE WHEELS EXIST

**The handoff premise is STALE.** The claim "NVIDIA ships only numpy-1
onnxruntime-gpu wheels for JP6/cu126" was true of `onnxruntime-gpu` **1.23.0**
(NVIDIA said so on the forums, 2025-08-12) but is **no longer true**:
`onnxruntime-gpu` **1.24.0** is published on the live jp6/cu126 index and is
**built against NumPy 2.x headers**.

Consequence: **the migration is a wheel install, not an on-device source build.**
Deliverable 3 (the onnxruntime source-build script) is very likely MOOT.

### The index MOVED — first correction

| | |
|---|---|
| `https://pypi.jetson-ai-lab.dev` | **DEAD** — `getaddrinfo ENOTFOUND`, domain no longer resolves |
| `https://pypi.jetson-ai-lab.io` | **LIVE** — devpi instance, this is the current one |

Exposed indexes: `jp6/cu126`, `jp6/cu128`, `jp6/cu129`, `sbsa/cu130`, `sbsa/dev`.
Our box is **jp6/cu126**.

devpi file-URL form (the `../../+f/...` hrefs on the listing page resolve
index-scoped, NOT root-scoped — root `/jp6/+f/...` returns 404):

```
https://pypi.jetson-ai-lab.io/jp6/cu126/+f/<sha3>/<sha13>/<filename>
```

### onnxruntime-gpu wheels available on jp6/cu126 (read 2026-08-04)

| version | filename | sha256 |
|---|---|---|
| **1.24.0** | `onnxruntime_gpu-1.24.0-cp310-cp310-linux_aarch64.whl` | `d980b934b9a29c1a9d6f39751edd7662b69fadd75556a10ff363773a58ce0950` |
| 1.23.0 | `onnxruntime_gpu-1.23.0-cp310-cp310-linux_aarch64.whl` | `4ebe6a8902dc7708434b2e1541b3fe629ebf434e16ab5537d1d6a622b42c622b` |

Direct URL for 1.24.0 (verified HTTP 200, 73,617,978 bytes):

```
https://pypi.jetson-ai-lab.io/jp6/cu126/+f/d98/0b934b9a29c1a/onnxruntime_gpu-1.24.0-cp310-cp310-linux_aarch64.whl
```

`cp310` matches the box's Python 3.10.12. Only cp310 is published — Python
version is therefore pinned by the wheel, not a free choice.

### Evidence that 1.24.0 is numpy-2 built

1. **Declared dependency has no upper bound.** From the wheel's `METADATA`:
   `Requires-Dist: numpy>=1.21.6`, `Requires-Python: >=3.10`. Necessary but not
   sufficient — a numpy-1-built wheel can declare the same thing.
2. **C-ABI signature in `onnxruntime/capi/onnxruntime_pybind11_state.so`.** The
   `import_array()` machinery embeds the module name it imports; NumPy 2 renamed
   `numpy.core` → `numpy._core`. The 1.24.0 binary contains:
   - `numpy._core._multiarray_umath` **and** `numpy.core._multiarray_umath`
     (the numpy-2 header's forward path plus its numpy-1 fallback)
   - `numpy._core.multiarray failed to import` (numpy-2 form of the error string)
   - `_ZN8pybind116detail27import_numpy_core_submoduleEPKc` — pybind11's
     numpy-2-aware helper (pybind11 ≥ 2.12)
   - `numpy.bool` (restored in numpy 2)

   A numpy-1-built module contains only the `numpy.core.…` forms.

   The residual string `module was compiled against NumPy C-API version 0x%x
   (NumPy 1.20)` is **not** counter-evidence: that is the standard compat-warning
   template baked into the headers, naming the `NPY_FEATURE_VERSION` baseline a
   numpy-2 build targets for backward compatibility.

3. **The detector was negative-controlled — it does go red.** Scanning the box's
   installed extensions with `.np2probe/abi_scan.sh` cleanly separates the two
   populations:
   - **numpy-1 form** (`numpy.core.multiarray failed to import`): every
     `numpy` 1.26.4 submodule, `spacy`, `thinc`, `blis`, and the whole of
     `/usr/lib/python3/dist-packages` (`scipy`, `pandas`, `matplotlib`).
   - **numpy-2 form** (`numpy._core.…`): `sklearn`, `pyarrow`, and the installed
     `onnxruntime` 1.23.2 in `~/.local`.

   Both outcomes occur in the same scan, so the instrument discriminates rather
   than always saying "numpy 2". Per `[[feedback_verify_your_instruments]]`.

**Not yet done:** an actual `import onnxruntime` under numpy 2 in a throwaway
venv. Deliberately deferred — at survey time the box reported ~0 GB available RAM
with the brain live, and the voice-stack memory doctrine forbids burning headroom.
This is the first step of the execution window, not of prep.

### Reference thread

NVIDIA forum, *"Onnxruntime-gpu wheel with numpy 2.x support"* (topic 341795):
AastaLLL, 2025-08-12 — *"Our prebuilt supports numpy 1.x for dependency issues.
You can build it from the source if a higher numpy version is preferred."* That
answer is about **1.23.0** and predates the 1.24.0 wheel. Do not cite it as
current.

## Box ground truth (measured 2026-08-04, read-only)

| fact | measured value | note |
|---|---|---|
| L4T | **R36.4.3** (`/etc/nv_tegra_release`) | handoff said 36.4.7 — **handoff is wrong**, box is 36.4.3 |
| JetPack | 6.2.1+b38 | `nvidia-jetpack` meta package |
| CUDA | 12.6.68 | `/usr/local/cuda-12.6` |
| OS / Python | Ubuntu 22.04.5, Python 3.10.12 | cp310 wheels only |
| RAM | 15 GB total, ~12 GB used, **~0 GB available** | brain live; no build headroom |
| swap | 50 GB `/swapfile` + 8× ~1 GB zram | swap-backed build is feasible |
| cores | 8 | bound `-j` if a build is ever needed |
| disk | 1.4 TB free on `/` | not a constraint |
| ccache | **NOT installed** | would need installing if a source build happens |

### Live ONNX runtime is CPU-only — investigate before assuming

`~/.local/lib/python3.10/site-packages` has **`onnxruntime` 1.23.2** (upstream
PyPI CPU build), **not** `onnxruntime-gpu`:

- `get_available_providers()` → `['AzureExecutionProvider', 'CPUExecutionProvider']`
- no `libonnxruntime_providers_cuda.so` / `_tensorrt.so` present
- and it is **already numpy-2-header-built**

Two consequences to chase on resume:

1. If nothing on the box actually uses `onnxruntime-gpu`, then the NVIDIA
   numpy-1 wheel is **not in the dependency set at all**, and the stated reason
   for `numpy<2` is doubly stale. The real blocker is then whatever
   `services/zoe-data/requirements.txt` lines 42–52 describe.
2. ~~Conversely there may be a **service venv** distinct from `~/.local` that does
   carry `onnxruntime-gpu`.~~ **RESOLVED by the completed inventory (see "Bump
   matrix" below): there is no service venv.** Re-verified on the box: `zoe-data`
   runs `/usr/bin/python3 -m uvicorn`, the Kokoro sidecar runs `/usr/bin/python3`,
   and the FunctionGemma router is a llama.cpp C++ binary with no Python at all —
   so the host-native service surface shares ONE interpreter and one numpy. Venvs
   do exist elsewhere on the box (`~/.hermes/*`, `~/.spikes/pipecat-voice`, uv
   caches), but nothing in the service surface runs from them, so they neither
   carry the services' `onnxruntime-gpu` nor participate in the flip.

### ctranslate2

`https://pypi.jetson-ai-lab.io/jp6/cu126/ctranslate2/` is a **plain upstream-PyPI
passthrough** (devpi mirror; entries point at `files.pythonhosted.org`, incl.
ancient cp36 macOS wheels). There is **no Jetson-specific ctranslate2 build** —
so if ctranslate2 is needed, it comes from upstream aarch64 manylinux wheels, not
from a Jetson source build. Whether anything still uses it (Moonshine replaced
Whisper; it may be dead) is **still open** — the repo inventory had not returned
at checkpoint time.

## Tooling produced

- `scripts/maintenance/numpy_abi_scan.sh` — read-only numpy-C-ABI signature
  scanner. Usage: `bash scripts/maintenance/numpy_abi_scan.sh <site-packages-dir>`.
  Prints one line per compiled extension with the numpy-1 or numpy-2 marker. This
  is the verification instrument for the whole migration: run it before and after
  to prove every extension is numpy-2 built. It reads files only — no imports, no
  installs, so it is safe to run against a live service venv.

  **It is fail-CLOSED, so check the exit code, not just the output**: `2` = root is not a
  directory, `4` = no compiled extensions found at all (wrong root), `5` = INCOMPLETE — the
  walk failed part-way *or* at least one `.so` could not be read and was never examined.
  Exit 5 with a plausible-looking list of matches is the case to watch for: the matches are
  real, the report is partial, and a before/after comparison against it under-counts.

- `scripts/maintenance/requirements_drift_check.py` (#1656) — reconciles a requirements file
  against what is actually installed in the running interpreter. This is the bump matrix's
  derivation tool; see the matrix section.

## Open items (resume brief)

1. **Repo inventory / bump matrix (deliverable 2)** — derived, and **re-derived box-first on
   2026-08-10**; see "Bump matrix" below. Not a closed item: it decays on any merge that
   touches the manifest and on any out-of-band install, so **re-run
   `requirements_drift_check.py` before acting on it**. Still outstanding from it: the
   four-package host uninstall, and the `soxr`/`moonshine-voice`/`fastembed` pins.
2. **Resolve the onnxruntime-gpu question** — the venv half is **closed** (no
   service venv exists; see the correction above and the bump matrix). What remains
   open is narrower: `abi_scan.sh` the single shared `~/.local` site-packages before
   and after, and confirm whether anything imports `onnxruntime` at all — which
   decides whether `onnxruntime-gpu` is in scope, and therefore whether the original
   `numpy<2` rationale survives.
3. **ctranslate2 dead-or-alive** — grep for `ctranslate2` / `faster_whisper` /
   `WhisperModel` in live `services/` code.
4. **Deliverable 3** — likely reduces to "not needed"; keep only a stub rationale
   unless (2) shows a genuinely numpy-1-only compiled dep with no wheel.
5. **Deliverable 4** — the window runbook. With wheels instead of a build the
   window shrinks dramatically; re-estimate once (1)–(3) land.
6. ~~Quote `services/zoe-data/requirements.txt` lines 42–52 verbatim into this note.~~
   **Dropped — superseded and actively harmful.** Line numbers in that file move on every
   merge (the `numpy<2` ceiling has already walked 52 → 102), so a verbatim quote is a
   snapshot that rots silently. Run `requirements_drift_check.py` instead; it reads the
   current file against the current box.

Nothing in this prep compiled anything, stopped any service, or changed any pin.

## Bump matrix (re-derived BOX-FIRST, as of 2026-08-10)

### The manifest is a RECORD, not a plan — derive from the box

`services/zoe-data/requirements.txt` **is never installed by anything.** The audit is in
#1656. zoe-data runs host-native out of `/usr/bin/python3` + `~/.local/lib/python3.10/
site-packages`, installed out of band; `.github/workflows/deploy.yml:158` carries its own
**hardcoded** nine-package `pip3 install --user` list (apscheduler, tzlocal, sqlalchemy,
alembic, asyncpg, psycopg2-binary, pywebpush, py-vapid, segno) — none of them numpy-adjacent
— and installs nothing else. So a bump matrix expressed as edits to the requirements file is
a **record of intent**, not an install plan: the migration is an on-box operation, and the
sequence is **box first, file second**.

Concretely, the file already disagrees with the box. Re-derive with the drift checker that
#1656 shipped, which is exactly this reconciliation:

```bash
python3 scripts/maintenance/requirements_drift_check.py services/zoe-data/requirements.txt
```

Measured 2026-08-10 — **38 requirements checked, 6 drifted**: `uvicorn` declared `==0.49.0` /
installed 0.34.0; `websockets` `==16.1.1` / 14.1; `ag-ui-protocol` `==0.1.19` / 0.1.14;
`aiortc` `==1.15.0` / 1.14.0; `av` `==17.1.0` / **16.1.0**; `python-json-logger` listed but
**NOT INSTALLED**. Plus two `unpinned` rows (below). Every one of those is a pin nothing ever
applied — read the file as evidence of what someone intended, and `pip list` as truth.

### Deltas found on this re-derivation (against the previous matrix)

| previous claim | status 2026-08-10 | evidence |
|---|---|---|
| `requirements.txt:52` is the `numpy<2` ceiling | **line moved → `:102`** | #1656 restructured the file |
| `validate.yml:145` is the CI mirror | **line moved → `:153`** (`pip install --quiet "numpy<2"`) | same |
| "add explicit pins: `onnxruntime`, `transformers`" | **DONE** — `onnxruntime==1.23.2`:108, `transformers==5.5.0`:128 | #1656 |
| "add explicit pins: `soxr`, `moonshine-voice`, `fastembed`" | **STILL OPEN** — `soxr` is absent from the file entirely (installed 1.1.0); `moonshine-voice`:115 and `fastembed`:131 are unversioned (installed 0.0.62 / 0.8.0) | drift checker reports both as `unpinned` |
| "delete first: requirements pins `pyannote.audio>=3.3.2`, `silero-vad>=5.1`" | **DONE — those pins no longer exist** | #1651 removed them; only the explanatory comment at `:105` survives |
| "delete first: `pip uninstall ctranslate2 faster-whisper kokoro-onnx useful-moonshine-onnx`" | **NOT DONE — all four are still installed** | `pip show`: ctranslate2 4.7.1, faster-whisper 1.2.1, kokoro-onnx 0.5.0, useful-moonshine-onnx 20251121 |
| "latent leak: `install-jetson-agent.sh:48` installs `mempalace chromadb` UNPINNED" | **FIXED** | #1651 — `MEMPALACE_PIN`/`CHROMADB_PIN` at `:49-50` |

The uninstall row is the one that matters: it was an **operator step, and operator steps do
not run themselves**. `ctranslate2` is `Required-by: faster-whisper`, so remove
`faster-whisper` first (or both in one command) or pip will refuse/orphan it.

### Installed versions of the numpy-1-compiled surface (measured on the box)

`numpy` **1.26.4**. `onnxruntime-gpu` is **NOT installed at all** — only CPU `onnxruntime`
1.23.2, which the scanner confirms is already numpy-2-header-built. The binaries that need
numpy-2 aarch64 provenance, at the versions actually present: `torch` 2.8.0, `scipy` 1.15.3,
`chroma-hnswlib` 0.7.6, `moonshine-voice` 0.0.62, `numba` 0.65.1, `llvmlite` 0.47.0, `av`
16.1.0, `soundfile` 0.13.1, `soxr` 1.1.0, `Resemblyzer` 0.1.4, `pyarrow` 23.0.1 (already
numpy-2), `spacy` 3.8.14 / `thinc` 8.3.13 / `blis` 1.3.3 (numpy-1). Most of these appear in
**no requirements file** — another reason the box, not the manifest, is the inventory.

### Carried forward from the repo-inventory pass (unchanged)

**The flip is ATOMIC across the host-native surface**: no *service* venv exists — zoe-data
(`ExecStart=/usr/bin/python3 -m uvicorn`), the Kokoro sidecar (`ExecStart=/usr/bin/python3`)
and every maintenance script share `/usr/bin/python3` + `~/.local` site-packages and therefore
ONE numpy; the FunctionGemma router is a llama.cpp C++ binary with no Python at all. Staging
per-service requires introducing venvs first (a separate decision).

Scope note, because a `pyvenv.cfg` sweep does find venvs on this box: `~/.hermes/self-evolution`,
`~/.hermes/hermes-agent`, `~/.spikes/pipecat-voice` and the uv archive cache each have one. None
is on the service surface, so none participates in the flip — but that also means they will **not**
be carried by it, and any of them that later needs numpy 2 must be handled separately.

**Must change together:** `services/zoe-data/requirements.txt:102` (`numpy<2` — the only ceiling in
the repo), `.github/workflows/validate.yml:153` (CI mirror — must match or CI lies), and
`labs/setfit-router/requirements.txt` (`numpy==1.26.4` — artifact-training pin; the committed
`router_head_logreg.joblib` needs re-validation under numpy 2, not necessarily retraining).

**Hard-hold through the bump (data-loss, not ABI):** `chromadb==0.6.3` + `mempalace==3.3.1`
(moving chromadb risks the documented silent drawer-write drops), `scikit-learn==1.7.2` /
`joblib==1.5.3` (artifact lock).

**Binaries needing numpy-2 aarch64 provenance:** `torch 2.8.0` — **the real blocker: no wheel
index or install provenance is recorded anywhere in the repo or on the host** (empirical comfort:
ADR-ambient-voice-framework.md:124-131 records numpy 2.2.6 + this torch coexisting with only a
warning; the Kokoro sidecar was already made numpy-agnostic via tolist()+struct.pack). Also:
`chroma-hnswlib 0.7.6`, `moonshine-voice 0.0.62`, `scipy`, `numba/llvmlite`, `av`, `soundfile`,
`soxr`, `Resemblyzer`. Live CPU `onnxruntime 1.23.2` is ALREADY numpy-2-header-built (scanner-verified).

**Add explicit pins BEFORE the bump (currently invisible to the resolver):** `soxr` (absent
from the file), plus versions on `moonshine-voice`:115 and `fastembed`:131. *(`onnxruntime`
and `transformers` were on this list and are now pinned — #1656.)*

**Delete first — shrinks the problem, and is STILL OUTSTANDING on the box:** `pip uninstall
faster-whisper ctranslate2 kokoro-onnx useful-moonshine-onnx` (four numpy-1 binaries, zero code
references; ctranslate2's presence in the `numpy<2` comment is stale — a deploy-preflight guard
already fails any whisper resurrection). The requirements half of this step is **done**: #1651
removed the `pyannote.audio` / `silero-vad` pins (neither installed nor imported — VAD loads a
raw .onnx), so do not go looking for them.

**CPU-only confirmation:** every ONNX consumer in the live stack (Moonshine, Silero VAD, Smart
Turn, fastembed) uses CPUExecutionProvider only — no CUDA/TensorRT provider anywhere in the repo,
so the gpu-wheel question above is insurance, not the critical path.
