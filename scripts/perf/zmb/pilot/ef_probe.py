"""Does a leaner MiniLM embedding function (pad to the longest doc, pinned ORT threads) change the vectors?

Chroma's ``ONNXMiniLM_L6_V2`` (and Zoe's ``_ZoeMiniLM`` subclass of it) pads EVERY document to 256 tokens
(``onnx_mini_lm_l6_v2.py`` ``tokenizer.enable_padding(length=256)``): a 28-character voice turn costs a full
256-token forward pass, and a batch of B documents allocates B x 256 activations (the RSS growth seen in
batch_rss_probe.py). MiniLM mean-pools with the attention mask, so padding should not change the vector. This
probe MEASURES that instead of assuming it:

* cosine(original, lean) for 300 synthetic turns (min / mean),
* per-document embedding latency of each, one at a time, and in a batch of 8,
* RSS after the run, ONNX intra-op threads 1 vs the default.

Run with the bake-off venv (mp_run.sh). Prints JSON.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from zmb.pilot import household  # noqa: E402


def rss() -> int:
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS"):
            return int(line.split()[1]) // 1024
    return -1


def make_lean(threads: int):
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
    from functools import cached_property

    class LeanMiniLM(ONNXMiniLM_L6_V2):
        """Same model, same tokenizer, pad to the longest document in the batch; ORT threads pinned."""
        @cached_property
        def tokenizer(self):  # type: ignore[override]
            import os
            from tokenizers import Tokenizer
            tok = Tokenizer.from_file(os.path.join(self.DOWNLOAD_PATH, self.EXTRACTED_FOLDER_NAME, "tokenizer.json"))
            tok.enable_truncation(max_length=256)
            tok.enable_padding(pad_id=0, pad_token="[PAD]")        # no fixed length: pad to the longest in the batch
            return tok

        def _forward(self, documents, batch_size: int = 32):  # type: ignore[override]
            """Chroma encodes documents one at a time (so a fixed padding length is the only thing that makes the
            batch rectangular); encode_batch pads to the longest document instead."""
            import numpy as np
            outs = []
            for i in range(0, len(documents), batch_size):
                enc = self.tokenizer.encode_batch(list(documents[i:i + batch_size]))
                ids = np.array([e.ids for e in enc], dtype=np.int64)
                mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
                hidden = self.model.run(None, {"input_ids": ids, "attention_mask": mask,
                                               "token_type_ids": np.zeros_like(ids)})[0]
                m = np.broadcast_to(np.expand_dims(mask, -1), hidden.shape)
                emb = (hidden * m).sum(1) / np.clip(m.sum(1), 1e-9, None)
                outs.append(self._normalize(emb).astype(np.float32))
            return np.concatenate(outs)

        @cached_property
        def model(self):  # type: ignore[override]
            import os
            so = self.ort.SessionOptions()
            so.log_severity_level = 3
            so.intra_op_num_threads = threads
            so.inter_op_num_threads = 1
            so.graph_optimization_level = self.ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            return self.ort.InferenceSession(
                os.path.join(self.DOWNLOAD_PATH, self.EXTRACTED_FOLDER_NAME, "model.onnx"),
                providers=["CPUExecutionProvider"], sess_options=so)

    return LeanMiniLM()


def timeit(fn, docs, b: int) -> "list[float]":
    out = []
    for i in range(0, len(docs), b):
        t0 = time.perf_counter()
        fn(docs[i:i + b])
        out.append((time.perf_counter() - t0) * 1000 / len(docs[i:i + b]))
    return out


def main() -> int:
    import numpy as np
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
    turns, _q, _m = household.generate()
    docs = [t["text"] for t in turns if t["user"] != household.GUEST][:300]
    out: dict = {"docs": len(docs), "rss_start": rss()}
    orig = ONNXMiniLM_L6_V2()
    orig(docs[:1])                                       # load
    out["rss_after_orig_load"] = rss()
    a = np.concatenate([np.array(orig(docs[i:i + 8])) for i in range(0, 300, 8)])   # chunks of 8: a 32-batch of padded-256 docs costs ~760 MB
    out["orig_ms_per_doc_b1"] = round(statistics.median(timeit(orig, docs[:100], 1)), 1)
    out["orig_ms_per_doc_b8"] = round(statistics.median(timeit(orig, docs[:96], 8)), 1)
    out["rss_after_orig_runs"] = rss()
    res: dict = {}
    for threads in (1, 2):
        lean = make_lean(threads)
        lean(docs[:1])
        b = np.concatenate([np.array(lean(docs[i:i + 8])) for i in range(0, 300, 8)])
        cos = (a * b).sum(1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1))
        res[f"lean_threads_{threads}"] = {
            "cosine_min": round(float(cos.min()), 6), "cosine_mean": round(float(cos.mean()), 6),
            "ms_per_doc_b1": round(statistics.median(timeit(lean, docs[:100], 1)), 1),
            "p95_ms_b1": round(sorted(timeit(lean, docs[:100], 1))[94], 1),
            "ms_per_doc_b8": round(statistics.median(timeit(lean, docs[:96], 8)), 1),
            "ms_per_doc_b32": round(statistics.median(timeit(lean, docs[:96], 32)), 1)}
        out["rss_after_lean_%d" % threads] = rss()
    out["lean"] = res
    out["orig_p95_ms_b1"] = round(sorted(timeit(orig, docs[:100], 1))[94], 1)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
