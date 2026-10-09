"""The 12B SPEED SWEEP's pure pieces: the config grid, the stages, the probes' prompts, the parsers, the winner rule and the table.

``night_window.py --speed-sweep`` owns the execution (lock, preflight, stops, compaction, one transient 12B per config, restore); this module owns what is
decided and what is printed, with no I/O, so the tests can pin it. Why a sweep at all (owner, 2026-10-09 11:45): the live 4B was squeezed with a measured flag
research (build b11194, ``--load-mode mmap+mlock``, MTP draft, q8_0 KV, ``--swa-full``...); the 12B had only been tried with the parked unit's flags, and it refuses
full GPU offload on this JetPack (one 6,637 MiB cudaMalloc fails; ``-ngl 30`` loads and decodes at 3.6-5.9 tok/s). The grid below asks, in order: does the 4B's
build + load mode lift the ceiling, does a smaller file buy layers under it, how many layers fit, and what do KV type, batch sizes and threads do to the speed.
"""
from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import Any, Optional

MIB = 1024 * 1024

#: model key -> path under $HOME. ``qat`` / ``q4km`` are the two real files; ``iq4xs`` / ``q3km`` are SPEED probes made offline with llama-quantize --allow-requantize from
#: the QAT file (no downloads): re-quantising a 4-bit file below 4 bits costs quality, so they can show how many layers fit under the ceiling but are never the default
#: (rocks rule: the 12B's quality is the owner's call).
SWEEP_MODEL_FILES = {
    "qat": "models/gemma4-12b-qat/gemma-4-12b-it-qat-q4_0.gguf",
    "q4km": "models/gemma4-12b/gemma-4-12B-it-Q4_K_M.gguf",
    "iq4xs": "models/gemma4-12b-sweep/gemma-4-12b-it-qat-requant-IQ4_XS.gguf",
    "q3km": "models/gemma4-12b-sweep/gemma-4-12b-it-qat-requant-Q3_K_M.gguf",
}
DEFAULT_MODELS = ("qat", "q4km")            # the only files that may become the window's default
LAYERS = 48                                 # gemma4 12B block_count (GGUF header)
STAGE_NAMES = {0: "control", 1: "offload", 2: "q4km", 3: "quant", 4: "ngl", 5: "kv", 6: "batch", 7: "threads", 8: "draft"}
#: a loaded config whose night-shape decode is below this share of the best decode "clearly loses": the rest of its family is skipped
LOSE_RATIO = 0.75
#: the night-mind pass sends 2,800-token prompts and wants about 320 tokens back (its real shape); the speed probe is the window's fixed 1.6k / 96
NIGHT_PROMPT_TOKENS, NIGHT_ANSWER_TOKENS, SPEED_ANSWER_TOKENS = 2800, 320, 96
#: lines of the synthetic night-shape prompt that give ~2,800 Gemma tokens (calibrated against llama-server /tokenize on 2026-10-09)
NIGHT_LINES = 87


@dataclasses.dataclass(frozen=True)
class SweepConfig:
    name: str
    family: str
    build: str = "parked"                  # "parked" = the parked unit's binary (b9733), "b11194" = the live 4B's build
    model: str = "qat"                     # a SWEEP_MODEL_FILES key
    ngl: Optional[int] = 30                # None = no --n-gpu-layers (with fit on, llama.cpp chooses)
    ctx: int = 8192
    kv: str = "q8_0"
    batch: int = 512
    ubatch: int = 128
    threads: Optional[int] = None          # None = the build's default
    load_mode: str = "mlock"               # "mlock" (the build's own spelling of a locked mmap), "mmap" (no lock), "none" (no mmap)
    fit: str = "default"                   # "off" | "on" | "default" (flag omitted)
    no_kv_offload: bool = False
    unified: bool = True                   # GGML_CUDA_ENABLE_UNIFIED_MEMORY=1 in the 12B's environment (the window's default on a Jetson)
    draft: Optional[str] = None            # a 12B draft GGUF (none exists on disk on 2026-10-09)
    only_if_prev_failed: bool = False      # a fallback of the config before it: runs only when that one did not load
    stop_family_on_fail: bool = False      # an ascending family: the first config that does not load ends it
    note: str = ""

    def desc(self) -> str:
        bits = [self.build, self.model, "ngl " + ("auto" if self.ngl is None else str(self.ngl)), f"ctx {self.ctx}", f"kv {self.kv}", f"b{self.batch}/ub{self.ubatch}"]
        bits.append({"mlock": "mlock", "mmap": "mmap (no lock)", "none": "no-mmap"}.get(self.load_mode, self.load_mode))
        if self.fit != "default":
            bits.append("fit " + self.fit)
        if self.threads:
            bits.append(f"-t {self.threads}")
        if self.no_kv_offload:
            bits.append("no-kv-offload")
        bits.append("uma" if self.unified else "no-uma")
        if self.draft:
            bits.append("draft")
        return " ".join(bits)


#: today's best known-good: the parked unit's binary, 30 of 48 layers, ctx 8192, the parked unit's batch sizes and --mlock, unified memory on
CONTROL = SweepConfig("C0", "control", note="today's best known config (3.6-5.9 tok/s) re-measured in THIS run, the yardstick")


def stage_configs(stage: int, best: SweepConfig, draft: Optional[str] = None, have_models: "Optional[set[str]]" = None) -> "list[SweepConfig]":
    """The configs of ``stage``, derived from ``best`` (the best default-eligible config measured so far; stage 1 is fixed). ``have_models``: the model keys whose files exist
    (None = all). Pure: the same inputs give the same list, which is what the dry run prints and the tests pin."""
    have = set(SWEEP_MODEL_FILES) if have_models is None else set(have_models)
    def R(base: SweepConfig, **kw: Any) -> SweepConfig:      # a derived config never inherits the base's note or its family-control flags
        return dataclasses.replace(base, **{"note": "", "only_if_prev_failed": False, "stop_family_on_fail": False, **kw})
    full = LAYERS + 2          # the "-ngl 99" of the parked unit; any number >= layers + 1 offloads the output layer too
    out: "list[SweepConfig]" = []
    if stage == 0:
        out = [CONTROL]
    elif stage == 1:
        b = R(CONTROL, build="b11194", ngl=99, fit="off", family="offload")
        out = [R(b, name="1a", note="THE KEY QUESTION: the 4B's build + --load-mode mmap+mlock + full offload (-ngl 99), --fit off like the live 4B unit"),
               R(b, name="1b", ngl=None, fit="on", note="the same with --fit on and no -ngl: llama.cpp chooses what fits"),
               R(b, name="1c", unified=False, note="full offload without GGML_CUDA_ENABLE_UNIFIED_MEMORY (plain cudaMalloc)"),
               R(b, name="1d", load_mode="mmap", note="full offload, mmap without the lock (the weights stay file-backed)"),
               R(b, name="1e", ngl=30, note="b11194 at the known-good -ngl 30: does the newer build itself change speed or footprint"),
               R(b, name="1f", ngl=30, unified=False, note="-ngl 30 without unified memory: does the lever matter at all")]
    elif stage == 2:
        out = [R(best, name="2a", family="q4km", model="q4km", note="the Q4_K_M file (7,039 MiB, 6% bigger than the QAT file) at the best layer count so far"),
               R(best, name="2b", family="q4km", model="q4km", ngl=max(1, (best.ngl or full) - 2), only_if_prev_failed=True, note="Q4_K_M two layers lower (its layers are bigger)")]
    elif stage == 3:
        n = best.ngl or full
        out = [R(best, name="3a", family="quant", model="q3km", note="Q3_K_M (5,790 MiB, re-quantised from the QAT file: speed probe only) at the same layer count"),
               R(best, name="3b", family="quant", model="q3km", ngl=min(full, n + 4), stop_family_on_fail=True, note="Q3_K_M with 4 more layers under the ceiling"),
               R(best, name="3c", family="quant", model="q3km", ngl=min(full, n + 8), stop_family_on_fail=True, note="Q3_K_M with 8 more layers"),
               R(best, name="3d", family="quant", model="iq4xs", ngl=min(full, n + 2), note="IQ4_XS (6,366 MiB, re-quantised: speed probe only) with 2 more layers")]
    elif stage == 4:
        n = best.ngl or full
        out = [R(best, name=f"4-{k}", family="ngl", ngl=k, stop_family_on_fail=True, note=f"-ngl {k} on the best default file (the ascending sweep ends at the first failure)")
               for k in (30, 32, 34, 38, 42, 48) if k > n]
    elif stage == 5:
        n = best.ngl or full
        out = [R(best, name="5a", family="kv", kv="q4_0", note="KV q4_0 at the best layer count (q8_0 is the baseline)")]
        if best.ngl is not None and n < full:
            out.append(R(best, name="5b", family="kv", kv="q4_0", ngl=n + 2, stop_family_on_fail=True, note="KV q4_0 with 2 more layers (the saved KV bytes are what the ceiling counts)"))
    elif stage == 6:
        out = [R(best, name="6a", family="batch", batch=2048, ubatch=512, note="llama.cpp's default batch / ubatch (the parked unit uses 512 / 128)"),
               R(best, name="6b", family="batch", batch=512, ubatch=256),
               R(best, name="6c", family="batch", batch=256, ubatch=64, note="small compute buffers: may buy a layer under the ceiling")]
    elif stage == 7:
        out = [R(best, name="7a", family="threads", threads=6, note="6 CPU threads for the CPU layers"),
               R(best, name="7b", family="threads", threads=4, note="4 CPU threads"),
               R(best, name="7c", family="threads", no_kv_offload=True, note="--no-kv-offload: the KV cache stays in host RAM")]
    elif stage == 8:
        if draft:
            out = [R(best, name="8a", family="draft", build="b11194", draft=draft, note="a 12B draft GGUF found on disk, --spec-draft-n-max 4 like the 4B")]
    return [c for c in out if c.model in have]


def plan_text(seed: SweepConfig = CONTROL, stages: "tuple[int, ...]" = tuple(range(9)), draft: Optional[str] = None, have_models: "Optional[set[str]]" = None) -> "list[str]":
    """The dry run's config list. Stages 2+ depend on the winner so far: the list shows them derived from ``seed`` and says so."""
    lines = []
    for s in stages:
        cfgs = stage_configs(s, seed, draft, have_models)
        lines.append(f"stage {s} ({STAGE_NAMES[s]})" + ("" if s <= 1 else f": derived at run time from the best default-eligible config so far (shown from the seed {seed.name})"))
        if s == 8 and not cfgs:
            lines.append("  8: no 12B draft GGUF on disk (ls /home/zoe/models/gemma4-12b*): no config")
        for c in cfgs:
            lines.append(f"  {c.name:<5}{c.desc()}" + (f"  [{c.note}]" if c.note else "") + ("  (only if the one before it did not load)" if c.only_if_prev_failed else "")
                         + ("  (ends the family at the first failure)" if c.stop_family_on_fail else ""))
    return lines


# ── the probes ───────────────────────────────────────────────────────────────

def night_prompt() -> str:
    """A fixed, invented ~2,800-token prompt (the night-mind pass's real input size) that asks for a ~320-token answer. Different words from the 1.6k speed probe, so no prompt cache can be shared."""
    notes = "\n".join(f"Log {i}: Mirela mended the lanterns at Ostrand harbour on day {i}, and Pavel sorted the copper kettles before supper." for i in range(1, NIGHT_LINES + 1))
    return notes + "\nWrite a detailed account of what Mirela and Pavel did over these days, grouped by theme, in about 300 words."


def chat_payload(prompt: str, max_tokens: int) -> str:
    return json.dumps({"model": "local", "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens, "temperature": 0, "stream": False, "cache_prompt": False})


def parse_probe(body: str) -> "dict[str, Any]":
    try:
        d = json.loads(body)
    except (ValueError, TypeError):
        d = {}
    d = d if isinstance(d, dict) else {}
    t, u = d.get("timings") or {}, d.get("usage") or {}
    return {"prompt_tokens": u.get("prompt_tokens"), "completion_tokens": u.get("completion_tokens"),
            "prefill_tps": round(float(t["prompt_per_second"]), 1) if t.get("prompt_per_second") else None,
            "decode_tps": round(float(t["predicted_per_second"]), 2) if t.get("predicted_per_second") else None}


# ── the parsers ──────────────────────────────────────────────────────────────

def parse_nvmap_client_mib(text: str, pid: "Optional[int]" = None) -> "Optional[float]":
    """``sudo cat /sys/kernel/debug/nvmap/iovmm/clients``: ``user  llama-server  <pid>  <size>K`` rows. The size of the client with ``pid``; without a pid (or no match) the largest
    llama-server client (during a sweep the other llama-servers are stopped). None when unreadable."""
    rows = []
    for line in (text or "").splitlines():
        m = re.match(r"\s*\S+\s+(\S+)\s+(\d+)\s+(\d+)K\s*$", line)
        if m:
            rows.append((m.group(1), int(m.group(2)), int(m.group(3)) / 1024.0))
    if pid is not None:
        for _n, p, mib in rows:
            if p == pid:
                return round(mib, 1)
    cand = [mib for n, _p, mib in rows if "llama" in n]
    return round(max(cand), 1) if cand else None


def parse_main_pid(text: str) -> "Optional[int]":
    m = re.search(r"(?:MainPID=)?\b(\d+)\b", (text or "").strip())
    return int(m.group(1)) if m and int(m.group(1)) > 0 else None


def parse_buffers(text: str) -> "dict[str, float]":
    """llama.cpp's own allocation lines (``CUDA0 model buffer size = 4163.31 MiB``, ``CUDA0 KV buffer``, ``CUDA0 compute buffer``, ``CUDA_Host ...``, ``CPU_Mapped ...``)
    summed per ``<device> <kind>`` key, MiB. Says what counts as device memory under each load mode."""
    out: "dict[str, float]" = {}
    for m in re.finditer(r"\b(CUDA\d*|CUDA_Host|CPU|CPU_Mapped|CUDA_Mapped|Vulkan\d*)\s+(model|KV|compute|RS|output)\s+buffer size\s*=\s*([\d.]+)\s*MiB", text or ""):
        key = f"{m.group(1)} {m.group(2)}"
        out[key] = round(out.get(key, 0.0) + float(m.group(3)), 1)
    return out


def failure_reason(journal: str) -> str:
    """Why a 12B did not load, from its own journal lines (never the prompt): the allocation that failed (``cudaMalloc of 4814 MiB``), else the last error line, else 'died'."""
    sizes = [float(m.group(1)) for m in re.finditer(r"allocating\s+([\d.]+)\s*MiB on device \d+: cudaMalloc failed", journal or "")]
    if sizes:
        return f"cudaMalloc of {sizes[-1]:.0f} MiB failed (out of memory)"
    errs = [ln.strip() for ln in (journal or "").splitlines() if re.search(r"\bE\b.*(error|failed)|unknown argument|invalid argument|error: ", ln)]
    return (errs[-1][:140] if errs else "the process died before it was healthy (no error line)")


# ── the winner ───────────────────────────────────────────────────────────────

def decode_of(row: "dict[str, Any]") -> "Optional[float]":
    return row.get("b_decode") if row.get("b_decode") is not None else row.get("a_decode")


def is_candidate(row: "dict[str, Any]") -> bool:
    return bool(row.get("loaded")) and decode_of(row) is not None and row.get("status") == "ok"


def pick_best(rows: "list[dict[str, Any]]", models: "tuple[str, ...]" = DEFAULT_MODELS) -> "Optional[dict[str, Any]]":
    """The loaded config with the highest decode tok/s on the night-mind shape (prefill breaks ties), among the files that may become the default (``models``)."""
    c = [r for r in rows if is_candidate(r) and (r.get("config") or {}).get("model") in models]
    return max(c, key=lambda r: (decode_of(r), r.get("b_prefill") or r.get("a_prefill") or 0.0)) if c else None


def config_from_row(row: "dict[str, Any]") -> SweepConfig:
    fields = {f.name for f in dataclasses.fields(SweepConfig)}
    return SweepConfig(**{k: v for k, v in (row.get("config") or {}).items() if k in fields})


def clearly_loses(row: "dict[str, Any]", best_decode: "Optional[float]") -> bool:
    d = decode_of(row)
    return bool(best_decode and is_candidate(row) and d is not None and d < LOSE_RATIO * best_decode)


# ── the table ────────────────────────────────────────────────────────────────

def _f(v: Any, nd: int = 1) -> str:
    return "-" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def table_lines(rows: "list[dict[str, Any]]") -> "list[str]":
    """The doc's table: config -> loaded? load s, prefill, decode (1.6k probe and the night shape), nvmap MiB, MemAvailable low."""
    out = ["| id | config | loaded | load s | 1.6k prefill | 1.6k decode | night prefill | night decode | nvmap MiB | MemAvail low MiB | result |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['name']} | {r['desc']} | {'yes' if r.get('loaded') else 'NO'} | {_f(r.get('load_s'))} | {_f(r.get('a_prefill'))} | {_f(r.get('a_decode'), 2)} | "
                   f"{_f(r.get('b_prefill'))} | {_f(r.get('b_decode'), 2)} | {_f(r.get('nvmap_mib'), 0)} | {_f(r.get('mem_low_mib'), 0)} | {(r.get('why') or r.get('status') or '')[:110]} |")
    return out


def load_state(text: str) -> "dict[str, Any]":
    try:
        d = json.loads(text)
    except (ValueError, TypeError):
        d = {}
    d = d if isinstance(d, dict) else {}
    d.setdefault("rows", [])
    return d


def merge_rows(old: "list[dict[str, Any]]", new: "list[dict[str, Any]]") -> "list[dict[str, Any]]":
    """A later run replaces a row with the same id (a re-measure), otherwise appends: the table is the union over the invocations."""
    by = {r["name"]: r for r in old}
    order = [r["name"] for r in old]
    for r in new:
        if r["name"] not in by:
            order.append(r["name"])
        by[r["name"]] = r
    return [by[n] for n in order]


def parse_stages(text: str) -> "tuple[int, ...]":
    """``all`` | ``0-4`` | ``5,6,7`` | ``1,3-4`` -> sorted stage numbers (ValueError on anything outside 0-8)."""
    t = (text or "all").strip().lower()
    if t == "all":
        return tuple(range(9))
    got: "set[int]" = set()
    for part in t.split(","):
        a, _, b = part.strip().partition("-")
        lo, hi = int(a), int(b or a)
        got.update(range(lo, hi + 1))
    if not got or min(got) < 0 or max(got) > 8:
        raise ValueError(f"stages must be within 0-8: {text!r}")
    return tuple(sorted(got))


def draft_candidates(listing: str) -> "list[str]":
    """Lines of ``ls`` output (paths) that look like a speculative draft / MTP head for the 12B (never an mmproj)."""
    return [p for p in (ln.strip() for ln in (listing or "").splitlines()) if p.endswith(".gguf") and re.search(r"mtp|draft|assistant", Path(p).name, re.I) and "mmproj" not in p]


def render_markdown(state_text: str) -> str:
    st = load_state(state_text)
    return "\n".join(table_lines(st["rows"])) + "\n"


if __name__ == "__main__":      # python3 scripts/night/speed_sweep.py <sweep-state.json>  ->  the markdown table
    import sys
    print(render_markdown(Path(sys.argv[1]).read_text()), end="")
