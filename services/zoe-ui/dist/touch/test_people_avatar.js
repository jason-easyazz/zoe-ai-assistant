/*
 * Controlled test for personAvatarInner() in skybridge-renderer.js — the people
 * card avatar (contact photo over initials). Pins the photo-present path, the
 * initials fallback, the URL scheme allowlist (https / root-relative only), and
 * CSS-punctuation stripping. Extracts the REAL function bodies and runs them.
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

// personAvatarInner depends on initialsFor + escapeHtml.
// eslint-disable-next-line no-eval
eval(['escapeHtml', 'initialsFor', 'personAvatarInner'].map(extract).join('\n'));

const hasImg = (s) => /class="people-avatar-img"/.test(s);

// (a) https photo → background-image layer over initials
const photo = personAvatarInner({ name: 'Ada Lovelace', photo: 'https://ex.com/a.jpg' });
assert(hasImg(photo), 'https photo renders the image layer');
assert(/background-image:url\('https:\/\/ex.com\/a.jpg'\)/.test(photo), 'background-image set to the url');
assert(/AL/.test(photo), 'initials kept as fallback behind the photo');

// (b) no url → initials only
const none = personAvatarInner({ name: 'Bob Khan' });
assert(!hasImg(none) && /BK/.test(none), 'no url falls back to initials only');

// (c) scheme allowlist: reject http / file / data / protocol-relative, allow root-relative
assert(!hasImg(personAvatarInner({ name: 'X', photo: 'http://insecure/a.jpg' })), 'http rejected (mixed content)');
assert(!hasImg(personAvatarInner({ name: 'X', photo: 'file:///etc/passwd' })), 'file:// rejected');
assert(!hasImg(personAvatarInner({ name: 'X', photo: 'data:image/svg+xml,<svg/>' })), 'data: rejected');
assert(!hasImg(personAvatarInner({ name: 'X', photo: '//evil.com/a.jpg' })), 'protocol-relative rejected');
assert(!hasImg(personAvatarInner({ name: 'X', photo: 'javascript:alert(1)' })), 'javascript: rejected');
assert(hasImg(personAvatarInner({ name: 'X', photo: '/media/x.jpg' })), 'root-relative same-origin allowed');

// (d) CSS-dangerous punctuation stripped from an allowed url
const dirty = personAvatarInner({ name: 'X', photo: "https://e.com/a').url(.jpg" });
assert(/background-image:url\('https:\/\/e.com\/a.url.jpg'\)/.test(dirty), 'quotes/parens/backslash stripped before render');

console.log('personAvatarInner: all assertions passed');
