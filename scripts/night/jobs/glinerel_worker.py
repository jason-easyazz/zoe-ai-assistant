#!/usr/bin/env python3
"""The GLiNEREL model call for the night-extract job, as a STANDALONE process in its own venv (``~/.zoe/venvs/glinerel``).

It exists so the model's imports (torch, glinerel-fast) and ~1.2 GB never enter zoe-data or the night window: the job starts it under
``systemd-run --user --scope -p MemoryMax=1600M -p MemorySwapMax=0 nice -n 10`` and reads one JSON document back.

    stdin   {"texts": [...], "entities": [labels], "relations": [labels], "batch_size": 8, "entity_threshold": 0.15, "relation_threshold": 0.03}
    stdout  {"results": [{"entities": [...], "relations": [...]}, ...], "load_s": .., "infer_s": .., "peak_rss_mb": .., "threads": ..}

Offline by construction (``HF_HUB_OFFLINE=1``, CPU only, no GPU). The model is loaded ONCE per process, whatever the number of texts, so the
job sends every member's turns in one call. Stdlib + torch + glinerel_fast only; imports nothing from the repo (it must run from the venv).
Exit 0 ok, 3 the model/package is missing (the job reports ``model_missing``), 1 any other failure. Texts are never written to disk or logged.
"""
from __future__ import annotations

import json
import os
import sys
import time

REPO_ID = "VAGOsolutions/SauerkrautLM-GLiNEREL-128m"
REVISION = "5b8448f983c2bc4058ea6359ec67589cd41422b9"       # pinned: the revision the lab scored
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ.setdefault("HF_HUB_CACHE", os.path.expanduser("~/.zoe/models/glinerel/hub"))


def _status(key: str) -> float:
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for ln in fh:
                if ln.startswith(key):
                    return int(ln.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


def main() -> int:
    req = json.load(sys.stdin)
    threads = int(os.environ.get("OMP_NUM_THREADS", "4"))
    try:
        import torch
        from glinerel_fast import GLiNERel
        from huggingface_hub import snapshot_download

        torch.set_num_threads(threads)
        t0 = time.perf_counter()
        path = snapshot_download(REPO_ID, revision=REVISION, local_files_only=True)
        model = GLiNERel.from_pretrained(path, device="cpu", precision="fp32")
    except (ImportError, OSError, ValueError) as exc:        # no venv package / no model files: a setup problem, not a crash
        print(json.dumps({"error": "model_missing", "kind": type(exc).__name__}))
        return 3
    load_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    texts = list(req.get("texts") or [])
    results = model.extract(texts, entities=req["entities"], relations=req["relations"], entity_threshold=float(req["entity_threshold"]),
                            relation_threshold=float(req["relation_threshold"]), batch_size=int(req.get("batch_size") or 8)) if texts else []
    print(json.dumps({"results": results, "load_s": round(load_s, 2), "infer_s": round(time.perf_counter() - t1, 2),
                      "peak_rss_mb": round(_status("VmHWM"), 0), "threads": threads}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": "failed", "kind": type(exc).__name__}))
        raise SystemExit(1)
