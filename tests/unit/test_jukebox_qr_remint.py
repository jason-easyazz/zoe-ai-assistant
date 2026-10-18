"""The panel's jukebox QR is re-minted before its token expires, for as long as the music surface stays up (touch/home.html).

Until now ``wireMusic()`` minted once per time the music card was built; a music screen left up for hours kept showing the QR it
had at the start, and past ``ZOE_JUKEBOX_TTL_S`` (2 h) a phone that scanned it got "scan the code again". ``jukeboxQR()`` is now a
function the 5 s music poll calls too. This runs the REAL function text (cut out of the page) in node against a fake element, a
fake ``apiJson`` and a fake clock - no browser. Fails (not skips) on CI without node, like the other node harnesses.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

HOME = Path(__file__).resolve().parents[2] / "services" / "zoe-ui" / "dist" / "touch" / "home.html"

HARNESS = r"""
const src = process.argv[1];
let now = 1000000, calls = 0, mode = 'ok', ttl = 7200;
const el = { dataset: {}, innerHTML: '', kids: [], appendChild(c) { this.kids.push(c); } };
const document = { getElementById: (id) => (id === 'mQRCode' ? el : null), createElement: () => ({}) };
const Date_ = { now: () => now };
const _panelDevId = () => 'kitchen';
const pending = [];
function apiJson() {
  calls++;
  if (mode === 'fail') return Promise.reject(new Error('down'));
  return Promise.resolve({ ok: true, qr_path: '/api/music/jukebox/qr/h' + calls, expires_in: ttl });
}
const jukeboxQR = new Function('document', 'Date', 'apiJson', '_panelDevId', src + '; return jukeboxQR;')(
  document, Date_, apiJson, _panelDevId);
const tick = async (s) => { now += s * 1000; jukeboxQR(); await new Promise((r) => setImmediate(r)); };
(async () => {
  const out = {};
  await tick(0); out.first = { calls, kids: el.kids.length, src: el.kids[0] && el.kids[0].src };
  for (let i = 0; i < 100; i++) await tick(5);            // 500 s of 5 s polls: well inside the token's life
  out.during = { calls };
  await tick(7200 - 500 - 300 + 1);                        // 5 min before expiry: re-mint
  out.remint = { calls, src: el.kids[el.kids.length - 1].src };
  mode = 'fail'; ttl = 7200;
  await tick(7200 + 1);                                    // the token is now expired and the mint keeps failing
  out.expired = { calls, innerHTML: el.innerHTML };
  mode = 'ok';
  await tick(61);
  out.recovered = { calls, src: el.kids[el.kids.length - 1].src, innerHTML: el.innerHTML };
  console.log(JSON.stringify(out));
})();
"""


def _function_source() -> str:
    text = HOME.read_text(encoding="utf-8")
    m = re.search(r"function jukeboxQR\(\)\{.*?\n    jukeboxQR\(\);", text, re.S)
    assert m, "touch/home.html lost jukeboxQR()"
    return m.group(0).rsplit("jukeboxQR();", 1)[0]


def _node() -> str:
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("Node.js is not installed on this CI host, so the jukebox re-mint harness cannot run.")
        pytest.skip("Node.js is not installed on this host")
    return node


def _run(src: str) -> dict:
    proc = subprocess.run([_node(), "-e", HARNESS, src], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_qr_is_re_minted_before_it_expires_and_never_left_expired():
    out = _run(_function_source())
    assert out["first"]["calls"] == 1 and out["first"]["kids"] == 1                  # minted on build
    assert out["during"]["calls"] == 1                                               # not on every 5 s poll
    assert out["remint"]["calls"] == 2 and out["remint"]["src"].endswith("/h2")      # re-minted ahead of the 2 h expiry
    assert out["expired"]["calls"] >= 3 and out["expired"]["innerHTML"] == ""        # mint failing past expiry: no stale code shown
    assert out["recovered"]["src"].endswith(f"/h{out['recovered']['calls']}")        # and it comes back when the server does


def test_negative_control_the_old_once_per_build_behaviour_fails_the_same_harness():
    """The pre-fix block ran once per music-card build and had no clock: strip the poll and the harness must notice."""
    src = _function_source()
    stale = src.replace("if(!qr||+qr.dataset.exp>Date.now())return;", "if(!qr||qr.dataset.exp)return;")
    assert stale != src
    assert _run(stale)["remint"]["calls"] == 1, "the control must show the old behaviour (never re-mints)"


def test_the_music_poll_and_the_build_both_call_it():
    text = HOME.read_text(encoding="utf-8")
    assert "if(_cur==='music'){loadMusic();jukeboxQR();}" in text
    assert len(re.findall(r"(?<!function )jukeboxQR\(\)", text)) >= 3  # build + poll + (definition counted separately)
