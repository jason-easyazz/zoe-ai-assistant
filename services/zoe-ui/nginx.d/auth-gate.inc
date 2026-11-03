# Included by every location that proxies to zoe-auth (:8002). zoe-auth authenticates PEOPLE; it knows nothing about client
# certificates, so on the relay listener a revoked or unbound household-CA certificate could still reach login, touch-panel and
# guest-session routes (and brute-force a passcode) from the internet. This asks zoe-data first: on the relay listener
# /__zoe_auth_gate proxies to GET /api/relay/check, which only answers 204 after relay_ingress proved the certificate
# binding AND a device credential for the same panel; anything else is 401 here). On the LAN blocks the same location is an
# internal always-204 that never leaves nginx (auth_request's URI is NOT variable-expanded, so each server defines its own), so the LAN path to zoe-auth is unchanged.
auth_request /__zoe_auth_gate;
