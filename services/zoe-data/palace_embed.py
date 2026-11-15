"""Dynamic-padding forward for the palace's MiniLM embedding function.

Chroma's ``ONNXMiniLM_L6_V2`` pads every text to a fixed 256 tokens, so a 10-word memory runs ~25x the tokens it needs
(41-165 ms per embed on the Orin CPU against ~4 ms padded to the batch's longest text). Mean pooling is attention-masked, so the
pad tokens carry no weight: the vectors are the same (cosine 1.00000 against the fixed-pad vectors; tests/test_palace_embed.py).

Same ONNX session (``self.model``) and same ``tokenizer.json`` as chroma's own; only a SECOND tokenizer object with no fixed
length, so chroma's ``ef.tokenizer`` (and any stock call through it) is untouched. Truncation stays at 256, the same as chroma.
Stored vectors are unchanged, so no re-index is needed.

``DynamicPadMixin`` goes in FRONT of ``ONNXMiniLM_L6_V2``: its ``_forward`` is the one hook chroma's ``__call__`` uses. Any
exception falls back to chroma's own ``_forward`` and is logged once per process (an exception type and a text count: never text).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

#: sentence-transformers' max_seq_length for this MiniLM (chroma truncates at the same length)
MAX_TOKENS = 256
_TOK_LOCK = threading.Lock()
_FALLBACK_LOGGED = False


def _tokenizer(ef: Any) -> Any:
    tok = getattr(ef, "_zoe_dyn_tok", None)
    if tok is None:
        with _TOK_LOCK:
            tok = getattr(ef, "_zoe_dyn_tok", None)
            if tok is None:
                from tokenizers import Tokenizer

                tok = Tokenizer.from_file(os.path.join(ef.DOWNLOAD_PATH, ef.EXTRACTED_FOLDER_NAME, "tokenizer.json"))
                tok.enable_truncation(max_length=MAX_TOKENS)
                tok.enable_padding(pad_id=0, pad_token="[PAD]")        # no ``length``: pad to the batch's longest
                ef._zoe_dyn_tok = tok
    return tok


def embed_dynamic(ef: Any, documents: list, batch_size: int = 32) -> Any:
    """Unit float32 vectors (n x 384), each batch of ``batch_size`` padded to its own longest text. Raises on any surprise."""
    import numpy as np

    out = []
    tok = _tokenizer(ef)
    for i in range(0, len(documents), batch_size):
        enc = tok.encode_batch([str(d) for d in documents[i : i + batch_size]])
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        hidden = ef.model.run(None, {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)})[0]
        m = np.broadcast_to(np.expand_dims(mask, -1), hidden.shape)      # chroma's own pooling, step for step
        pooled = np.sum(hidden * m, 1) / np.clip(m.sum(1), a_min=1e-9, a_max=None)
        out.append(ef._normalize(pooled).astype(np.float32))
    return np.concatenate(out)


try:   # PALACE_EMBED_SUMMARY (gap audit 2026-11-14.6: #2460's live number); None = no instrument, the embed is unchanged
    import perf_summary as _perf_summary

    #: one embed call = one observation: its wall ms and its text count, bucketed; fallback = calls that took chroma's forward
    _EMBED_SUMMARY = _perf_summary.Summary("PALACE_EMBED", ("calls", "texts", "ms_sum", "fallback"),
                                           {"ms": _perf_summary.MS_BUCKETS, "len": _perf_summary.LEN_BUCKETS}, log=logger)
except Exception:  # noqa: BLE001
    _EMBED_SUMMARY = None


def _observe(t0: float, documents: Any, fell_back: bool) -> None:
    """One embed call into the summary (wall ms, text count). Never raises; a line at most every 10 min."""
    if _EMBED_SUMMARY is None:
        return
    try:
        ms, n = (time.monotonic() - t0) * 1000.0, len(documents) if documents is not None else 0
        _EMBED_SUMMARY.observe({"ms": ms, "len": n}, calls=1, texts=n, ms_sum=round(ms), fallback=fell_back)
    except Exception:  # noqa: BLE001
        pass


class DynamicPadMixin:
    def _forward(self, documents: list, batch_size: int = 32) -> Any:  # type: ignore[override]
        t0, fell_back = time.monotonic(), [False]
        try:
            return self._forward_inner(documents, batch_size, fell_back)
        finally:
            _observe(t0, documents, fell_back[0])

    def _forward_inner(self, documents: list, batch_size: int, fell_back: list) -> Any:
        global _FALLBACK_LOGGED
        if documents:
            try:
                return embed_dynamic(self, documents, batch_size)
            except Exception as exc:  # noqa: BLE001 - any surprise: chroma's own fixed-pad forward, slower but the same vectors
                if not _FALLBACK_LOGGED:
                    _FALLBACK_LOGGED = True
                    logger.warning("palace_embed: dynamic-pad embed unavailable (%s, %d texts); using chroma's fixed-pad forward",
                                   type(exc).__name__, len(documents))
                fell_back[0] = True
        return super()._forward(documents, batch_size)  # type: ignore[misc]
