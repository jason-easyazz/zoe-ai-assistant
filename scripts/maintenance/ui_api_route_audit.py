#!/usr/bin/env python3
"""UI → API route audit: every `/api/...` path literal in the desktop UI must be served.

Reads the LIVE route tables (zoe-data + zoe-auth OpenAPI) and lists every literal in
services/zoe-ui/dist (html + js, excluding vendored lib/, the retired touch/ pages and
harnesses) that no route matches. A literal that is a prefix of a parametrised route
(`/api/lists` + `/${id}` built later) counts as served. Found 2026-10-09: ten dead paths
(an HA write route that never existed behind every music transport button, a photo upload
with no backend, panel Restart/Logs posting to a non-existent tools endpoint, …).

  python3 scripts/maintenance/ui_api_route_audit.py            # live box defaults
  python3 scripts/maintenance/ui_api_route_audit.py --data http://127.0.0.1:8000 --auth http://127.0.0.1:8002
Exit 0 = every literal served (allowlisted gaps are reported, not failed); 1 = dead paths.
"""
import argparse, glob, json, os, re, sys, urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DIST = os.path.join(ROOT, "services", "zoe-ui", "dist")

# Known gaps: UI that still points at a backend that was never built. Each entry is a
# decision recorded in docs/knowledge/ui-deep-review-2026-10-04.md §11 — remove the line
# when the backend lands or the UI is retired.
ALLOWLIST = {
    "/api/calendar/week": "dashboard week-planner widget expects a finance week payload nobody serves (not instantiated by default)",
    "/api/chat/confirm": "voice.html AG-UI approval decision has no decide route (approvals are read-only)",
    "/api/tiles": "memories.html tile layout save; /api/collections is a stub router",
}

ap = argparse.ArgumentParser()
ap.add_argument("--data", default="http://127.0.0.1:8000")
ap.add_argument("--auth", default="http://127.0.0.1:8002")
args = ap.parse_args()

def openapi_paths(base):
    with urllib.request.urlopen(f"{base}/openapi.json", timeout=10) as r:
        return sorted(json.load(r)["paths"].keys())

routes = openapi_paths(args.data) + openapi_paths(args.auth)
matchers = [re.compile("^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(r)) + "/?$") for r in routes]

def served(u):
    if any(m.match(u) for m in matchers):
        return True
    return any(r.startswith(u + "/") or r.startswith(u + "{") for r in routes)

files = [f for f in glob.glob(DIST + "/**/*.html", recursive=True) + glob.glob(DIST + "/**/*.js", recursive=True)
         if "/lib/" not in f and "/touch/" not in f and "/test_" not in f and not f.endswith("sw.js")]
calls = {}
for f in files:
    for m in re.finditer(r"""['"`](/api/[A-Za-z0-9_\-./]+)""", open(f, encoding="utf-8", errors="replace").read()):
        calls.setdefault(m.group(1).rstrip("/"), set()).add(os.path.relpath(f, DIST))

dead = {u: sorted(fs) for u, fs in calls.items() if not served(u)}
allowed = {u: v for u, v in dead.items() if u in ALLOWLIST}
failing = {u: v for u, v in dead.items() if u not in ALLOWLIST}
print(f"routes: {len(routes)} live | UI literals: {len(calls)} | unserved: {len(dead)} (allowlisted {len(allowed)})")
for u, fs in sorted(allowed.items()):
    print(f"  gap  {u}  <- {', '.join(fs)}  [{ALLOWLIST[u]}]")
for u, fs in sorted(failing.items()):
    print(f"  DEAD {u}  <- {', '.join(fs)}")
print("RESULT:", "PASS" if not failing else f"FAIL ({len(failing)} dead paths)")
sys.exit(0 if not failing else 1)
