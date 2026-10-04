# Zoe locations — shared by the :80 and :443 server blocks in ../nginx.conf
# (they used to be two hand-copied 260-line bodies and had drifted). Mounted
# at /etc/nginx/zoe/ by docker-compose.yml. Order matters: nginx takes an
# exact match first, then the longest ^~ prefix, then the FIRST matching
# regex, then the longest plain prefix.
#
# CACHING POLICY (the whole thing):
#   documents / manifest / sw.js   no-cache  — always revalidate (ETag → 304)
#   first-party js/css (+lib)      no-cache  — vendored libs are overwritten in
#                                              place on upgrade, so no immutable
#   fonts / icons / images         7 days, stale-while-revalidate 1 day
#   anything proxied               whatever the upstream says
# Every location below that sets Cache-Control re-includes the security
# headers (nginx drops inherited add_header lines otherwise).

error_page 404 /404.html;

# Never serve agent docs, source, or test harnesses from the docroot.
location ~* (^|/)(README[^/]*|[^/]+\.md|[^/]+\.py|test_[^/]*\.js)$ { return 404; }
location ~ /AGENTS\.md$ { return 404; }

location = /sw.js {
    include /etc/nginx/zoe/security-headers.inc;
    add_header Cache-Control "no-cache, must-revalidate" always;
}

location ~* \.(js|css)$ {
    include /etc/nginx/zoe/security-headers.inc;
    add_header Cache-Control "no-cache, must-revalidate" always;
    try_files $uri =404;
}

location ~* \.(html|json|webmanifest)$ {
    include /etc/nginx/zoe/security-headers.inc;
    add_header Cache-Control "no-cache, must-revalidate" always;
    try_files $uri =404;
}

location ~* \.(woff2?|ttf|png|ico|svg|jpe?g|webp|gif)$ {
    include /etc/nginx/zoe/security-headers.inc;
    add_header Cache-Control "public, max-age=604800, stale-while-revalidate=86400" always;
    try_files $uri =404;
}

# Multi-page app: a missing page is a 404, never index.html with a 200
# (which Workbox then cached under the wrong URL for a week).
location / {
    try_files $uri $uri/ =404;
}

location /developer/ {
    alias /usr/share/nginx/html/developer/;
    try_files $uri $uri/ /developer/index.html;
}

location ^~ /_preview/ { return 404; }
location ^~ /js/_archive/ { return 404; }

# ---- proxies. ALL prefixes are ^~ so the .js/.css/.json regexes above can
# never capture a proxied path (that 404'd Music Assistant's own assets).

# A2A agent discovery — exact match takes priority over generic /.well-known/
location = /.well-known/agent.json {
    proxy_pass http://host.docker.internal:8000/.well-known/agent.json;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

# OIDC provider endpoints — served by zoe-auth
location ^~ /.well-known/ {
    proxy_pass http://zoe-auth:8002/.well-known/;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Real-IP $remote_addr;
}
location = /jwks.json {
    proxy_pass http://zoe-auth:8002/jwks.json;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
location ^~ /application/o/ {
    proxy_pass http://zoe-auth:8002/application/o/;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}

location ^~ /api/auth/ {
    proxy_pass http://zoe-auth:8002;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
location ^~ /api/admin/ {
    proxy_pass http://zoe-auth:8002;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
location ^~ /api/touch-panel/ {
    proxy_pass http://zoe-auth:8002;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
location ^~ /api/sso/ {
    proxy_pass http://zoe-auth:8002;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location ^~ /api/ {
    # Streams (SSE chat, NDJSON voice turns) pass through here: never gzip
    # them — the gzip filter buffers output and would hold chunks back.
    gzip off;
    client_max_body_size 25m;
    proxy_pass http://host.docker.internal:8000/api/;
    # Defense-in-depth: strip client-supplied internal-trust headers at the edge.
    # These must only ever come from trusted internal callers, never a browser —
    # resolve_acting_user honors X-Zoe-User-Id, so a forwarded one = impersonation.
    proxy_set_header X-Internal-Token "";
    proxy_set_header X-Zoe-User-Id "";
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_buffering off;
    proxy_request_buffering off;
    proxy_read_timeout 900s;
    proxy_send_timeout 900s;
    proxy_connect_timeout 60s;
}

location = /health {
    proxy_pass http://host.docker.internal:8000/health;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location ^~ /ws/ {
    gzip off;
    proxy_pass http://host.docker.internal:8000/ws/;
    # Defense-in-depth: strip client-supplied internal-trust headers (see /api/).
    proxy_set_header X-Internal-Token "";
    proxy_set_header X-Zoe-User-Id "";
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_read_timeout 900s;
    proxy_send_timeout 900s;
}

# LiveKit WebRTC signalling proxy (port 7880)
location ^~ /livekit/ {
    gzip off;
    proxy_pass http://host.docker.internal:7880/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_read_timeout 900s;
    proxy_send_timeout 900s;
}

# Home Assistant proxy — serves HA over HTTPS so the kiosk can embed dashboards in iframes without mixed-content errors
# NOTE: Requires HA to trust the nginx container IP (e.g. 172.18.0.0/16) in configuration.yaml:
#   http:
#     use_x_forwarded_for: true
#     trusted_proxies: ["172.18.0.0/16", "172.17.0.0/16"]
location ^~ /ha/ {
    rewrite ^/ha/(.*)$ /$1 break;
    proxy_pass http://host.docker.internal:8123;
    proxy_http_version 1.1;
    proxy_set_header Host localhost:8123;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    proxy_buffering off;
}

# SECURITY: proxies straight to the Music Assistant host service (its own
# native web UI + WebSocket). A Zoe auth_request gate is NOT applied here
# because the only legitimate browser callers are music.html's
# `new WebSocket('/modules/music-assistant/ws')` and a `window.open(...)`
# popup of the MA UI — and Zoe's auth is a header/localStorage bearer token
# the browser cannot attach to either a WebSocket handshake or window.open,
# so a gate would break live music setup/status. MA enforces its own
# onboarding/auth; treat this route as local-trusted. Proper fix (tracked):
# move to a cookie-issued session or relay the WS behind /api/music/. Do not
# add unauthenticated state-changing surface beyond MA's own UI here.
location ^~ /modules/music-assistant/ {
    proxy_pass        http://host.docker.internal:8095/;
    proxy_http_version 1.1;
    proxy_set_header   Host $host;
    proxy_set_header   Upgrade $http_upgrade;
    proxy_set_header   Connection $connection_upgrade;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}

# Requires zoe-ui extra_hosts host.docker.internal:host-gateway (see docker-compose.yml).
location ^~ /multica-api/ {
    gzip off;
    proxy_pass http://host.docker.internal:8080/;
    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}

# Route /_next/ asset requests (JS/CSS chunks) to multica-web.
# Next.js embeds root-relative /_next/ paths in its HTML when no basePath is configured.
location ^~ /_next/ {
    proxy_pass        http://host.docker.internal:3000/_next/;
    proxy_http_version 1.1;
    proxy_set_header   Host        $host;
}

location ^~ /multica/ {
    proxy_pass        http://host.docker.internal:3000/;
    proxy_http_version 1.1;
    proxy_set_header   Upgrade     $http_upgrade;
    proxy_set_header   Connection  $connection_upgrade;
    proxy_set_header   Host        $host;
    # Rewrite server-side redirects so /login → /multica/login (not Zoe's login)
    proxy_redirect     /login      /multica/login;
    proxy_redirect     /register   /multica/register;
}
