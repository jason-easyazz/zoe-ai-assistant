"""The palace MiniLM pads each batch to its own longest text (palace_embed.DynamicPadMixin), not to a fixed 256 tokens.

Synthetic texts only. Needs the MiniLM model files on disk (no download in a test): skipped where they are absent. The
equivalence is asserted against chroma's OWN fixed-256 ``_forward`` run on the same session, so a pass means the dynamic path
really produced the same vectors, not that two copies of one function agree.
"""
from __future__ import annotations

import numpy as np
import pytest

import memory_service
import palace_embed

pytestmark = pytest.mark.ci_safe


@pytest.fixture(scope="module")
def ef():
    try:
        e = memory_service._drawers_embedding_function()
        e.model, e.tokenizer  # noqa: B018 - model files must already be on disk
        return e
    except Exception:  # noqa: BLE001
        pytest.skip("the palace MiniLM is not on this machine")


def _fixture_texts() -> list:
    base = [
        "", " ", "a", "yes", "Tomatoes", "Remind me to water the ferns on Sunday.",
        "Marisol's flight lands at 6:40pm; the pickup is at the airport.",
        "The spare key lives under the blue pot by the garage door.",
        "long " * 300,                                                   # > 256 tokens: truncation must match
        "word " * 256, "word " * 254, "word " * 255,                     # right at the truncation boundary
        "Café crème brûlée, naïve jalapeño — Zürich, São Paulo, Ångström",  # accents
        "東京の天気は今日晴れです。明日は雨が降るでしょう。", "Привет, как дела? Это тест.", "مرحبا بالعالم", "🙂🎉👍 emoji only 🚀",
        "tab\tand\nnewline\r\nmix   of   spaces", "UPPER lower MiXeD 12345 67.89 #tag @mention http://example.invalid/path?q=1",
        "x" * 2000,                                                      # one huge token run
    ]
    topics = ["ferns", "garage", "kayak", "oven", "recipe", "invoice", "bicycle", "telescope", "marathon", "piano"]
    many = [f"Synthetic memory {i}: the {topics[i % 10]} needs attention on day {i} " + "and more detail " * (i % 17)
            for i in range(60)]
    return base + many


def test_vectors_match_chroma_fixed_pad_on_60_plus_texts_in_mixed_batches(ef):
    pytest.importorskip("chromadb")   # slim CI shards install NumPy, not Chroma
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

    texts = _fixture_texts()
    assert len(texts) >= 60
    stock = np.asarray(ONNXMiniLM_L6_V2._forward(ef, texts), dtype=np.float32)       # chroma's fixed-256, same session
    dyn = np.asarray(ef(texts), dtype=np.float32)                                     # the public path chroma calls
    assert dyn.shape == stock.shape == (len(texts), 384)
    cos = np.sum(dyn * stock, axis=1)
    assert float(cos.min()) >= 0.9999, (int(cos.argmin()), float(cos.min()))
    # batch composition must not matter: singles and odd batch sizes give the same vectors
    for i in (0, 1, 8, 12, 20, 70):
        one = np.asarray(ef([texts[i]]), dtype=np.float32)
        assert float(np.sum(one[0] * stock[i])) >= 0.9999
    odd = np.asarray(ONNXMiniLM_L6_V2._forward(ef, texts[:5]), dtype=np.float32)
    assert float(np.sum(np.asarray(ef(texts[:5]), dtype=np.float32) * odd, axis=1).min()) >= 0.9999


def test_batches_pad_to_their_own_longest_item_and_truncate_at_256(ef):
    tok = palace_embed._tokenizer(ef)
    short = tok.encode_batch(["a b c", "hello"])
    assert {len(e.ids) for e in short} == {5}                    # [CLS] a b c [SEP]: the longest, not 256
    mixed = tok.encode_batch(["a b c", "word " * 400])
    assert {len(e.ids) for e in mixed} == {256}                  # truncated at exactly chroma's 256
    stock = ef.tokenizer.encode("word " * 400)
    assert mixed[1].ids == stock.ids                             # same tokens as chroma's own tokenizer
    assert len(ef.tokenizer.encode("a b c").ids) == 256          # chroma's tokenizer is untouched (still fixed-256)


def test_stock_tokenizer_untouched_and_output_is_unit_float32(ef):
    out = ef(["one", "two words"])
    assert all(np.asarray(v).dtype == np.float32 for v in out)
    assert all(abs(float(np.linalg.norm(v)) - 1.0) < 1e-5 for v in out)


def test_any_exception_falls_back_to_the_stock_forward_and_logs_once_without_text(ef, monkeypatch, caplog):
    pytest.importorskip("chromadb")   # slim CI shards install NumPy, not Chroma
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

    secret = "zz-private-memory-text-zz"

    def boom(*_a, **_k):
        raise RuntimeError(secret)

    monkeypatch.setattr(palace_embed, "embed_dynamic", boom)
    monkeypatch.setattr(palace_embed, "_FALLBACK_LOGGED", False)
    stock = np.asarray(ONNXMiniLM_L6_V2._forward(ef, [secret, "b"]), dtype=np.float32)
    with caplog.at_level("WARNING", logger="palace_embed"):
        a = np.asarray(ef([secret, "b"]), dtype=np.float32)
        b = np.asarray(ef([secret, "b"]), dtype=np.float32)
    assert float(np.sum(a * stock, axis=1).min()) > 0.9999 and float(np.sum(b * stock, axis=1).min()) > 0.9999
    recs = [r for r in caplog.records if r.name == "palace_embed"]
    assert len(recs) == 1                                         # logged once, not per call
    assert secret not in recs[0].getMessage() and "RuntimeError" in recs[0].getMessage() and "2 texts" in recs[0].getMessage()


def test_empty_input_is_chromas_own_behaviour(ef):
    pytest.importorskip("chromadb")   # slim CI shards install NumPy, not Chroma
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

    try:
        ONNXMiniLM_L6_V2._forward(ef, [])
        stock_raises = False
    except Exception:  # noqa: BLE001
        stock_raises = True
    try:
        ef([])
        ours_raises = False
    except Exception:  # noqa: BLE001
        ours_raises = True
    assert stock_raises == ours_raises


def test_no_embedding_function_conflict_either_way_and_stored_vectors_unchanged(ef, tmp_path):
    """Opening a collection with a different EF object must not raise chroma's conflict error, in either direction, and a row
    written under the stock EF is found by a query embedded by the dynamic EF (same vectors, so no re-index)."""
    pytest.importorskip("chromadb")   # slim CI shards install NumPy, not Chroma
    import chromadb
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

    client = chromadb.PersistentClient(path=str(tmp_path / "palace_a"))
    docs = ["the ferns need water on Sunday", "garage key is under the blue pot", "piano lesson moved to Friday"]
    ids = ["d1", "d2", "d3"]
    # (a) written with the STOCK fixed-pad vectors under the "default" identity, as every existing palace row was
    stock_vecs = [list(map(float, v)) for v in ONNXMiniLM_L6_V2._forward(ef, docs)]
    col = client.create_collection("drawers", metadata={"hnsw:space": "cosine"}, embedding_function=DefaultEmbeddingFunction())
    col.add(ids=ids, documents=docs, embeddings=stock_vecs)
    del col
    # reopened with the dynamic EF object: no conflict, and a dynamic-embedded query finds its stock-embedded row at distance ~0
    col2 = client.get_collection("drawers", embedding_function=ef)
    res = col2.query(query_texts=[docs[1]], n_results=1)
    assert res["ids"][0][0] == "d2" and res["distances"][0][0] < 1e-4
    # (b) created with the dynamic EF, reopened with no EF at all (what any other opener does) and with the stock default
    col3 = client.create_collection("drawers_b", metadata={"hnsw:space": "cosine"}, embedding_function=ef)
    col3.add(ids=ids, documents=docs)
    assert client.get_collection("drawers_b").count() == 3
    assert client.get_collection("drawers_b", embedding_function=DefaultEmbeddingFunction()).count() == 3
    assert ef.name() == "default" == DefaultEmbeddingFunction.name()
