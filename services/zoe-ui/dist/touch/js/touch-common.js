"use strict";

// Core utilities shared across touch pages

const TouchCommon = (() => {
  const isTouchDevice = () => (
    "ontouchstart" in window || navigator.maxTouchPoints > 0 || navigator.msMaxTouchPoints > 0
  );

  const STORAGE_KEYS = {
    interfaceMode: "zoe.interface.mode", // "touch" | "desktop"
    ambientEnabled: "zoe.touch.ambient.enabled",
  };

  function readInterfaceMode() {
    const urlMode = new URLSearchParams(location.search).get("mode");
    if (urlMode === "touch" || urlMode === "desktop") return urlMode;
    return localStorage.getItem(STORAGE_KEYS.interfaceMode) || (isTouchDevice() ? "touch" : "desktop");
  }

  function setInterfaceMode(mode) {
    if (mode !== "touch" && mode !== "desktop") return;
    localStorage.setItem(STORAGE_KEYS.interfaceMode, mode);
  }

  function navigateTo(path) {
    window.location.href = path;
  }

  // Lightweight event bus
  const bus = new EventTarget();
  function on(eventName, handler) { bus.addEventListener(eventName, handler); }
  function off(eventName, handler) { bus.removeEventListener(eventName, handler); }
  function emit(eventName, detail) { bus.dispatchEvent(new CustomEvent(eventName, { detail })); }

  // Helpers
  const clamp = (value, min, max) => Math.min(Math.max(value, min), max);
  const debounce = (fn, wait) => {
    let t;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn.apply(null, args), wait);
    };
  };
  const throttle = (fn, limit) => {
    let inThrottle = false;
    let lastArgs;
    return function throttled(...args) {
      lastArgs = args;
      if (!inThrottle) {
        fn.apply(null, lastArgs);
        inThrottle = true;
        setTimeout(() => { inThrottle = false; if (lastArgs) fn.apply(null, lastArgs); }, limit);
      }
    };
  };

  // Minimal API helper (expects backend endpoints already exist)
  async function fetchJSON(url, opts = {}) {
    const res = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }

  // DOM helper
  function h(tag, attrs = {}, children = []) {
    const el = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => {
      if (k === "class") el.className = v;
      else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v);
    });
    (Array.isArray(children) ? children : [children]).forEach((c) => {
      if (c == null) return;
      if (typeof c === "string") el.appendChild(document.createTextNode(c));
      else el.appendChild(c);
    });
    return el;
  }

  // Interface switch control: attach to any element with data-interface-toggle
  function wireInterfaceSwitches() {
    document.querySelectorAll("[data-interface-toggle]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const current = readInterfaceMode();
        const next = current === "touch" ? "desktop" : "touch";
        setInterfaceMode(next);
        emit("interface:modeChanged", { mode: next });
        // Navigate to sibling path if provided
        const to = btn.getAttribute("data-to");
        if (to) navigateTo(to);
      });
    });
  }

  function ready(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }

  return {
    isTouchDevice,
    STORAGE_KEYS,
    readInterfaceMode,
    setInterfaceMode,
    navigateTo,
    on,
    off,
    emit,
    clamp,
    debounce,
    throttle,
    fetchJSON,
    h,
    wireInterfaceSwitches,
    ready,
  };
})();

window.TouchCommon = TouchCommon;


