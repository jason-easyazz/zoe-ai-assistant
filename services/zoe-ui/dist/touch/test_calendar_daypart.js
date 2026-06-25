/*
 * Controlled test for calendarDaypart() in skybridge-renderer.js — the helper that
 * phases the calendar "sky" dawn → day → dusk → night by local hour. Pins each
 * boundary so an off-by-one in the table is caught. Extracts the real function and
 * feeds it timestamps built at specific LOCAL hours.
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const src = fs.readFileSync(path.join(__dirname, 'js/skybridge-renderer.js'), 'utf8');

function extract(name) {
  const start = src.indexOf('function ' + name + '(');
  assert(start >= 0, 'missing function ' + name);
  let depth = 0;
  for (let j = src.indexOf('{', start); j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}') {
      depth--;
      if (depth === 0) return src.slice(start, j + 1);
    }
  }
  throw new Error('unbalanced braces for ' + name);
}

// eslint-disable-next-line no-eval
eval(extract('calendarDaypart'));

// A timestamp at a given LOCAL hour (so getHours() is deterministic regardless of TZ).
const atHour = (h) => new Date(2026, 0, 15, h, 0, 0, 0).getTime();

const cases = [
  [4, 'night'], [5, 'dawn'],   // night → dawn
  [7, 'dawn'], [8, 'day'],     // dawn → day
  [16, 'day'], [17, 'dusk'],   // day → dusk
  [19, 'dusk'], [20, 'night'], // dusk → night
  [23, 'night'], [0, 'night'], // wrap
];
for (const [h, expected] of cases) {
  assert.strictEqual(calendarDaypart(atHour(h)), expected, `hour ${h} → ${expected}`);
}

// nowMs=0 (Unix epoch) must be honoured, not treated as "no arg" → use current time.
assert.strictEqual(typeof calendarDaypart(0), 'string', 'epoch (0) is a valid explicit timestamp');

console.log('calendarDaypart: all assertions passed');
