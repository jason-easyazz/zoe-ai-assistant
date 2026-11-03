// Force a Node server's TCP listeners onto one address (security fix 2026-10-19).
//
// WHY: the Flue 2.x sidecars - the live brain (flue-zoe-brain-2x, :3579) and the Telegram bot (flue-zoe-telegram, :3582) -
// run Flue's generated entry (`dist/server.mjs` -> startFlueNodeServer({ port })), which passes NO hostname, so Node binds
// every interface and both ports answer the whole LAN. Nothing off the box calls them (zoe-data reaches both on 127.0.0.1;
// measured 2026-10-19), and Flue 2.1.1 reads no HOST variable, so the bind cannot be set by configuration.
//
// HOW: preloaded with `NODE_OPTIONS=--import=<this file>` and `ZOE_LISTEN_HOST=127.0.0.1` (drop-ins
// scripts/setup/systemd/flue-zoe-*.service.d/60-loopback.conf). It wraps net.Server.prototype.listen so a TCP listen -
// `listen(port[, host][, backlog][, cb])` or `listen({ port, host })` - uses ZOE_LISTEN_HOST. A Unix socket path, a
// handle / fd, and a listen with no port are untouched. Unset or empty ZOE_LISTEN_HOST = this file does nothing.
// Pinned by tests/unit/test_node_listen_host.py.
import net from 'node:net';

const host = (process.env.ZOE_LISTEN_HOST || '').trim();

function isPort(v) {
  return typeof v === 'number' || (typeof v === 'string' && /^\d+$/.test(v));
}

if (host) {
  const original = net.Server.prototype.listen;
  net.Server.prototype.listen = function zoeListenHost(...args) {
    const first = args[0];
    if (isPort(first)) {
      if (args.length > 1 && (args[1] === undefined || args[1] === null || typeof args[1] === 'string')) {
        args[1] = host;
      } else {
        args.splice(1, 0, host);
      }
    } else if (first && typeof first === 'object' && !Array.isArray(first)
               && first.path === undefined && first.fd === undefined && first.handle === undefined && first._handle === undefined
               && isPort(first.port)) {
      args[0] = { ...first, host };
    }
    return original.apply(this, args);
  };
}
