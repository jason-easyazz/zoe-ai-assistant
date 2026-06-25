/*
 * Panel theme: light by day, dark by night (sun-driven), with a manual override.
 * Sets data-theme="light|dark" on <html>. Mode persisted in localStorage:
 *   'auto' (default) → follows local sunrise/sunset; 'light'/'dark' → pinned.
 * SkybridgeTheme.setMode('auto'|'light'|'dark') from the Settings control.
 */
(function () {
  var KEY = 'sky_theme_mode';
  var GERALDTON = { lat: -28.774, lon: 114.612 };

  // Location for the sun calc, in priority order: an explicit panel config global,
  // a stored panel location, else the home default. Lets a second panel in another
  // city theme correctly without code changes; falls back safely if unconfigured.
  function panelLoc() {
    try {
      if (window.ZOE_PANEL_LOCATION && isFinite(window.ZOE_PANEL_LOCATION.lat)) return window.ZOE_PANEL_LOCATION;
      var raw = localStorage.getItem('sky_panel_location');
      if (raw) { var p = JSON.parse(raw); if (isFinite(p.lat) && isFinite(p.lon)) return p; }
    } catch (e) {}
    return GERALDTON;
  }

  // Compact sunrise/sunset (NOAA approximation, w/ equation-of-time) → {rise,set}.
  function sunTimes(date) {
    var loc = panelLoc(), LAT = loc.lat, LON = loc.lon;
    var rad = Math.PI / 180, day = Math.floor((date - new Date(date.getFullYear(), 0, 0)) / 864e5);
    var decl = -23.44 * Math.cos(rad * (360 / 365) * (day + 10));
    var ha = Math.acos(Math.max(-1, Math.min(1, -Math.tan(LAT * rad) * Math.tan(decl * rad)))) / rad;
    function mk(h) { var d = new Date(date); d.setHours(0, 0, 0, 0); d.setMinutes(h * 60); return d; }
    var noonLocal = 12 + (-(date.getTimezoneOffset()) / 60) - LON / 15 - eqTime(day) / 60;
    return { rise: mk(noonLocal - ha / 15), set: mk(noonLocal + ha / 15) };
  }
  function eqTime(day) { var b = (360 / 365) * (day - 81) * Math.PI / 180; return 9.87 * Math.sin(2 * b) - 7.53 * Math.cos(b) - 1.5 * Math.sin(b); }

  function isDay() { var now = new Date(), t = sunTimes(now); return now >= t.rise && now < t.set; }
  function mode() { try { return localStorage.getItem(KEY) || 'auto'; } catch (e) { return 'auto'; } }
  // Resting/idle-screen dimming so the standby clock never lights up the room:
  // deep = sleep hours (22:00–06:00), then night (after sunset) / day.
  function restDim() {
    var h = new Date().getHours();
    if (h >= 22 || h < 6) return 'deep';
    return isDay() ? 'day' : 'night';
  }
  function apply() {
    var m = mode(), theme = (m === 'light' || m === 'dark') ? m : (isDay() ? 'light' : 'dark');
    document.documentElement.setAttribute('data-theme', theme);
    document.documentElement.setAttribute('data-rest-dim', restDim());
    // Keep the legacy `dark-mode` class in sync with the theme. Old touch-adapter
    // rules force white text under `html.dark-mode` with !important; if that class
    // lingers while data-theme is "light", every card's secondary text washes out
    // to white-on-white. Toggle it so those legacy rules only fire in dark theme.
    document.documentElement.classList.toggle('dark-mode', theme === 'dark');
    document.documentElement.classList.toggle('light-mode', theme === 'light');
  }
  apply();
  setInterval(apply, 60000);
  window.SkybridgeTheme = {
    apply: apply,
    getMode: mode,
    setMode: function (m) { try { localStorage.setItem(KEY, m); } catch (e) {} apply(); },
    // Local sunrise/sunset for today (the clock card surfaces these).
    sunTimes: function () { return sunTimes(new Date()); }
  };
})();
