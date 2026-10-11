#!/usr/bin/env python3
"""Build a reduced-vocabulary Gemma 4 MTP draft GGUF (row-sliced head + `d2t` map).

The Gemma 4 assistant ("MTP") GGUF ties its LM head to ``token_embd.weight``
(``[n_embd, 262144]``).  The head is read in full on every draft step but only a
few thousand distinct token ids are ever emitted.  This tool keeps K rows (an
exact byte-level row gather - no dequantize, no requantize) and adds an int64
``d2t`` tensor (draft row -> target token id), the same convention llama.cpp
already uses for EAGLE3 / DFlash.  Needs a llama.cpp with the gemma4-assistant
``d2t`` patch (docs/knowledge/mtp-draft-vocab-trim-2026-10-10.md); a stock build
rejects the sliced file at load (tensor shape mismatch).  The original GGUF is
never modified.

  select    count token ids from text files via a running llama-server
            /tokenize and write an id list (one id per line): pinned specials,
            then the domain text, then the base text, up to K.
  build     gather the rows of an id list out of a draft GGUF -> new GGUF.
  coverage  report what fraction of a text's tokens an id list covers.

Needs the `gguf` and `numpy` packages.  Pass paths as arguments.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np

try:
    import gguf
except ImportError:  # pragma: no cover
    sys.exit("pip install gguf (or put llama.cpp/gguf-py on PYTHONPATH)")

CHUNK_CHARS = 16000
TOKEN_TYPE_CONTROL = 3
TOKEN_TYPE_BYTE = 6


def _tokenize(url: str, text: str) -> list[int]:
    req = urllib.request.Request(
        url.rstrip("/") + "/tokenize",
        data=json.dumps({"content": text, "add_special": False, "parse_special": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["tokens"]


def count_tokens(url: str, files: list[Path]) -> collections.Counter:
    c: collections.Counter = collections.Counter()
    for f in files:
        text = f.read_text(errors="replace")
        for i in range(0, len(text), CHUNK_CHARS):
            c.update(_tokenize(url, text[i : i + CHUNK_CHARS]))
    return c


def pinned_specials(target_gguf: Path) -> list[int]:
    """Control + byte-fallback tokens (EOS / end-of-turn markers must stay draftable)."""
    r = gguf.GGUFReader(str(target_gguf), "r")
    types = r.fields["tokenizer.ggml.token_type"].contents()
    return [i for i, t in enumerate(types) if t in (TOKEN_TYPE_CONTROL, TOKEN_TYPE_BYTE)]


def cmd_select(a: argparse.Namespace) -> None:
    domain = count_tokens(a.server, [Path(p) for p in a.domain_text])
    base = count_tokens(a.server, [Path(p) for p in a.base_text])
    chosen = list(dict.fromkeys(pinned_specials(Path(a.target_gguf))))
    seen = set(chosen)
    for src in (domain, base):  # domain first, then the generic base
        for tid, _ in src.most_common():
            if len(chosen) >= a.k:
                break
            if tid not in seen:
                chosen.append(tid)
                seen.add(tid)
    i = 0  # deterministic filler if the corpora have fewer than K distinct ids
    while len(chosen) < a.k:
        if i not in seen:
            chosen.append(i)
            seen.add(i)
        i += 1
    chosen = sorted(chosen)
    Path(a.out).write_text("\n".join(map(str, chosen)) + "\n")
    print(f"wrote {len(chosen)} ids -> {a.out} (domain distinct={len(domain)}, base distinct={len(base)})")


def cmd_coverage(a: argparse.Namespace) -> None:
    ids = {int(x) for x in Path(a.ids).read_text().split()}
    c = count_tokens(a.server, [Path(p) for p in a.text])
    tot = sum(c.values())
    hit = sum(n for t, n in c.items() if t in ids)
    print(f"K={len(ids)} coverage={hit / tot:.4%} of {tot} tokens ({len(c)} distinct, {sum(1 for t in c if t in ids)} in set)")


def cmd_build(a: argparse.Namespace) -> None:
    ids = np.array(sorted({int(x) for x in Path(a.ids).read_text().split()}), dtype=np.int64)
    r = gguf.GGUFReader(a.draft_gguf, "r")
    arch = r.fields["general.architecture"].contents()
    if arch != "gemma4-assistant":
        sys.exit(f"expected gemma4-assistant, got {arch}")
    if any(t.name == "d2t" for t in r.tensors):
        sys.exit("input already has a d2t tensor")
    emb = next(t for t in r.tensors if t.name == "token_embd.weight")
    n_vocab = emb.data.shape[0]
    if ids.min() < 0 or ids.max() >= n_vocab:
        sys.exit("id out of range")
    w = gguf.GGUFWriter(a.out, arch)
    for f in r.fields.values():
        if f.name == "general.architecture" or f.name.startswith("GGUF."):
            continue
        vt = f.types[0]
        st = f.types[-1] if vt == gguf.GGUFValueType.ARRAY else None
        w.add_key_value(f.name, f.contents(), vt, sub_type=st)
    out_data = {}
    for t in r.tensors:
        d = t.data
        if t.name == "token_embd.weight":
            d = np.ascontiguousarray(d[ids])  # exact row gather, quantized bytes untouched
        out_data[t.name] = d
        w.add_tensor_info(t.name, d.shape, d.dtype, d.nbytes, t.tensor_type)
    w.add_tensor_info("d2t", ids.shape, ids.dtype, ids.nbytes, gguf.GGMLQuantizationType.I64)
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_ti_data_to_file()
    for t in r.tensors:
        w.write_tensor_data(out_data[t.name])
    w.write_tensor_data(ids)
    w.close()
    print(f"wrote {a.out}: head {n_vocab} -> {len(ids)} rows")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("select")
    s.add_argument("--server", required=True, help="llama-server base URL serving the TARGET model")
    s.add_argument("--target-gguf", required=True, help="target GGUF (tokenizer specials)")
    s.add_argument("--domain-text", nargs="+", required=True)
    s.add_argument("--base-text", nargs="+", default=[])
    s.add_argument("-k", type=int, default=16384)
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_select)
    c = sp.add_parser("coverage")
    c.add_argument("--server", required=True)
    c.add_argument("--ids", required=True)
    c.add_argument("--text", nargs="+", required=True)
    c.set_defaults(fn=cmd_coverage)
    b = sp.add_parser("build")
    b.add_argument("--draft-gguf", required=True)
    b.add_argument("--ids", required=True)
    b.add_argument("--out", required=True)
    b.set_defaults(fn=cmd_build)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
