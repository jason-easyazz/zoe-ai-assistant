"""Fixture tests for the B9.1 Omi lab receiver — no hardware, no bleak.

Hand-run (labs are outside production CI by design, see labs/AGENTS.md):

    pytest labs/omi-receiver/tests -q -x -p no:cacheprovider

The ``ci_safe`` marker records that the file is slim-venv-green (stdlib + pytest
only; the real-decode test SKIPS with a stated reason when opuslib/libopus is
absent, it never fakes a pass).
"""
from __future__ import annotations

import asyncio
import math
import struct
import subprocess
import sys
import wave
from pathlib import Path

import pytest

LAB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_DIR))

import omi_bridge as ob  # noqa: E402
import split_on_silence as sos  # noqa: E402
import wer  # noqa: E402

pytestmark = pytest.mark.ci_safe

FRAME = b"\x78" * 80          # an 80-byte stand-in for a 20 ms Opus frame
SAMPLES = 320                 # codec 21 (Opus FS320): 20 ms @ 16 kHz


def packets(ids, payload=FRAME, fragment=0):
    return [ob.build_packet(i, fragment, payload) for i in ids]


def make_pipeline(tmp_path, *, detect_gaps=True, fill_gaps=True, roll_seconds=600.0):
    framer = ob.OmiFramer(detect_gaps=detect_gaps)
    sink = ob.WavSink(tmp_path, roll_seconds=roll_seconds)
    return ob.AudioPipeline(framer, ob.StubDecoder(SAMPLES), sink, fill_gaps=fill_gaps)


def run_stream(pipeline, stream):
    for pkt in stream:
        pipeline.on_packet(pkt)
    pipeline.flush()
    pipeline.sink.close()
    return pipeline.framer.stats


def wav_samples(path: Path) -> int:
    with wave.open(str(path), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        return w.getnframes()


# --- header ---------------------------------------------------------------------------
def test_header_layout_matches_firmware_push_to_gatt():
    # transport.c: data[0] = id & 0xFF; data[1] = (id >> 8) & 0xFF; data[2] = fragment index
    raw = ob.build_packet(0x1234, 2, b"ab")
    assert raw == bytes([0x34, 0x12, 0x02]) + b"ab"
    pkt = ob.parse_packet(bytearray(raw))
    assert (pkt.packet_id, pkt.fragment, pkt.payload) == (0x1234, 2, b"ab")


def test_module_imports_without_bleak_or_opuslib():
    code = "import sys, omi_bridge; assert 'bleak' not in sys.modules and 'opuslib' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], cwd=LAB_DIR, check=True)


# --- contiguous / gaps / wrap / truncation ----------------------------------------------
def test_contiguous_stream_reassembles_every_frame(tmp_path):
    p = make_pipeline(tmp_path)
    st = run_stream(p, packets(range(50)))
    assert st.packets_received == 50 and st.packets_lost == 0 and st.gap_events == 0
    assert st.frames_complete == 50 and st.frames_dropped == 0 and st.gap_pct == 0.0
    assert p.sink.total_samples == 50 * SAMPLES
    assert wav_samples(p.sink.files[0]) == 50 * SAMPLES
    assert all(f == FRAME for f in p.decoder.frames)


def _assert_gap_accounted(st: ob.FramerStats):
    assert st.packets_lost == 5
    assert st.gap_events == 1 and st.largest_gap == 5
    assert st.packets_expected == 30
    assert st.gap_pct == pytest.approx(100 * 5 / 30)


def test_gap_is_counted_and_filled_with_silence(tmp_path):
    p = make_pipeline(tmp_path)
    st = run_stream(p, packets(list(range(10)) + list(range(15, 30))))
    _assert_gap_accounted(st)
    assert st.frames_complete == 25
    assert p.silence_samples == 5 * SAMPLES
    assert wav_samples(p.sink.files[0]) == 30 * SAMPLES  # wall-clock length preserved


def test_gap_fill_can_be_disabled(tmp_path):
    p = make_pipeline(tmp_path, fill_gaps=False)
    st = run_stream(p, packets(list(range(10)) + list(range(15, 30))))
    _assert_gap_accounted(st)
    assert wav_samples(p.sink.files[0]) == 25 * SAMPLES


def test_negative_control_bypassed_gap_detector_fails_the_gap_test(tmp_path):
    """With detection bypassed the same lossy stream reports zero loss — the
    assertions above MUST go red, proving they measure the detector."""
    p = make_pipeline(tmp_path, detect_gaps=False)
    st = run_stream(p, packets(list(range(10)) + list(range(15, 30))))
    assert st.packets_lost == 0 and st.gap_pct == 0.0
    with pytest.raises(AssertionError):
        _assert_gap_accounted(st)


def test_counter_wrap_is_not_a_gap(tmp_path):
    ids = list(range(65530, 65536)) + list(range(0, 6))
    st = run_stream(make_pipeline(tmp_path), packets(ids))
    assert st.packets_lost == 0 and st.gap_events == 0
    assert st.wraps == 1 and st.frames_complete == 12


def test_gap_straddling_the_wrap_is_measured(tmp_path):
    st = run_stream(make_pipeline(tmp_path), packets([65533, 65534, 2, 3]))  # lost 65535, 0, 1
    assert st.packets_lost == 3 and st.gap_events == 1 and st.wraps == 1
    assert st.frames_complete == 4


def test_truncated_and_header_only_packets(tmp_path):
    p = make_pipeline(tmp_path)
    stream = [b"", b"\x01", b"\x01\x00"] + packets([0]) + [ob.build_packet(1, 0, b"")] + packets([2])
    st = run_stream(p, stream)
    assert st.truncated == 3
    assert st.packets_received == 3 and st.empty == 1
    assert st.packets_lost == 0
    assert st.frames_complete == 2 and st.frames_dropped == 1  # the empty frame is dropped, not decoded


def test_reordered_duplicate_is_ignored(tmp_path):
    st = run_stream(make_pipeline(tmp_path), packets([0, 1, 2, 1, 3]))
    assert st.reordered == 1 and st.packets_lost == 0 and st.frames_complete == 4


# --- fragmentation (ids advance per notification, fragment index per frame) --------------
def test_fragmented_frame_is_reassembled_in_order(tmp_path):
    big = bytes(range(200))
    stream = [ob.build_packet(7, 0, big[:80]), ob.build_packet(8, 1, big[80:160]),
              ob.build_packet(9, 2, big[160:]), ob.build_packet(10, 0, FRAME)]
    p = make_pipeline(tmp_path)
    st = run_stream(p, stream)
    assert p.decoder.frames == [big, FRAME]
    assert st.frames_complete == 2 and st.packets_lost == 0


def test_missing_fragment_drops_only_that_frame(tmp_path):
    big = bytes(range(200))
    stream = [ob.build_packet(7, 0, big[:80]), ob.build_packet(9, 2, big[160:]),  # id 8 (frag 1) lost
              ob.build_packet(10, 0, FRAME)]
    p = make_pipeline(tmp_path)
    st = run_stream(p, stream)
    assert st.packets_lost == 1 and st.frames_dropped == 1 and st.frames_complete == 1
    assert p.decoder.frames == [FRAME]


def test_gap_after_a_fragmented_frame_drops_it(tmp_path):
    """Once the stream has fragmented, a gap right after fragment 0 is a possibly
    incomplete frame and must be dropped (the unfragmented case keeps it — see
    test_gap_is_counted_and_filled_with_silence, frames_complete == 25)."""
    big = bytes(range(200))
    stream = [ob.build_packet(1, 0, big[:100]), ob.build_packet(2, 1, big[100:]),   # a fragmented frame
              ob.build_packet(3, 0, big[:100]),                                     # frag 0 ... then id 4 lost
              ob.build_packet(5, 0, FRAME)]
    p = make_pipeline(tmp_path)
    st = run_stream(p, stream)
    assert st.packets_lost == 1 and st.frames_dropped == 1 and st.frames_complete == 2
    assert p.decoder.frames == [big, FRAME]


def test_counter_restart_mid_session_resyncs_instead_of_dropping_forever(tmp_path):
    ids = list(range(20000, 20010)) + list(range(0, 20))  # counter restarted at 0 (looks "behind" from 20010)
    st = run_stream(make_pipeline(tmp_path), packets(ids))
    assert st.resyncs == 1
    assert st.reordered == ob.OmiFramer.BEHIND_STREAK_RESYNC - 1  # the streak that triggered it
    assert st.frames_complete == 30 - st.reordered
    assert st.packets_lost == 0


def test_orphan_fragment_without_frame_start_is_counted(tmp_path):
    st = run_stream(make_pipeline(tmp_path), [ob.build_packet(3, 1, FRAME), ob.build_packet(4, 0, FRAME)])
    assert st.orphan_fragments == 1 and st.frames_complete == 1


# --- WAV sink -----------------------------------------------------------------------------
def test_wav_rolls_into_multiple_files(tmp_path):
    p = make_pipeline(tmp_path, roll_seconds=2 * SAMPLES / 16000)  # two frames per file
    run_stream(p, packets(range(5)))
    assert len(p.sink.files) == 3
    assert [wav_samples(f) for f in p.sink.files] == [2 * SAMPLES, 2 * SAMPLES, SAMPLES]
    assert p.sink.seconds == pytest.approx(5 * 0.02)


# --- the bridge over a fake transport -----------------------------------------------------
class FakeTransport:
    """Scripted pendant: each session feeds its packets then drops (except the last)."""

    def __init__(self, sessions, *, codec=ob.CODEC_OPUS_FS320, battery=(90, 80), fail_first_connect=False,
                 drop_during_setup=False):
        self.sessions = list(sessions)
        self.codec = codec
        self.battery = list(battery)
        self.fail_first_connect = fail_first_connect
        self.drop_during_setup = drop_during_setup   # first connect: the link dies inside start_notify
        self.connects = 0
        self.connected = False
        self.notify = {}
        self.reads = []
        self.stopped = []

    identity = "Omi [AA:BB:CC:DD:EE:FF]"

    @property
    def is_connected(self):
        return self.connected

    def has_service(self, uuid):
        return uuid == ob.STORAGE_SERVICE_UUID

    async def connect(self, on_disconnect):
        self.connects += 1
        if self.fail_first_connect and self.connects == 1:
            raise ConnectionError("pendant not found")
        self.connected = True
        self._on_disconnect = on_disconnect

    async def disconnect(self):
        self.connected = False

    async def read(self, uuid):
        self.reads.append(uuid)
        if uuid == ob.BATTERY_LEVEL_UUID:
            return bytes([self.battery.pop(0)]) if self.battery else None
        return {ob.DIS_MODEL_UUID: b"Omi CV 1\x00", ob.DIS_FIRMWARE_UUID: b"3.0.21", ob.DIS_HARDWARE_UUID: b"5.0",
                ob.AUDIO_CODEC_UUID: bytes([self.codec])}.get(uuid)

    async def start_notify(self, uuid, callback):
        if uuid == ob.BUTTON_TRIGGER_UUID:
            raise RuntimeError("no button service on this fixture")
        if uuid == ob.AUDIO_DATA_UUID and self.drop_during_setup and self.connects == 1:
            self.connected = False
            self._on_disconnect()
            raise ConnectionError("Not connected")      # what bleak raises once the link is gone
        self.notify[uuid] = callback
        if uuid == ob.AUDIO_DATA_UUID and self.sessions:
            for pkt in self.sessions.pop(0):
                callback(pkt)
            if self.sessions:
                self._on_disconnect()

    async def stop_notify(self, uuid):
        self.stopped.append(uuid)


def run_bridge(transport, tmp_path, **kw):
    pipeline = ob.AudioPipeline(ob.OmiFramer(), ob.StubDecoder(SAMPLES), ob.WavSink(tmp_path))
    bridge = ob.Bridge(transport, pipeline, backoff=(0.01,), **kw)
    return asyncio.run(bridge.run())


def test_bridge_reconnects_after_its_own_disconnect_callback(tmp_path):
    t = FakeTransport([packets(range(10)), packets(range(10, 30))])
    s = run_bridge(t, tmp_path, reconnect=True, max_seconds=0.3)
    assert t.connects == 2 and s.reconnects == 1 and s.connect_failures == 0
    assert s.packets_received == 30 and s.packets_lost == 0 and s.gap_gate_pass is True
    assert s.frames_complete == 30 and s.wav_seconds == pytest.approx(30 * 0.02)
    assert (s.model, s.firmware, s.hardware, s.codec_id, s.codec) == ("Omi CV 1", "3.0.21", "5.0", 21, "opus-fs320-20ms")
    assert s.storage_service_seen is True
    assert (s.battery_start, s.battery_end) == (90, 80) and s.battery_drop_per_hour > 0
    assert ob.AUDIO_DATA_UUID in t.stopped and not t.connected
    assert "Omi CV 1" in ob.format_summary(s) and "PASS" in ob.format_summary(s)
    assert Path(s.wav_files[0]).exists()


def test_bridge_resyncs_the_packet_counter_on_reconnect(tmp_path):
    t = FakeTransport([packets(range(500, 510)), packets(range(0, 10))])  # counter restarted at 0
    s = run_bridge(t, tmp_path, reconnect=True, max_seconds=0.3)
    assert s.reconnects == 1 and s.resyncs == 1
    assert s.packets_received == 20 and s.reordered == 0 and s.packets_lost == 0 and s.frames_complete == 20


def test_bridge_without_reconnect_stops_at_first_drop(tmp_path):
    t = FakeTransport([packets(range(10)), packets(range(10, 30))])
    s = run_bridge(t, tmp_path, reconnect=False, max_seconds=0.3)
    assert t.connects == 1 and s.reconnects == 0 and s.packets_received == 10


def test_bridge_retries_a_failed_connect_with_backoff(tmp_path):
    t = FakeTransport([packets(range(5))], fail_first_connect=True)
    s = run_bridge(t, tmp_path, reconnect=True, max_seconds=0.3)
    assert t.connects == 2 and s.connect_failures == 1 and s.packets_received == 5


def test_bridge_reports_gap_gate_fail_over_one_percent(tmp_path):
    t = FakeTransport([packets(list(range(50)) + list(range(51, 100)))])  # 1 lost of 100 = 1.0 %
    s = run_bridge(t, tmp_path, reconnect=False, max_seconds=0.3)
    assert s.packets_lost == 1 and s.gap_pct == pytest.approx(1.0) and s.gap_gate_pass is False


def test_bridge_refuses_unsupported_codec_and_still_summarises(tmp_path):
    t = FakeTransport([packets(range(5))], codec=ob.CODEC_PCM16_8K)
    s = run_bridge(t, tmp_path, reconnect=True, max_seconds=0.3)
    assert s.packets_received == 0 and t.connects == 1 and not t.connected
    assert s.gap_gate_pass is False


# --- real Opus decode (skips honestly when the codec binding is absent) ------------------
def _opuslib_or_reason():
    try:
        import opuslib  # noqa: F401
    except Exception as exc:  # ImportError, or opuslib's own error when libopus is missing
        return None, f"opuslib/libopus unavailable in this venv: {exc!r}"
    return opuslib, ""


_OPUSLIB, _SKIP_REASON = _opuslib_or_reason()


@pytest.mark.skipif(_OPUSLIB is None, reason=_SKIP_REASON)
def test_real_opus_frames_decode_to_16k_pcm(tmp_path):
    enc = _OPUSLIB.Encoder(16000, 1, "restricted_lowdelay")  # config.h: RESTRICTED_LOWDELAY, 32 kbps, complexity 3
    enc.bitrate = 32000
    enc.complexity = 3
    amp, hz, n_frames = 8000, 440, 25
    pcm_frames = []
    for f in range(n_frames):
        samples = [int(amp * math.sin(2 * math.pi * hz * (f * SAMPLES + i) / 16000)) for i in range(SAMPLES)]
        pcm_frames.append(struct.pack(f"<{SAMPLES}h", *samples))
    stream = [ob.build_packet(i, 0, enc.encode(pcm, SAMPLES)) for i, pcm in enumerate(pcm_frames)]
    assert all(len(p) - ob.HEADER_BYTES <= 160 for p in stream)  # CODEC_OUTPUT_MAX_BYTES

    dec = ob.make_decoder(ob.CODEC_OPUS_FS320)
    p = ob.AudioPipeline(ob.OmiFramer(), dec, ob.WavSink(tmp_path))
    st = run_stream(p, stream)
    assert st.frames_complete == n_frames and dec.errors == 0
    assert wav_samples(p.sink.files[0]) == n_frames * SAMPLES

    with wave.open(str(p.sink.files[0]), "rb") as w:
        out = struct.unpack(f"<{w.getnframes()}h", w.readframes(w.getnframes()))
    tail = out[len(out) // 2:]  # skip the codec's warm-up
    rms = math.sqrt(sum(v * v for v in tail) / len(tail))
    assert rms == pytest.approx(amp / math.sqrt(2), rel=0.25)
    crossings = sum(1 for a, b in zip(tail, tail[1:]) if (a < 0) != (b < 0))
    assert crossings / (len(tail) / 16000) == pytest.approx(2 * hz, rel=0.1)


# --- helpers used by the manual protocol --------------------------------------------------
def test_split_on_silence_finds_each_utterance(tmp_path):
    rate = 16000
    tone = [int(6000 * math.sin(2 * math.pi * 300 * i / rate)) for i in range(rate)]  # 1 s
    quiet = [0] * int(1.2 * rate)
    import array
    pcm = array.array("h", quiet + tone + quiet + tone + quiet + tone[: rate // 2])
    segs = sos.find_segments(pcm, rate, threshold=300, min_silence_s=0.8, min_speech_s=0.4, pad_s=0.0)
    assert len(segs) == 3
    assert [round((b - a) / rate, 1) for a, b in segs] == [1.0, 1.0, 0.5]
    paths = sos.write_segments(pcm, rate, segs, tmp_path, "cap")
    assert [wav_samples(p) for p in paths] == [rate, rate, rate // 2]


def test_wer():
    assert wer.wer("The cat sat.", "the cat sat") == 0.0
    assert wer.wer("the cat sat on the mat", "the cat on the hat") == pytest.approx(2 / 6)
    assert wer.wer("", "") == 0.0 and wer.wer("", "noise") == 1.0
    assert wer.corpus_wer(["a b c d", "e f"], ["a b c d", "x y"]) == pytest.approx(2 / 6)
    with pytest.raises(ValueError):
        wer.corpus_wer(["a"], [])


# --- review round 1 (PR #1693): each test was red before its fix ---------------------------
def wav_pcm(path: Path) -> list[int]:
    with wave.open(str(path), "rb") as w:
        return list(struct.unpack(f"<{w.getnframes()}h", w.readframes(w.getnframes())))


def test_gap_silence_is_written_after_the_frame_that_preceded_it(tmp_path):
    """packet 0, [1 lost], packet 2 → WAV is frame0 → silence → frame2, not silence first."""
    p = make_pipeline(tmp_path)
    run_stream(p, packets([0, 2]))
    pcm = wav_pcm(p.sink.files[0])
    assert len(pcm) == 3 * SAMPLES
    assert set(pcm[:SAMPLES]) == {1000}                   # frame 0 (StubDecoder value)
    assert set(pcm[SAMPLES:2 * SAMPLES]) == {0}           # the lost packet's silence
    assert set(pcm[2 * SAMPLES:]) == {1000}               # frame 2


def test_gap_before_a_fragment_of_the_next_frame_keeps_the_complete_pending_frame(tmp_path):
    """Fragmented stream: frame A = ids 1(f0)+2(f1); id 3 (f0 of B) is LOST; id 4 arrives
    as f1 of B. The arriving index says B started at id 3, so A (ending at id 2) is
    complete and must be emitted; B lacks its fragment 0 and is the only frame lost."""
    big = bytes(range(200))
    stream = [ob.build_packet(1, 0, big[:100]), ob.build_packet(2, 1, big[100:]),
              ob.build_packet(4, 1, big[100:]),                    # id 3 = B's fragment 0, lost
              ob.build_packet(5, 0, FRAME), ob.build_packet(6, 0, FRAME)]
    p = make_pipeline(tmp_path)
    st = run_stream(p, stream)
    assert st.packets_lost == 1
    assert p.decoder.frames == [big, FRAME, FRAME]
    assert st.frames_complete == 3 and st.frames_dropped == 1


def test_gap_that_swallows_a_trailing_fragment_still_drops_that_frame(tmp_path):
    """Frame A = ids 1(f0)+2(f1)+3(f2); id 2 lost → A is broken, not emitted."""
    big = bytes(range(240))
    stream = [ob.build_packet(1, 0, big[:80]), ob.build_packet(3, 2, big[160:]),
              ob.build_packet(4, 0, FRAME)]
    p = make_pipeline(tmp_path)
    st = run_stream(p, stream)
    assert p.decoder.frames == [FRAME]
    assert st.frames_dropped == 1 and st.frames_complete == 1


def test_fragmentation_memory_is_reset_per_connection(tmp_path):
    """MTU (hence fragmentation) is negotiated per connection: an earlier fragmented
    session must not make a later unfragmented session drop whole frames on a gap."""
    fragmented = [ob.build_packet(1, 0, FRAME), ob.build_packet(2, 1, FRAME), ob.build_packet(3, 0, FRAME)]
    t = FakeTransport([fragmented, packets([10, 12, 13])])   # session 2: unfragmented, id 11 lost
    s = run_bridge(t, tmp_path, reconnect=True, max_seconds=0.3)
    assert s.reconnects == 1 and s.packets_lost == 1
    assert s.frames_dropped == 0 and s.frames_complete == 2 + 3


def test_gap_fill_is_capped_per_event_and_by_wall_clock(tmp_path):
    """Greptile P1 (security): a forward id jump used to write delta × frame of silence
    with no bound (≈20 MB per notification). Now: ≤ max_gap_fill_s per event, and the
    total silence never runs ahead of the wall clock by more than that allowance."""
    now = [0.0]
    framer = ob.OmiFramer()
    p = ob.AudioPipeline(framer, ob.StubDecoder(SAMPLES), ob.WavSink(tmp_path),
                         max_gap_fill_s=1.0, clock=lambda: now[0])
    p.on_packet(ob.build_packet(0, 0, FRAME))
    p.on_packet(ob.build_packet(1000, 0, FRAME))          # 999 lost ≈ 20 s → capped to 1 s
    assert p.silence_samples == 16000 and framer.stats.silence_capped_events == 1
    p.on_packet(ob.build_packet(2000, 0, FRAME))          # wall clock still 0 → allowance spent
    assert p.silence_samples == 16000 and framer.stats.silence_capped_events == 2
    now[0] = 10.0                                         # 10 s elapsed → budget is back
    p.on_packet(ob.build_packet(3000, 0, FRAME))
    assert p.silence_samples == 32000
    p.flush(); p.sink.close()
    assert p.sink.total_samples == 4 * SAMPLES + 32000
    assert framer.stats.packets_lost == 3 * 999          # accounting is untouched by the cap


def test_negative_control_uncapped_gap_fill_writes_the_whole_jump(tmp_path):
    p = ob.AudioPipeline(ob.OmiFramer(), ob.StubDecoder(SAMPLES), ob.WavSink(tmp_path),
                         max_gap_fill_s=None, clock=lambda: 0.0)
    p.on_packet(ob.build_packet(0, 0, FRAME))
    p.on_packet(ob.build_packet(1000, 0, FRAME))
    assert p.silence_samples == 999 * SAMPLES


def test_wav_roll_splits_one_large_write_at_the_boundary(tmp_path):
    sink = ob.WavSink(tmp_path, roll_seconds=2 * SAMPLES / 16000)   # two frames per file
    sink.write(bytes(2 * 5 * SAMPLES))                               # one 5-frame block
    sink.close()
    assert [wav_samples(f) for f in sink.files] == [2 * SAMPLES, 2 * SAMPLES, SAMPLES]
    assert sink.total_samples == 5 * SAMPLES


def test_bridge_rebuilds_the_decoder_for_the_codec_the_pendant_reports(tmp_path):
    """--codec 21 but the pendant answers 20 (10 ms frames): the decoder is rebuilt for
    20 before any audio flows, so a lost packet is concealed with 160 samples, not 320."""
    built = []

    def factory(codec_id):
        built.append(codec_id)
        return ob.StubDecoder(ob.FRAME_SAMPLES_BY_CODEC[codec_id])

    t = FakeTransport([packets([0, 2])], codec=ob.CODEC_OPUS_10MS)
    pipeline = ob.AudioPipeline(ob.OmiFramer(), ob.StubDecoder(SAMPLES), ob.WavSink(tmp_path))
    bridge = ob.Bridge(t, pipeline, backoff=(0.01,), reconnect=False, max_seconds=0.3,
                       expected_codec=ob.CODEC_OPUS_FS320, decoder_factory=factory)
    s = asyncio.run(bridge.run())
    assert built == [ob.CODEC_OPUS_10MS] and s.codec_id == 20
    assert pipeline.decoder.frame_samples == 160 and pipeline.silence_samples == 160
    assert s.wav_seconds == pytest.approx(3 * 0.01)


def test_bridge_keeps_the_decoder_when_the_reported_codec_matches(tmp_path):
    t = FakeTransport([packets(range(3))], codec=ob.CODEC_OPUS_FS320)
    pipeline = ob.AudioPipeline(ob.OmiFramer(), ob.StubDecoder(SAMPLES), ob.WavSink(tmp_path))
    bridge = ob.Bridge(t, pipeline, backoff=(0.01,), reconnect=False, max_seconds=0.3,
                       expected_codec=ob.CODEC_OPUS_FS320,
                       decoder_factory=lambda c: pytest.fail("must not rebuild on a match"))
    asyncio.run(bridge.run())
    assert pipeline.decoder.frame_samples == SAMPLES


def test_ctrl_c_still_writes_the_summary(tmp_path, monkeypatch):
    """README: 'Ctrl-C ends a run cleanly (summary still written)'. On Python < 3.11
    asyncio.run installs no SIGINT handler, so the KeyboardInterrupt used to escape
    main() before summary.json existed."""
    import os
    import signal
    import threading

    t = FakeTransport([packets(range(10))])            # one session that never drops
    monkeypatch.setattr(ob, "BleakTransport", lambda **kw: t)
    monkeypatch.setattr(ob, "make_decoder", lambda codec_id: ob.StubDecoder(SAMPLES))
    timer = threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGINT))
    timer.start()
    try:
        rc = ob.main(["--device-name", "Omi", "--out", str(tmp_path)])
    finally:
        timer.cancel()
    summary = __import__("json").loads((tmp_path / "summary.json").read_text())
    assert rc == 0 and summary["packets_received"] == 10 and summary["interrupted"] is True
    assert summary["battery_end"] == 80 and not t.connected


def test_wer_cli_rejects_a_transcript_count_mismatch(tmp_path, capsys):
    ref = tmp_path / "ref.txt"
    hyp = tmp_path / "hyp.txt"
    ref.write_text("one two three\nfour five six\n")
    hyp.write_text("one two three\n")
    assert wer.main(["--ref", str(ref), "--hyp", str(hyp)]) == 2
    out = capsys.readouterr()
    assert "corpus WER" not in out.out and "2 reference" in out.err and "1 " in out.err
    hyp.write_text("one two three\nfour five six\nextra line\n")
    assert wer.main(["--ref", str(ref), "--hyp", str(hyp)]) == 2
    hyp.write_text("one two three\nfour fife six\n")
    assert wer.main(["--ref", str(ref), "--hyp", str(hyp)]) == 0
    assert "corpus WER 16.7% over 2 sentences" in capsys.readouterr().out


def test_wer_cli_reads_replay_samples_json_in_file_order(tmp_path, capsys):
    """--hyp may be replay_samples.py's --json output: {"rows": [{"file", "transcript"}]}."""
    import json
    ref = tmp_path / "ref.txt"
    ref.write_text("alpha bravo\ncharlie delta\n")
    replay = tmp_path / "replay.json"
    replay.write_text(json.dumps({"counts": {}, "stt_mode": "remote", "rows": [
        {"file": "seg_01.wav", "transcript": "alpha bravo"},
        {"file": "seg_02.wav", "transcript": ""}]}))
    assert wer.main(["--ref", str(ref), "--hyp", str(replay)]) == 0
    assert "corpus WER 50.0% over 2 sentences" in capsys.readouterr().out
    assert wer.load_hypotheses(replay) == ["alpha bravo", ""]


def test_split_on_silence_rerun_replaces_the_previous_segments(tmp_path):
    import array
    rate = 16000
    pcm = array.array("h", [0] * rate)
    old = sos.write_segments(pcm, rate, [(0, 100), (100, 200), (200, 300)], tmp_path, "cap")
    assert len(old) == 3 and all(p.exists() for p in old)
    (tmp_path / "other_01.wav").write_bytes(b"keep")          # a different stem is not ours
    new = sos.write_segments(pcm, rate, [(0, 100), (100, 200)], tmp_path, "cap")
    assert sorted(p.name for p in tmp_path.glob("*.wav")) == ["cap_01.wav", "cap_02.wav", "other_01.wav"]
    assert new == old[:2]


# --- review round 2 (PR #1693) --------------------------------------------------------------
def test_bridge_reconnects_after_a_drop_during_setup(tmp_path):
    """A link that dies inside start_notify() on the first connect is a DROP, not a fatal
    error: with --reconnect the bridge must follow the reconnect path and capture."""
    t = FakeTransport([packets(range(10))], drop_during_setup=True)
    s = run_bridge(t, tmp_path, reconnect=True, max_seconds=0.3)
    assert t.connects == 2 and s.reconnects == 1 and s.connect_failures == 0
    assert s.packets_received == 10 and s.frames_complete == 10
    assert (s.model, s.codec_id, s.battery_start) == ("Omi CV 1", 21, 90)   # identity re-read after the drop


def test_bridge_without_reconnect_stops_at_a_setup_drop(tmp_path):
    t = FakeTransport([packets(range(10))], drop_during_setup=True)
    s = run_bridge(t, tmp_path, reconnect=False, max_seconds=0.3)
    assert t.connects == 1 and s.reconnects == 0 and s.packets_received == 0


def test_wer_cli_rejects_replay_rows_with_an_stt_error(tmp_path, capsys):
    """A row replay_samples.py marked stt_error (empty transcript) is a transcription
    FAILURE, not a mishearing: refuse to score it rather than count it as 100 % WER."""
    import json
    ref = tmp_path / "ref.txt"
    ref.write_text("alpha bravo\ncharlie delta\n")
    replay = tmp_path / "replay.json"
    replay.write_text(json.dumps({"rows": [
        {"file": "seg_01.wav", "transcript": "alpha bravo"},
        {"file": "seg_02.wav", "transcript": "", "stt_error": "HTTP 503 from /stt"}]}))
    assert wer.main(["--ref", str(ref), "--hyp", str(replay)]) == 2
    out = capsys.readouterr()
    assert "corpus WER" not in out.out and "seg_02.wav" in out.err and "HTTP 503" in out.err
    replay.write_text(json.dumps({"rows": [                       # an honestly empty transcript still scores
        {"file": "seg_01.wav", "transcript": "alpha bravo"},
        {"file": "seg_02.wav", "transcript": ""}]}))
    assert wer.main(["--ref", str(ref), "--hyp", str(replay)]) == 0
    assert "corpus WER 50.0%" in capsys.readouterr().out


def test_split_on_silence_rerun_replaces_segments_of_a_glob_looking_stem(tmp_path):
    import array
    rate = 16000
    pcm = array.array("h", [0] * rate)
    sos.write_segments(pcm, rate, [(0, 100), (100, 200), (200, 300)], tmp_path, "cap[1]")
    sos.write_segments(pcm, rate, [(0, 100), (100, 200)], tmp_path, "cap[1]")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cap[1]_01.wav", "cap[1]_02.wav"]
