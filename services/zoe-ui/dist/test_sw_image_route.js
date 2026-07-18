// Guard: the image CacheFirst route must stay SAME-ORIGIN.
//
// A cross-origin <img> is a no-cors request → opaque response → CacheFirst can't
// validate it and the fetch dies with net::ERR_FAILED. That silently broke every
// remote album cover on the panel (Music Assistant serves art from i.ytimg.com /
// yt3.googleusercontent.com): the API returned good urls, the panel could curl
// them, and the Cover Flow still rendered broken-image glyphs.
//
// This is a STATIC check on the route predicate, not a live SW test — it can't
// prove images load, only that the origin guard hasn't been dropped again.
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, 'sw.js'), 'utf8');

let failed = 0;
const check = (name, cond) => {
  console.log(`  ${cond ? 'PASS' : 'FAIL'} ${name}`);
  if (!cond) failed++;
};

// find the image route's matcher
const m = src.match(/registerRoute\(\s*\(\{([^}]*)\}\)\s*=>\s*request\.destination === 'image'([^,]*),/);
check('image route exists', !!m);
if (m) {
  const matcher = m[0];
  check('matcher destructures url (so it can compare origins)', /\burl\b/.test(m[1]));
  check('matcher compares against self.location.origin',
        /url\.origin\s*===\s*self\.location\.origin/.test(matcher));
}

// and nothing else may blanket-route every image
const broad = /registerRoute\(\s*\(\{\s*request\s*\}\)\s*=>\s*request\.destination === 'image'\s*,/.test(src);
check('no route matches ALL images regardless of origin', !broad);

console.log(failed ? `\n  ${failed} check(s) failed` : '\n  sw image route: same-origin guard intact');
process.exit(failed ? 1 : 0);
