/*
 * Controlled test for getHomeCards() in skybridge-capabilities.js — the guest
 * dashboard glance cards rendered on wake. Pins the shape: a time/clock card +
 * a room-controls card. Extracts the real function body and runs it.
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const src = fs.readFileSync(path.join(__dirname, 'js/skybridge-capabilities.js'), 'utf8');

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
eval(extract('getHomeCards'));

const cards = getHomeCards();
assert(Array.isArray(cards) && cards.length === 2, 'returns two glance cards');

const clock = cards[0];
assert(clock.component === 'status' && clock.props.source === 'clock_show', 'first card is the time/clock card');
assert(/^\d{1,2}$/.test(clock.props.hour), 'clock hour is 1–2 digits (12h)');
assert(/^\d{2}$/.test(clock.props.minute), 'clock minute is zero-padded');
assert(['AM', 'PM'].includes(clock.props.meridiem), 'meridiem is AM/PM');
assert(/morning|afternoon|evening/.test(clock.props.summary), 'greeting is set');
assert(typeof clock.props.weekday === 'string' && clock.props.weekday.length > 0, 'weekday present');

const room = cards[1];
assert(room.component === 'status' && /room controls/i.test(room.props.title), 'second card is room controls');
assert(room.props.actions && room.props.actions[0] && room.props.actions[0].query === 'smart home', 'room card opens the controls');

console.log('getHomeCards: all assertions passed');
