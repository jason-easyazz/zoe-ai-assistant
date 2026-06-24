/*
 * Panel theme: light by day, dark by night (sun-driven), with a manual override.
 * Sets data-theme="light|dark" on <html>. Mode persisted in localStorage:
 *   'auto' (default) → follows local sunrise/sunset; 'light'/'dark' → pinned.
 * SkybridgeTheme.setMode('auto'|'light'|'dark') from the Settings control.
 */
(function () {
  var LAT = -28.774, LON = 114.612; // Geraldton; TODO: from panel config
  var KEY = 'sky_theme_mode';

  // Compact sunrise/sunset (NOAA approximation) → {rise,set} as Date today.
  function sunTimes(date) {
    var rad = Math.PI / 180, day = Math.floor((date - new Date(date.getFullYear(), 0, 0)) / 864e5);
    var decl = -23.44 * Math.cos(rad * (360 / 365) * (day + 10));
    var ha = Math.acos(Math.max(-1, Math.min(1, -Math.tan(LAT * rad) * Math.tan(decl * rad)))) / rad;
    var solarNoon = 12 - LON / 15 - (date.getTimezoneOffset() / 60 + LON / 15) * 0 - eqTime(day) / 60;
    function mk(h) { var d = new Date(date); d.setHours(0, 0, 0, 0); d.setMinutes(h * 60); return d; }
    var noonLocal = 12 + (-(date.getTimezoneOffset()) / 60) - LON / 15;
    return { rise: mk(noonLocal - ha / 15), set: mk(noonLocal + ha / 15) };
  }
  function eqTime(day) { var b = (360 / 365) * (day - 81) * Math.PI / 180; return 9.87 * Math.sin(2 * b) - 7.53 * Math.cos(b) - 1.5 * Math.sin(b); }

  function isDay() { var now = new Date(), t = sunTimes(now); return now >= t.rise && now < t.set; }
  function mode() { try { return localStorage.getItem(KEY) || 'auto'; } catch (e) { return 'auto'; } }
  function apply() {
    var m = mode(), theme = (m === 'light' || m === 'dark') ? m : (isDay() ? 'light' : 'dark');
    document.documentElement.setAttribute('data-theme', theme);
  }
  apply();
  setInterval(apply, 60000);
  window.SkybridgeTheme = {
    apply: apply,
    getMode: mode,
    setMode: function (m) { try { localStorage.setItem(KEY, m); } catch (e) {} apply(); }
  };
})();
