"""ZMB bake-off: the loopback embeddings shim (``scripts/perf/zmb/embed_shim.py``).

Slim-lane safe: no onnxruntime, no model, no port, no network. The protocol is exercised through ``handle()`` with a deterministic
fake embedder; the real ONNX path is exercised only when the router's bge-small is on disk AND onnxruntime + tokenizers import
(``test_real_model_*``: skipped otherwise, never a pass).
"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "perf"))

from zmb import embed_shim as shim  # noqa: E402

DIM = 16


class FakeEmbedder:
    """Deterministic unit vectors; texts that share words are closer (so the selftest's paraphrase check is meaningful)."""
    model_id = "fake-embed"
    dim = DIM

    def __init__(self) -> None:
        self.batches: "list[int]" = []
        self.fail = False

    def embed(self, texts):
        self.batches.append(len(texts))
        if self.fail:
            raise RuntimeError("boom")
        out, counts = [], []
        for t in texts:
            v = [0.0] * DIM
            for w in t.lower().replace(".", "").replace("'", " ").split():
                v[int(hashlib.sha1(w.encode()).hexdigest(), 16) % DIM] += 1.0
            n = sum(x * x for x in v) ** 0.5 or 1.0
            out.append([x / n for x in v])
            counts.append(len(t.split()))
        return out, counts


def post(emb, body, path="/v1/embeddings"):
    return shim.handle("POST", path, json.dumps(body).encode(), emb)


# ── the protocol ─────────────────────────────────────────────────────────────

def test_embeddings_shape_float_and_base64_agree():
    emb = FakeEmbedder()
    st, body = post(emb, {"model": "x", "input": ["the dog is named Biscuit", "tax returns are due"]})
    assert st == 200 and body["object"] == "list" and body["model"] == "fake-embed"
    assert [d["index"] for d in body["data"]] == [0, 1] and all(d["object"] == "embedding" for d in body["data"])
    assert all(len(d["embedding"]) == DIM for d in body["data"])
    assert body["usage"]["total_tokens"] == body["usage"]["prompt_tokens"] > 0
    st2, b64 = post(emb, {"input": "the dog is named Biscuit", "encoding_format": "base64"})
    raw = struct.unpack(f"<{DIM}f", base64.b64decode(b64["data"][0]["embedding"]))   # what the openai SDK decodes
    assert max(abs(a - b) for a, b in zip(raw, body["data"][0]["embedding"])) < 1e-6


def test_a_single_string_input_is_accepted():
    st, body = post(FakeEmbedder(), {"input": "hello world"})
    assert st == 200 and len(body["data"]) == 1


def test_compute_batches_are_capped_at_eight():
    emb = FakeEmbedder()
    st, body = post(emb, {"input": [f"sentence number {i}" for i in range(20)]})
    assert st == 200 and len(body["data"]) == 20 and max(emb.batches) <= shim.BATCH_CAP == 8
    assert emb.batches == [8, 8, 4]


@pytest.mark.parametrize("body,status", [
    ({}, 400), ({"input": []}, 400), ({"input": [1, 2]}, 400), ({"input": ["ok", ""]}, 400), ({"input": "   "}, 400),
    ({"input": "x", "encoding_format": "int8"}, 400), ({"input": "x", "dimensions": 7}, 400),
    ({"input": "x", "dimensions": -1}, 400), ({"input": ["a"] * (shim.MAX_INPUTS + 1)}, 400),
    ({"input": "a" * (shim.MAX_TEXT_CHARS + 1)}, 400)])
def test_bad_requests_are_refused_with_an_openai_style_error(body, status):
    st, out = post(FakeEmbedder(), body)
    assert st == status and out["error"]["message"] and out["error"]["type"] == "invalid_request_error"


def test_oversize_body_and_bad_json_and_routes():
    emb = FakeEmbedder()
    assert shim.handle("POST", "/v1/embeddings", b"x" * (shim.MAX_BODY_BYTES + 1), emb)[0] == 413
    assert shim.handle("POST", "/v1/embeddings", b"{not json", emb)[0] == 400
    assert shim.handle("GET", "/v1/embeddings", b"", emb)[0] == 405
    assert shim.handle("GET", "/nope", b"", emb)[0] == 404
    st, health = shim.handle("GET", "/health", b"", emb, rss_mb=lambda: 123.4)
    assert st == 200 and health["dim"] == DIM and health["rss_mb"] == 123.4 and health["batch_cap"] == 8
    assert shim.handle("GET", "/v1/models", b"", emb)[1]["data"][0]["id"] == "fake-embed"


def test_dimensions_equal_to_the_model_is_accepted():
    assert post(FakeEmbedder(), {"input": "x", "dimensions": DIM})[0] == 200


def test_a_model_failure_is_a_500_not_a_crash():
    emb = FakeEmbedder()
    emb.fail = True
    st, out = post(emb, {"input": "x"})
    assert st == 500 and out["error"]["type"] == "server_error" and "boom" not in json.dumps(out)


# ── loopback only, no downloads ──────────────────────────────────────────────

def test_it_will_not_bind_a_non_loopback_address():
    for host in ("0.0.0.0", "192.168.1.5", "::"):
        with pytest.raises(ValueError, match="loopback"):
            shim.make_server(FakeEmbedder(), host, 0)


def test_main_refuses_a_non_loopback_host(monkeypatch, capsys):
    monkeypatch.setattr(shim, "find_model", lambda explicit=None: (Path("m.onnx"), Path("t.json"), "cls", "bge-small-en-v1.5"))
    assert shim.main(["--serve", "--host", "0.0.0.0"]) == 2
    assert "not a loopback address" in capsys.readouterr().err


def test_no_model_on_disk_refuses_and_never_touches_the_network(monkeypatch, tmp_path, capsys):
    def no_network(*_a, **_k):
        raise AssertionError("the shim touched the network")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(shim, "_candidate_dirs", lambda explicit: [(tmp_path / "empty", "cls", shim._BGE_FILES)])
    with pytest.raises(shim.ModelNotFound, match="never downloads"):
        shim.find_model()
    assert shim.main(["--selftest"]) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_the_module_imports_no_downloader():
    import ast
    tree = ast.parse((REPO / "scripts/perf/zmb/embed_shim.py").read_text())
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not imported & {"fastembed", "huggingface_hub", "chromadb", "requests", "httpx", "urllib", "openai", "transformers"}, imported


def test_find_model_prefers_an_explicit_dir_and_picks_the_pooling(tmp_path, monkeypatch):
    d = tmp_path / "m"
    d.mkdir()
    (d / "model_optimized.onnx").write_bytes(b"x")
    (d / "tokenizer.json").write_text("{}")
    onnx, tok, pooling, model_id = shim.find_model(str(d))
    assert onnx.name == "model_optimized.onnx" and pooling == "cls" and model_id == "bge-small-en-v1.5"
    mini = tmp_path / "mini"
    (mini / "onnx").mkdir(parents=True)
    (mini / "onnx" / "model.onnx").write_bytes(b"x")
    (mini / "tokenizer.json").write_text("{}")
    monkeypatch.setattr(shim, "_candidate_dirs", lambda explicit: [(mini, "mean", shim._MINILM_FILES)])
    assert shim.find_model()[2:] == ("mean", "all-MiniLM-L6-v2")


# ── the selftest (red-before-green) ──────────────────────────────────────────

def test_selftest_passes_on_a_sane_embedder():
    ok, detail = shim.selftest(FakeEmbedder(), rss_mb=lambda: 100.0)
    assert ok and detail["checks"]["paraphrase_closer"] and detail["checks"]["base64_matches_float"] and detail["dim"] == DIM


def test_selftest_goes_red_when_the_embedder_is_broken_or_too_big():
    class Constant(FakeEmbedder):
        def embed(self, texts):
            return [[1.0] + [0.0] * (DIM - 1)] * len(texts), [1] * len(texts)
    ok, detail = shim.selftest(Constant(), rss_mb=lambda: 100.0)
    assert not ok and not detail["checks"]["paraphrase_closer"]              # identical vectors cannot tell a paraphrase from noise
    ok, detail = shim.selftest(FakeEmbedder(), rss_mb=lambda: 151.0)
    assert not ok and not detail["checks"]["rss_under_limit"]                # the 150 MB budget is enforced
    broken = FakeEmbedder()
    broken.fail = True
    ok, detail = shim.selftest(broken, rss_mb=lambda: 100.0)
    assert not ok and not detail["checks"]["status_200"]


# ── the real model (skipped unless it is on disk and the libraries import) ───

def _real():
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
        return shim.find_model()
    except (ImportError, shim.ModelNotFound):
        return None


@pytest.mark.skipif(_real() is None, reason="needs the router's bge-small on disk plus onnxruntime/tokenizers (not in the slim lane)")
def test_real_model_selftest_in_a_fresh_process_stays_under_the_memory_budget():
    """A FRESH process: the budget is the shim's own RSS, not pytest's. Prints the numbers on failure."""
    import subprocess
    r = subprocess.run([sys.executable, str(REPO / "scripts/perf/zmb/embed_shim.py"), "--selftest"], capture_output=True, text=True, timeout=180)
    assert r.returncode == 0 and "SELFTEST PASS" in r.stdout, r.stdout[-1500:] + r.stderr[-500:]
    detail = json.loads(r.stdout[:r.stdout.index("SELFTEST")])
    assert detail["dim"] == 384 and 0 < detail["rss_mb"] <= shim.RSS_LIMIT_MB


@pytest.mark.skipif(_real() is None, reason="needs the router's bge-small on disk plus onnxruntime/tokenizers (not in the slim lane)")
def test_real_model_handles_eight_long_texts_in_token_budgeted_runs():
    onnx, tok, pooling, model_id = _real()
    emb = shim.OnnxEmbedder(onnx, tok, pooling, model_id)
    st, body = shim.handle("POST", "/v1/embeddings", json.dumps({"input": ["the harbour office opens early " * 120] * 8 + ["short"]}).encode(), emb)
    assert st == 200 and len(body["data"]) == 9
    norms = [sum(x * x for x in d["embedding"]) ** 0.5 for d in body["data"]]
    assert all(abs(n - 1.0) < 1e-3 for n in norms)
