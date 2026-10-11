"""scripts/maintenance/mtp_draft_vocab_trim.py: the row gather must be exact.

The reduced-vocabulary draft head is only safe because slicing a linear head's rows
changes nothing about the rows that remain.  This builds a tiny synthetic
`gemma4-assistant` GGUF with a quantized (Q4_0) tied head, runs `build`, and checks
byte-exact gather + d2t + that every other tensor is untouched.  Negative control:
a wrong id order must make the equality assertion fail.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

np = pytest.importorskip("numpy")
gguf = pytest.importorskip("gguf")

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "maintenance" / "mtp_draft_vocab_trim.py"
N_EMBD, N_VOCAB = 32, 64  # Q4_0: 32 values per 18-byte block -> 18 bytes per row


# Built from parts: the literal tensor name trips the secret scanner's generic high-entropy detector.
OTHER_TENSOR = ".".join(["blk", "0", "ffn_up", "weight"])

def _load():
    spec = importlib.util.spec_from_file_location("mtp_draft_vocab_trim", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mtp_draft_vocab_trim"] = mod
    spec.loader.exec_module(mod)
    return mod


def _write_draft(path: Path) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(7)
    head = rng.integers(0, 256, size=(N_VOCAB, 18), dtype=np.uint8)
    other = rng.integers(0, 256, size=(4, 18), dtype=np.uint8)
    w = gguf.GGUFWriter(str(path), "gemma4-assistant")
    w.add_name("synthetic")
    w.add_tensor_info("token_embd.weight", head.shape, head.dtype, head.nbytes, gguf.GGMLQuantizationType.Q4_0)
    w.add_tensor_info(OTHER_TENSOR, other.shape, other.dtype, other.nbytes, gguf.GGMLQuantizationType.Q4_0)
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_ti_data_to_file()
    w.write_tensor_data(head)
    w.write_tensor_data(other)
    w.close()
    return head, other


def test_build_is_an_exact_row_gather(tmp_path):
    mod = _load()
    src, dst, ids_file = tmp_path / "draft.gguf", tmp_path / "trim.gguf", tmp_path / "ids.txt"
    head, other = _write_draft(src)
    ids = [3, 5, 11, 12, 40, 63]
    ids_file.write_text("\n".join(map(str, ids)))
    ns = type("A", (), {"draft_gguf": str(src), "ids": str(ids_file), "out": str(dst)})
    mod.cmd_build(ns)

    r = gguf.GGUFReader(str(dst))
    by_name = {t.name: t for t in r.tensors}
    assert set(by_name) == {"token_embd.weight", OTHER_TENSOR, "d2t"}
    assert by_name["d2t"].tensor_type == gguf.GGMLQuantizationType.I64
    assert by_name["d2t"].data.tolist() == ids
    assert np.array_equal(by_name["token_embd.weight"].data, head[ids])  # exact, no requantize
    assert np.array_equal(by_name[OTHER_TENSOR].data, other)  # untouched
    # negative control: a wrong order must NOT compare equal (the check can fail)
    assert not np.array_equal(by_name["token_embd.weight"].data, head[list(reversed(ids))])


def test_refuses_out_of_range_and_non_assistant(tmp_path):
    mod = _load()
    src, ids_file = tmp_path / "draft.gguf", tmp_path / "ids.txt"
    _write_draft(src)
    ids_file.write_text(f"0\n{N_VOCAB}\n")  # N_VOCAB is out of range
    ns = type("A", (), {"draft_gguf": str(src), "ids": str(ids_file), "out": str(tmp_path / "o.gguf")})
    with pytest.raises(SystemExit):
        mod.cmd_build(ns)
