"use strict";

// DISABLED: Web Speech API voice path replaced by native Pi daemon (zoe_voice_daemon.py).
// The Pi daemon POSTs to /api/voice/command directly; browser SpeechRecognition is not used.
// This file is kept for reference but exports a no-op stub so existing callers don't throw.

const VoiceTouch = (() => {
  // No-op stub — all voice is handled by the hardware daemon on the Pi.
  return {
    start: () => {},
    stop: () => {},
    isSupported: () => false,
    isListening: () => false,
  };
})();

// ── ORIGINAL IMPLEMENTATION (disabled) ──────────────────────────────────────
/* eslint-disable */
const _VoiceTouch_DISABLED = (() => {
  let recognition = null;
  let listening = false;

  function isSupported() {
    return "webkitSpeechRecognition" in window || "SpeechRecognition" in window;
  }

  function getRecognizer() {
    if (!isSupported()) return null;
    if (recognition) return recognition;
    const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition;
    recognition = new Ctor();
    recognition.continuous = true;
    recognition.interimResults = false;
    recognition.lang = navigator.language || "en-US";
    recognition.onresult = (e) => {
      const transcript = Array.from(e.results).slice(-1)[0][0].transcript.trim();
      document.dispatchEvent(new CustomEvent("voice:command", { detail: { transcript } }));
    };
    recognition.onend = () => { listening = false; document.dispatchEvent(new Event("voice:stopped")); };
    recognition.onerror = () => { listening = false; document.dispatchEvent(new Event("voice:error")); };
    return recognition;
  }

  function start() {
    const rec = getRecognizer();
    if (!rec || listening) return;
    listening = true;
    document.dispatchEvent(new Event("voice:starting"));
    rec.start();
    document.dispatchEvent(new Event("voice:started"));
  }

  function stop() {
    if (!recognition || !listening) return;
    recognition.stop();
  }

  return { isSupported, start, stop };
// (original implementation body ends here)
})(); // _VoiceTouch_DISABLED
/* eslint-enable */

window.VoiceTouch = VoiceTouch;


