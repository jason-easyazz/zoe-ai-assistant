"""The Kokoro sidecar's pinned style (zoe-data ZOE_KOKORO_STYLE_PIN sends ``style_len``).

KPipeline.infer picks the style row ``pack[len(phonemes) - 1]``, so each chunk of a reply gets a different row: a pitch
reset at every chunk. A request carrying ``style_len`` is synthesised with a pack whose rows are all row ``style_len - 1``.

Pinned here (no model load; torch-backed checks skip where torch is absent):
  * the pure row-pinning function, on a fake array, and the real pack as a CPU float32 tensor with identical rows, bounded cache;
  * a request WITHOUT ``style_len`` is unchanged: the same cache key, ``_run_synthesis`` called with the same arguments, the
    voice id (not a tensor) handed to the pipeline, and the warm-up + background precache path byte-identical;
  * a pinned call can neither read nor write an unpinned cache entry (the keys are disjoint by construction);
  * /health says the sidecar understands the pin.
"""
import asyncio
import importlib.util
import io
import pathlib
import sys
import types
import wave

import pytest

pytestmark = pytest.mark.ci_safe

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "setup" / "kokoro_sidecar.py"


@pytest.fixture(scope="module")
def kok():
    spec = importlib.util.spec_from_file_location("kokoro_sidecar_style_pin", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["kokoro_sidecar_style_pin"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _clean(kok):
    kok._phrase_cache.clear()
    kok._cache_meta.clear()
    kok._pinned_packs.clear()
    yield
    kok._phrase_cache.clear()
    kok._cache_meta.clear()
    kok._pinned_packs.clear()


def _wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(b"\x00\x00" * 8)
    return buf.getvalue()


class _Rows:
    """A fake pack: a list of rows with a shape and list indexing (what the pure function needs)."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.shape = (len(self.rows),)

    def __getitem__(self, idx):
        return _Rows(self.rows[i] for i in idx)


def test_pin_rows_is_pure_and_clamped(kok):
    base = _Rows(range(10))
    assert kok._pin_style_rows(base, 4).rows == [3] * 10
    assert kok._pin_style_rows(base, 0).rows == [0] * 10        # clamped to the first row
    assert kok._pin_style_rows(base, 999).rows == [9] * 10      # ... and to the last
    assert base.rows == list(range(10))                         # the base pack is never touched


def test_pinned_pack_is_a_cpu_float_tensor_with_identical_rows(kok, monkeypatch):
    torch = pytest.importorskip("torch")
    base = torch.randn(510, 1, 256, dtype=torch.float64)
    loads = []
    monkeypatch.setattr(kok, "_pipeline", types.SimpleNamespace(load_voice=lambda v: loads.append(v) or base))
    pack = kok._pinned_pack("af_sky", 120)
    assert isinstance(pack, torch.FloatTensor) and pack.shape == base.shape     # KPipeline.load_voice passes it through
    assert torch.equal(pack, base[119].to(torch.float32).expand_as(pack))
    assert kok._pinned_pack("af_sky", 120) is pack and loads == ["af_sky"]      # cached
    for n in range(1, 12):
        kok._pinned_pack("af_sky", n)
    assert len(kok._pinned_packs) == kok._PINNED_PACKS_MAX                      # bounded


def test_unpinned_key_is_unchanged_and_pinned_keys_are_disjoint(kok):
    assert kok._phrase_cache_key("af_sky", "  Hello There ") == "af_sky|hello there"
    pinned = kok._pinned_cache_key("af_sky", "Hello there", 120)
    assert pinned != kok._phrase_cache_key("af_sky", "Hello there")
    # whatever the text says, a pinned key's voice part is not a voice id, so it cannot equal ANY unpinned key
    for text in ("hello#s120", "x|y", "af_sky#s120|hello"):
        assert kok._pinned_cache_key("af_sky", text, 120).split("|", 1)[0] == "af_sky#s120"
        assert not kok._VOICE_NAME_RE.fullmatch(kok._pinned_cache_key("af_sky", text, 120).split("|", 1)[0])
    assert kok._pinned_cache_key("af_sky", "a", 9999) == kok._pinned_cache_key("af_sky", "a", 510)


def _recorder(kok, monkeypatch, wav=b"RIFF-new"):
    calls = []

    async def rec(*a, **k):
        calls.append((a, k))
        return wav

    monkeypatch.setattr(kok, "_pipeline", object())
    monkeypatch.setattr(kok, "_run_synthesis", rec)
    return calls


def test_a_pinned_call_neither_reads_nor_writes_an_unpinned_entry(kok, monkeypatch):
    unpinned = kok._phrase_cache_key("af_sky", "Hello there")
    kok._phrase_cache[unpinned] = b"UNPINNED"
    calls = _recorder(kok, monkeypatch, b"PINNED")

    r = asyncio.run(kok.synthesize(kok.SynthRequest(text="Hello there", style_len=120)))
    assert (r.body, r.headers["X-Cache"]) == (b"PINNED", "miss")                 # did not read the unpinned entry
    assert kok._phrase_cache[unpinned] == b"UNPINNED"                            # ... nor overwrite it
    assert calls == [(("Hello there", "af_sky", 1.0), {"style_len": 120})]
    again = asyncio.run(kok.synthesize(kok.SynthRequest(text="Hello there", style_len=120)))
    assert (again.body, again.headers["X-Cache"]) == (b"PINNED", "hit")           # pinned entries cache under their own key
    plain = asyncio.run(kok.synthesize(kok.SynthRequest(text="Hello there")))
    assert (plain.body, plain.headers["X-Cache"]) == (b"UNPINNED", "hit")
    assert len(calls) == 1


def test_an_unpinned_request_calls_synthesis_exactly_as_before(kok, monkeypatch):
    calls = _recorder(kok, monkeypatch, _wav())
    long_text = "word " * 60                                                       # over the cache length: always synthesised
    asyncio.run(kok.synthesize(kok.SynthRequest(text=long_text, speed=0.88)))

    async def drain(resp):
        return [c async for c in resp.body_iterator]

    asyncio.run(drain(asyncio.run(kok.synthesize_stream(kok.SynthRequest(text="Hi there")))))
    assert calls == [((long_text.strip(), "af_sky", 0.88), {}), (("Hi there", "af_sky", 1.0), {})]


def test_the_pipeline_gets_the_voice_id_unpinned_and_the_pack_pinned(kok, monkeypatch):
    torch = pytest.importorskip("torch")
    seen = []

    def pipeline(text, voice, speed):
        seen.append(voice)
        return [types.SimpleNamespace(audio=torch.zeros(48))]

    pipeline.load_voice = lambda v: torch.randn(510, 1, 256)
    monkeypatch.setattr(kok, "_pipeline", pipeline)
    kok._blocking_synthesize("Hi there", "af_sky", 1.0)
    kok._blocking_synthesize("Hi there", "af_sky", 1.0, 120)
    assert seen[0] == "af_sky"
    assert isinstance(seen[1], torch.FloatTensor) and torch.equal(seen[1][0], seen[1][509])


def test_warmup_and_precache_are_unchanged(kok, monkeypatch):
    calls = _recorder(kok, monkeypatch, b"RIFF-warm")

    async def none(*_a, **_k):
        return None

    monkeypatch.setattr(kok, "_load_pipeline", lambda: object())
    monkeypatch.setattr(kok, "_CACHE_PERSIST", False)
    monkeypatch.setattr(kok, "_flush_cache_async", none)
    monkeypatch.setattr(kok, "_WARM_PHRASES", ["Sure!", "Got it!"])

    async def go():
        async with kok.lifespan(kok.app):
            for _ in range(20):
                await asyncio.sleep(0)

    asyncio.run(go())
    assert calls == [(("Zoe is ready.", "af_sky"), {"speed": 1.0}), (("Sure!", "af_sky"), {"speed": 1.0}),
                     (("Got it!", "af_sky"), {"speed": 1.0})]
    assert sorted(kok._phrase_cache) == ["af_sky|got it!", "af_sky|sure!"]
    assert kok._pinned_packs == {}


def test_health_reports_style_pin(kok):
    assert asyncio.run(kok.health())["style_pin"] is True
