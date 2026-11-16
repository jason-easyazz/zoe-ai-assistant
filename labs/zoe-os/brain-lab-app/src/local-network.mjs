/** Lab-only fetch boundary: validate before request construction, never follow redirects. */
import { isIP } from 'node:net';

function checkedURL(raw, explicitPort) {
  const match = /^http:\/\/(localhost|127(?:\.[0-9]{1,3}){3}|\[::1\])(?::([0-9]{1,5}))?(\/.*)?$/.exec(raw);
  if (!match || (explicitPort && !match[2]) || Number(match[2] ?? 80) < 1 || Number(match[2] ?? 80) > 65535 ||
      (match[1].startsWith('127.') && isIP(match[1]) !== 4) || /[\s\\%?#@]/.test(raw)) {
    throw new Error('lab fetch requires an http loopback URL with an explicit port');
  }
  return new URL(raw).origin;
}

export function localOrigin(raw) {
  return checkedURL(raw, true);  // configuration must have an explicit port before any SDK call
}

export function localFetch(fetcher) {
  return (input, init = {}) => {
    const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    // The SDK's URL constructor removes an explicit :80. At transport time it is still
    // loopback HTTP; require the explicit port at configuration time, before normalization.
    checkedURL(raw, false);
    return fetcher(input, { ...init, redirect: 'error' });
  };
}

// pi-ai's HTTP client and the tools both use the process fetch. Install before either runs.
export const labFetch = localFetch(globalThis.fetch);
globalThis.fetch = labFetch;
