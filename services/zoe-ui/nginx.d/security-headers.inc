# Zoe security headers — the ONE copy. Included at server scope by both server
# blocks in ../nginx.conf AND inside every location that sets its own add_header
# (nginx drops inherited add_header lines the moment a location adds one of its
# own — that is how the old conf grew six hand-copied CSP strings that drifted).
# Keep the CSP in lockstep with SECURITY_HEADERS in
# tools/audit/ensure_nginx_security_headers.py (pinned by
# tests/unit/test_nginx_delivery_layer.py).
#
# CSP notes:
#  - script-src keeps 'unsafe-inline' (every page is inline-script heavy) and
#    has NO 'unsafe-eval' (no eval/new Function in first-party, lib or workbox).
#  - connect-src allows the panel's OWN voice daemon (http://localhost:7777,
#    http://127.0.0.1:8765) — see services/zoe-ui/AGENTS.md.
#  - img-src https: stays broad: album art comes from whichever provider Music
#    Assistant resolves (Google/YouTube hosts today, others tomorrow).
#  - media-src blob: — TTS audio is played from blob: object URLs.
#  - HSTS is emitted only on TLS: $zoe_hsts is "" on :80 (map in nginx.conf),
#    and nginx skips an add_header whose value is empty.
add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; media-src 'self' blob: data:; font-src 'self'; connect-src 'self' ws: wss: http://localhost:7777 http://127.0.0.1:8765; frame-src 'self'; frame-ancestors 'self';" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header X-Content-Type-Options "nosniff" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Permissions-Policy "camera=(), microphone=(self), geolocation=()" always;
add_header Strict-Transport-Security $zoe_hsts always;
