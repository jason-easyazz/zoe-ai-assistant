#!/usr/bin/env python3
"""A loopback OpenAI-compatible ``POST /v1/embeddings`` server over an ONNX embedding model ALREADY ON DISK.

Why it exists: Hindsight 0.10.2 slim ships no local embedder (``HINDSIGHT_API_EMBEDDINGS_PROVIDER=local`` fails at startup, the
``openai`` provider defaults to api.openai.com). The bake-off needs a ``127.0.0.1`` endpoint, and the box already holds the router's
bge-small (``BAAI/bge-small-en-v1.5``, the int8 ``model_optimized.onnx`` that fastembed cached) - so this reuses that file and its
tokenizer and does the same pooling fastembed does (CLS token, L2-normalised), i.e. the SAME 384-d vectors the router sees.
(docs/research/memory-system-decision-2026-10-05.md section 3.1; /home/zoe/.zoe/bakeoff-2026-10/G0-install-report.md section 7.)

Hard rules, each one tested:

* **No downloads, ever.** The model directory is searched on disk only (an explicit ``--model-dir`` / ``ZMB_EMBED_MODEL_DIR``, fastembed's
  cache, the HF hub cache, then Chroma's cached MiniLM as the fallback); if nothing is there it REFUSES with the places it looked.
  Nothing here imports fastembed, huggingface_hub or chromadb, and ``HF_HUB_OFFLINE=1`` is set before anything else loads.
* **Loopback only.** It will not bind a non-loopback address. Default ``127.0.0.1:11501``.
* **Small.** onnxruntime with the CPU memory arena off and 2 threads; compute batch cap 8 (a bigger request is split, never run at once);
  at most 64 inputs and 1 MiB per request; ``--selftest`` fails if RSS exceeds 150 MB.
* **Standard shape.** ``{"object":"list","data":[{"object":"embedding","index":i,"embedding":[...]}],"model":...,"usage":{...}}`` with
  ``encoding_format`` ``float`` or ``base64`` (the openai SDK asks for base64 by default and decodes it).

    scripts/perf/zmb/embed_shim.py --selftest          # embed three sentences, print numbers, exit 0/1 (no port opened)
    scripts/perf/zmb/embed_shim.py --serve             # 127.0.0.1:11501 until stopped
"""
from __future__ import annotations

import argparse
import base64
import glob
import ipaddress
import json
import os
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, Sequence

os.environ.setdefault("HF_HUB_OFFLINE", "1")          # belt and braces: nothing here may reach the network
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11501
MODEL_ID = "bge-small-en-v1.5"
BATCH_CAP = 8                  # texts per onnx run; a larger request is split into chunks of this size
MAX_INPUTS = 64                # texts per HTTP request
MAX_BODY_BYTES = 1 << 20       # 1 MiB
MAX_TEXT_CHARS = 20_000        # per text; the tokenizer truncates to MAX_TOKENS anyway
MAX_TOKENS = 512
TOKEN_BUDGET = 512             # rows x longest-row tokens per onnx run (bounds the quadratic attention memory)
RSS_LIMIT_MB = 150.0

#: (pooling, model file candidates in preference order) - bge pools the CLS token, MiniLM averages the masked tokens
_BGE_FILES = ("model_optimized.onnx", "model_quantized.onnx", "onnx/model.onnx", "model.onnx")
_MINILM_FILES = ("onnx/model.onnx", "model.onnx")


class ShimError(Exception):
    """A request the shim refuses: carries the HTTP status and an OpenAI-style error body."""

    def __init__(self, status: int, message: str, kind: str = "invalid_request_error"):
        super().__init__(message)
        self.status, self.message, self.kind = status, message, kind


class ModelNotFound(RuntimeError):
    """No embedding model on disk. The shim never downloads one."""


class Embedder(Protocol):
    model_id: str
    dim: int

    def embed(self, texts: Sequence[str]) -> "tuple[list[list[float]], list[int]]":
        """(unit-length vectors, token counts) for ``texts`` (at most ``BATCH_CAP``)."""


# ── finding the model on disk (never downloading) ────────────────────────────

def _is_loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _pick_file(d: Path, names: "tuple[str, ...]") -> "Optional[Path]":
    for n in names:
        p = d / n
        if p.is_file():
            return p
    return None


def _candidate_dirs(explicit: "Optional[str]") -> "list[tuple[Path, str, tuple[str, ...]]]":
    """(directory, pooling, model files) in search order. Pure path arithmetic + glob: no I/O beyond listing."""
    home = Path(os.environ.get("HOME", "~")).expanduser()
    out: "list[tuple[Path, str, tuple[str, ...]]]" = []
    if explicit:
        out.append((Path(explicit).expanduser(), "cls", _BGE_FILES))
        out.append((Path(explicit).expanduser(), "mean", _MINILM_FILES))
    fast_roots = []
    if os.environ.get("FASTEMBED_CACHE_PATH"):
        fast_roots.append(Path(os.environ["FASTEMBED_CACHE_PATH"]))
    import tempfile
    fast_roots += [Path(tempfile.gettempdir()) / "fastembed_cache", Path("/tmp/fastembed_cache")]
    for root in fast_roots:                                     # the router's own copy (fastembed's HF layout)
        for pat in ("models--[Qq]drant--bge-small-en-v1.5-onnx-[Qq]/snapshots/*",):
            for d in sorted(glob.glob(str(root / pat))):
                out.append((Path(d), "cls", _BGE_FILES))
    for d in sorted(glob.glob(str(home / ".cache/huggingface/hub/models--BAAI--bge-small-en-v1.5/snapshots/*"))):
        out.append((Path(d), "cls", ("onnx/model.onnx",) + _BGE_FILES))
    out.append((home / ".cache/chroma/onnx_models/all-MiniLM-L6-v2", "mean", _MINILM_FILES))   # Chroma's cached MiniLM
    return out


def find_model(explicit: "Optional[str]" = None) -> "tuple[Path, Path, str, str]":
    """``(onnx file, tokenizer.json, pooling, model_id)`` from what is ALREADY on disk, else ``ModelNotFound``."""
    explicit = explicit or os.environ.get("ZMB_EMBED_MODEL_DIR")
    tried: list[str] = []
    for d, pooling, names in _candidate_dirs(explicit):
        tried.append(str(d))
        f = _pick_file(d, names)
        tok = d / "tokenizer.json"
        if not tok.is_file():
            tok = d / "onnx" / "tokenizer.json" if (d / "onnx" / "tokenizer.json").is_file() else tok
        if f is not None and tok.is_file():
            model_id = MODEL_ID if pooling == "cls" else "all-MiniLM-L6-v2"
            return f, tok, pooling, model_id
    raise ModelNotFound("no embedding model on disk and this shim never downloads one; looked in: "
                        + ", ".join(dict.fromkeys(tried)))


# ── the real embedder (onnxruntime + tokenizers, imported lazily) ────────────

class OnnxEmbedder:
    def __init__(self, onnx_path: Path, tokenizer_path: Path, pooling: str, model_id: str):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self._np = np
        self.model_id = model_id
        self.pooling = pooling
        so = ort.SessionOptions()
        so.intra_op_num_threads = 2
        so.inter_op_num_threads = 1
        so.enable_cpu_mem_arena = False        # the arena is where onnxruntime's RSS goes to grow
        so.enable_mem_pattern = False
        so.log_severity_level = 3
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC     # measured: about -30 MB at load
        so.add_session_config_entry("session.disable_prepacking", "1")                # measured: no second copy of the weights
        so.add_session_config_entry("session.use_device_allocator_for_initializers", "1")
        self._sess = ort.InferenceSession(str(onnx_path), sess_options=so, providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self._sess.get_inputs()}
        self._tok = Tokenizer.from_file(str(tokenizer_path))
        self._tok.enable_truncation(max_length=MAX_TOKENS)
        self._tok.no_padding()                 # padded by hand, per sub-batch, so attention memory stays bounded
        self.dim = len(self.embed(["warm up"])[0][0])
        self._trim()

    @staticmethod
    def _trim(collect: bool = True) -> None:
        """Hand freed heap back to the OS: the load-time peak would otherwise stay in the RSS."""
        try:
            import ctypes
            import gc
            if collect:
                gc.collect()
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception:  # noqa: BLE001 - not the GNU allocator: the RSS number is what it is
            pass

    def _run(self, rows: "list[Any]") -> "tuple[Any, Any]":
        np = self._np
        width = max(len(e.ids) for e in rows)
        ids = np.zeros((len(rows), width), dtype=np.int64)
        mask = np.zeros((len(rows), width), dtype=np.int64)
        for i, e in enumerate(rows):
            ids[i, :len(e.ids)] = e.ids
            mask[i, :len(e.ids)] = 1
        feed: "dict[str, Any]" = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = self._sess.run(None, feed)[0]
        if hidden.ndim == 3:
            if self.pooling == "cls":
                vec = hidden[:, 0]
            else:
                m = mask[:, :, None].astype(hidden.dtype)
                vec = (hidden * m).sum(axis=1) / np.maximum(m.sum(axis=1), 1e-9)
        else:
            vec = hidden
        return vec / np.maximum(np.linalg.norm(vec, axis=1, keepdims=True), 1e-12), mask.sum(axis=1)

    def embed(self, texts: Sequence[str]) -> "tuple[list[list[float]], list[int]]":
        """Vectors in input order. Sub-batches are cut so ``rows x longest <= TOKEN_BUDGET``: attention memory is quadratic in
        length, so eight 512-token texts would not fit the RSS limit but eight 40-token facts do."""
        if not texts:
            return [], []
        enc = self._tok.encode_batch(list(texts))
        out_v: "list[Any]" = [None] * len(enc)
        out_t = [0] * len(enc)
        order = sorted(range(len(enc)), key=lambda i: len(enc[i].ids))
        i = 0
        while i < len(order):
            j = i + 1
            while j < len(order) and (j + 1 - i) * len(enc[order[j]].ids) <= TOKEN_BUDGET:
                j += 1
            idx = order[i:j]
            vecs, counts = self._run([enc[k] for k in idx])
            for n, k in enumerate(idx):
                out_v[k] = vecs[n].astype("float32").tolist()
                out_t[k] = int(counts[n])
            i = j
        self._trim(collect=False)          # the next request starts from the trimmed heap, not from this one's peak
        return out_v, out_t


# ── the protocol (pure: no sockets, no model) ────────────────────────────────

def parse_request(raw: bytes) -> "tuple[list[str], str, Optional[int], str]":
    """``(texts, encoding_format, dimensions, model)`` or ``ShimError``."""
    if len(raw) > MAX_BODY_BYTES:
        raise ShimError(413, f"request body over {MAX_BODY_BYTES} bytes")
    try:
        body = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ShimError(400, f"body is not valid JSON: {exc}") from None
    if not isinstance(body, dict):
        raise ShimError(400, "body must be a JSON object")
    inp = body.get("input")
    if isinstance(inp, str):
        texts = [inp]
    elif isinstance(inp, list) and inp and all(isinstance(t, str) for t in inp):
        texts = list(inp)
    else:
        raise ShimError(400, "'input' must be a string or a non-empty list of strings")
    if len(texts) > MAX_INPUTS:
        raise ShimError(400, f"{len(texts)} inputs; at most {MAX_INPUTS} per request (compute batch cap is {BATCH_CAP})")
    if any(not t.strip() for t in texts):
        raise ShimError(400, "an input is empty")
    if any(len(t) > MAX_TEXT_CHARS for t in texts):
        raise ShimError(400, f"an input is over {MAX_TEXT_CHARS} characters")
    fmt = body.get("encoding_format") or "float"
    if fmt not in ("float", "base64"):
        raise ShimError(400, f"encoding_format {fmt!r} is not supported (float, base64)")
    dims = body.get("dimensions")
    if dims is not None and not (isinstance(dims, int) and not isinstance(dims, bool) and dims > 0):
        raise ShimError(400, "'dimensions' must be a positive integer")
    return texts, fmt, dims, str(body.get("model") or "")


def encode_vector(vec: "Sequence[float]", fmt: str) -> "Any":
    if fmt == "base64":
        return base64.b64encode(struct.pack(f"<{len(vec)}f", *vec)).decode("ascii")
    return [float(x) for x in vec]


def embed_all(embedder: Embedder, texts: "list[str]") -> "tuple[list[list[float]], int]":
    """Embed in compute batches of at most ``BATCH_CAP``; returns the vectors and the total token count."""
    vecs: "list[list[float]]" = []
    tokens = 0
    for i in range(0, len(texts), BATCH_CAP):
        v, t = embedder.embed(texts[i:i + BATCH_CAP])
        vecs.extend(v)
        tokens += sum(t)
    return vecs, tokens


def handle(method: str, path: str, raw: bytes, embedder: Embedder,
           rss_mb: "Callable[[], float]" = lambda: 0.0) -> "tuple[int, dict]":
    """One request -> ``(status, JSON body)``. No sockets: the unit tests call this directly."""
    path = path.split("?", 1)[0].rstrip("/") or "/"
    try:
        if method == "GET" and path in ("/health", "/healthz"):
            return 200, {"status": "ok", "model": embedder.model_id, "dim": embedder.dim, "rss_mb": round(rss_mb(), 1),
                         "batch_cap": BATCH_CAP}
        if method == "GET" and path == "/v1/models":
            return 200, {"object": "list", "data": [{"id": embedder.model_id, "object": "model", "owned_by": "local"}]}
        if method == "POST" and path == "/v1/embeddings":
            texts, fmt, dims, _model = parse_request(raw)
            if dims is not None and dims != embedder.dim:
                raise ShimError(400, f"this model has {embedder.dim} dimensions and cannot return {dims}")
            vecs, tokens = embed_all(embedder, texts)
            return 200, {"object": "list", "model": embedder.model_id,
                         "data": [{"object": "embedding", "index": i, "embedding": encode_vector(v, fmt)}
                                  for i, v in enumerate(vecs)],
                         "usage": {"prompt_tokens": tokens, "total_tokens": tokens}}
        if path in ("/v1/embeddings", "/health", "/healthz", "/v1/models"):
            raise ShimError(405, f"{method} not allowed on {path}")
        raise ShimError(404, f"no route {method} {path}")
    except ShimError as exc:
        return exc.status, {"error": {"message": exc.message, "type": exc.kind, "code": exc.status}}
    except Exception as exc:  # noqa: BLE001 - a model failure is a 500 with a label, never a crash of the server
        return 500, {"error": {"message": f"embedding failed: {type(exc).__name__}", "type": "server_error", "code": 500}}


# ── process memory ───────────────────────────────────────────────────────────

def process_rss_mb(pid: "Optional[int]" = None, key: str = "VmRSS") -> float:
    try:
        with open(f"/proc/{pid or 'self'}/status", encoding="ascii", errors="ignore") as fh:
            for line in fh:
                if line.startswith(key + ":"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


# ── the server ───────────────────────────────────────────────────────────────

def make_server(embedder: Embedder, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    if not _is_loopback(host):
        raise ValueError(f"refusing to bind {host!r}: the embeddings shim is loopback-only")
    lock = threading.Lock()                 # one onnx run at a time: bounded RSS, no thread pile-up

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "zmb-embed-shim"

        def log_message(self, *_a: Any) -> None:   # quiet: no request text in any log
            return

        def _send(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _serve(self, method: str) -> None:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY_BYTES:
                self._send(413, {"error": {"message": "request body too large", "type": "invalid_request_error", "code": 413}})
                self.close_connection = True
                return
            raw = self.rfile.read(n) if n else b""
            with lock:
                status, body = handle(method, self.path, raw, embedder, process_rss_mb)
            self._send(status, body)

        def do_GET(self) -> None:  # noqa: N802
            self._serve("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._serve("POST")

    return ThreadingHTTPServer((host, port), Handler)


# ── selftest ─────────────────────────────────────────────────────────────────

SELFTEST_TEXTS = ("The dog is named Biscuit.", "My dog's name is Biscuit.", "Quarterly tax returns are due in October.")


def _cos(a: "Sequence[float]", b: "Sequence[float]") -> float:
    return sum(x * y for x, y in zip(a, b))


def selftest(embedder: Embedder, rss_limit_mb: float = RSS_LIMIT_MB,
             rss_mb: "Callable[[], float]" = process_rss_mb) -> "tuple[bool, dict]":
    """Embed three sentences through the real request path and check what the bake-off needs: right shape, unit norm,
    the paraphrase pair closer than the unrelated sentence, base64 and float agree, RSS under the limit."""
    t0 = time.monotonic()
    raw = json.dumps({"model": embedder.model_id, "input": list(SELFTEST_TEXTS)}).encode()
    status, body = handle("POST", "/v1/embeddings", raw, embedder)
    ms = (time.monotonic() - t0) * 1000.0
    checks: "dict[str, bool]" = {"status_200": status == 200}
    detail: "dict[str, Any]" = {"model": embedder.model_id, "dim": embedder.dim, "ms": round(ms, 1)}
    if status == 200:
        vecs = [d["embedding"] for d in body["data"]]
        checks["three_vectors"] = len(vecs) == 3 and all(len(v) == embedder.dim for v in vecs)
        checks["unit_norm"] = all(abs(_cos(v, v) - 1.0) < 1e-3 for v in vecs)
        near, far = _cos(vecs[0], vecs[1]), _cos(vecs[0], vecs[2])
        checks["paraphrase_closer"] = near > far + 0.05
        detail.update(cos_paraphrase=round(near, 3), cos_unrelated=round(far, 3))
        s2, b2 = handle("POST", "/v1/embeddings", json.dumps({"input": SELFTEST_TEXTS[0], "encoding_format": "base64"}).encode(), embedder)
        if s2 == 200:
            back = struct.unpack(f"<{embedder.dim}f", base64.b64decode(b2["data"][0]["embedding"]))
            checks["base64_matches_float"] = max(abs(a - b) for a, b in zip(back, vecs[0])) < 1e-5
        else:
            checks["base64_matches_float"] = False
    rss = rss_mb()
    detail["rss_mb"] = round(rss, 1)
    detail["rss_hwm_mb"] = round(process_rss_mb(key="VmHWM"), 1)
    checks["rss_under_limit"] = 0.0 < rss <= rss_limit_mb
    detail["rss_limit_mb"] = rss_limit_mb
    detail["checks"] = checks
    return all(checks.values()), detail


def main(argv: "Optional[list[str]]" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--selftest", action="store_true", help="embed three sentences, print the numbers, exit 0/1 (opens no port)")
    mode.add_argument("--serve", action="store_true", help=f"serve on {DEFAULT_HOST}:{DEFAULT_PORT} until stopped")
    mode.add_argument("--find", action="store_true", help="print the model that would be used and exit")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--model-dir", default=None, help="a directory holding the .onnx file and tokenizer.json (default: search the disk)")
    ap.add_argument("--rss-limit-mb", type=float, default=RSS_LIMIT_MB)
    args = ap.parse_args(argv)
    try:
        onnx_path, tok_path, pooling, model_id = find_model(args.model_dir)
    except ModelNotFound as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    if args.find:
        print(json.dumps({"onnx": str(onnx_path), "tokenizer": str(tok_path), "pooling": pooling, "model": model_id}))
        return 0
    if not _is_loopback(args.host):
        print(f"REFUSED: {args.host!r} is not a loopback address", file=sys.stderr)
        return 2
    embedder = OnnxEmbedder(onnx_path, tok_path, pooling, model_id)
    if args.selftest:
        ok, detail = selftest(embedder, args.rss_limit_mb)
        detail["onnx"] = str(onnx_path)
        print(json.dumps(detail, indent=2, sort_keys=True))
        print("SELFTEST " + ("PASS" if ok else "FAIL"))
        return 0 if ok else 1
    server = make_server(embedder, args.host, args.port)
    print(f"embed shim: {model_id} dim={embedder.dim} on http://{args.host}:{args.port}/v1/embeddings (rss {process_rss_mb():.0f} MB)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
