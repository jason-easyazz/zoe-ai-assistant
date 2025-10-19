"use strict";

// Minimal voice integration hooks (no external deps). Uses Web Speech API if available.

const VoiceTouch = (() => {
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
})();

window.VoiceTouch = VoiceTouch;


