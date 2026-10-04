---
type: Reference
title: Dependabot alerts — disposition of 2026-10-04 (critical first, rest batched)
description: Every open Dependabot alert as of 2026-10-04 with its disposition (fixed / dismissed / accepted) and the evidence behind it — PyJWT is pinned but never imported by zoe-auth (python-jose carries every token), the chromadb advisories are all HTTP-server surface we never run, brace-expansion is pinned past the fix via an npm override, torch stays on the Kokoro rock. Includes the operator apply step for the live box and what was not verifiable from a worktree.
tags: [dependabot, security, pyjwt, chromadb, brace-expansion, torch, operator]
timestamp: 2026-10-04T00:00:00Z
---

# Dependabot alerts — disposition of 2026-10-04

Owner decision Q29: "Review the four critical ones first, batch the rest." This note is the
batch. One draft PR (`chore/dependabot-critical-2026-10-04`) carries the pin changes; the
dismissals were applied through the API with the comment pointing here.

Alert list source: `gh api repos/:owner/:repo/dependabot/alerts?state=open`.

## The table

| # | Sev | Package | Manifest | Disposition |
|---|---|---|---|---|
| 70 | critical | PyJWT | zoe-auth/requirements.txt | **fixed** — pin 2.13.0 → 2.15.0 |
| 73, 69, 68, 67, 66 | high | PyJWT | zoe-auth | **fixed** — same bump |
| 76, 75, 74, 72, 71, 65, 64 | medium | PyJWT | zoe-auth | **fixed** — same bump (#75 is why it is 2.15.0, not 2.14.0; #76 has no fix version but its range ends at 2.13.0) |
| 60 | critical | chromadb | zoe-data/requirements-py312.txt | **dismissed (not_used)** — pre-auth RCE via `/api/v2/.../collections` HTTP endpoint |
| 59, 55 | critical | chromadb | both zoe-data manifests | **dismissed (not_used)** — authenticated RCE via the same HTTP endpoint (needs UPDATE_COLLECTION permission) |
| 58, 54 | high | chromadb | both | **dismissed (not_used)** — `SimpleRBACAuthorizationProvider` ignores tenant/db/collection scope |
| 57, 53 | high | chromadb | both | **dismissed (not_used)** — authenticated cross-tenant read/write via the server |
| 78, 77, 79 | high/high/medium | brace-expansion | zoe-core/package-lock.json | **fixed** — lockfile now resolves 5.0.12 via a scoped `overrides` entry |
| 61, 62, 63 | medium/low/low | torch | scripts/setup/requirements-kokoro.txt | **accepted** — Kokoro's torch is a pinned CUDA/Tegra rock; not bumpable |

## PyJWT (#64–#76) — fixed, but note what actually carries the tokens

The thing to know before anyone treats this as a hot patch: **zoe-auth never imports PyJWT.**
Every JWT in the service goes through python-jose — `oidc/tokens.py` (`from jose import jwt`,
`jwk`) and `oidc/keys.py` (`from jose import jwk`). A repo-wide grep for `import jwt` /
`from jwt` inside `services/zoe-auth` returns nothing; the only `jwt` word outside the jose
lines is a docstring. PyJWT sits in `requirements.txt` as a pinned-but-unused distribution,
so none of the fourteen advisories (HMAC/PEM confusion guards, `PyJWKClient` redirects and
JWKS amplification, the `options`-dict mutation in #76, the `RecursionError` escapes) has a
code path in zoe-auth.

The pin is bumped anyway because the image should not ship a known-vulnerable wheel, and
because the repo's other PyJWT pin (zoe-data, both manifests) is already `2.15.0` — one
PyJWT version repo-wide is the class fix, not a per-service drift. Why 2.15.0 and not the
2.14.0 Dependabot names for #70: #75 (GHSA-42vr-xj54-vc7v) is `<= 2.14.0`, fixed in 2.15.0.
(2.15.1 exists upstream — a single fix for trailing `=` padding — but matching zoe-data's
pin wins over newest.)

Changelog review (CHANGELOG.rst, 2.14.0 and 2.15.0): every entry is a hardening of
`PyJWK`/`PyJWKClient`/`decode` error types; no removed API, no minimum-Python change
(3.15 support added). Nothing a jose-only service could observe.

Tests: the full zoe-auth suite is what CI runs (`validate.yml` installs
`services/zoe-auth/requirements.txt` and runs `services/zoe-auth/tests` whole). In this
worktree, 4 of the 15 modules fail collection on `No module named 'sqlite_compat'` — that
module lives in zoe-data and CI puts it on `PYTHONPATH`; it is unrelated to this change.
The one JWT module, `tests/test_oidc_jwt.py`, runs locally: **14 passed**. There is no
`ci_safe` marker in zoe-auth (the marker is a zoe-data / `tests/unit` convention).

## chromadb (#53–#60) — dismissed: server surface we do not run

All seven advisories describe the **chroma HTTP server** (the FastAPI app behind
`chroma run` / `HttpClient`):

- #60 — "pre-authentication code injection ... by sending a malicious model repository and
  `trust_remote_code` set to true in the `/api/v2/tenants/{tenant}/databases/{db}/collections`
  endpoint." A network caller.
- #59 / #55 — the same injection through the collection-update endpoint, requiring an
  authenticated user with `UPDATE_COLLECTION`.
- #57 / #53 — "any authenticated users" reaching other tenants' collections through the API.
- #58 / #54 — `SimpleRBACAuthorizationProvider` not scoping permissions. That provider only
  exists in the server's auth stack.

What Zoe runs: `services/zoe-data/memory_service.py:100` opens the palace with
`chromadb.PersistentClient(path=...)` — embedded, in-process, SQLite + HNSW on disk. Every
other chroma caller in the tree (`scripts/lib/palace_client.py`, the maintenance scripts)
is also `PersistentClient`. Grep over `*.py *.yml *.sh *.service Dockerfile*` for
`chroma run`, `chromadb.server`, `HttpClient(`, `AsyncHttpClient`, `CHROMA_SERVER*`,
`CHROMA_CLIENT_AUTH*`, `ghcr.io/chroma` returns **nothing** outside tests. There is no
tenant, no auth provider, no listening socket — the "attacker" in these advisories would
have to be zoe-data itself, and the embedding function it configures is the local
`ONNXMiniLM_L6_V2`, not a remote model repository.

Why dismiss rather than bump: `first_patched_version` is `null` on every one of these, 1.5.9
is the newest release, and the on-disk palace format is one-way pinned to the 1.5.x line
(`requirements-py312.txt` comment; migration runbook `chroma-1-5-migration.md`). The legacy
`requirements.txt` manifest (0.6.3, py3.10) is the same embedded usage. Chroma usage itself
was **not** touched — 1.5.9's Rust client has a known `count()` wedge and this PR is not the
place to go near it.

If a future change ever stands up the chroma server (e.g. to share the palace across
processes), these dismissals must be revisited — reopen them from the alert page.

## brace-expansion (#77–#79) — fixed via a scoped override

`services/zoe-core/package-lock.json` carried `brace-expansion@5.0.9` nested under
`@earendil-works/pi-coding-agent → minimatch@10.2.6`. Nothing in `package.json` depends on
it directly, so `npm update brace-expansion --package-lock-only` reported "up to date" and
left the nested entry alone. The fix is an `overrides` entry scoped to the major that is
actually in the tree:

```json
"overrides": { "brace-expansion@^5": "^5.0.12" }
```

The `@^5` scope keeps the override from ever dragging a 1.x/2.x `brace-expansion` (which
other future deps may bring in) onto 5.x. With the stale entry removed and
`npm install --package-lock-only --ignore-scripts` re-run, npm resolved and hoisted
`brace-expansion@5.0.12` (+ its `balanced-match@4.0.4`, same version as before, now at
top level). Verified:

- `npm ls brace-expansion --package-lock-only` → `brace-expansion@5.0.12 overridden`, no
  `invalid`;
- `npm ci --ignore-scripts --dry-run` → "add 4 packages", no lock/package.json mismatch.

`npm ci` itself was **not** run on this box, and by design never is: `services/AGENTS.md`
documents that the zoe-core extensions run via Node's built-in type stripping with "no
build step and no `node_modules`", and `services/zoe-core/` is in no GitHub workflow. So
the lockfile is proven resolvable and in sync, not proven installable; the first `npm ci`
on any host that does install it will be the installability proof (nothing today consumes
the vulnerable `minimatch → brace-expansion` chain at runtime).

## torch (#61–#63) — accepted

`scripts/setup/requirements-kokoro.txt` is a **drift manifest**, not an install list
(its header: "NOTHING IS INSTALLED FROM THIS FILE"). The Kokoro venv is
`--system-site-packages` over the NVIDIA CUDA torch 2.8.0 wheel in `~/.local` (1.8 GB,
no recorded source, not reproducible offline) — see `build_kokoro_venv.sh` and
`voice-pipeline.md`. The three advisories (`unpack_sequence`, `torch.lstm_cell`,
`torch.jit.script` memory corruption) all need attacker-supplied model code or
tensors reaching those entry points; the sidecar runs the pinned Kokoro model from local
files behind a loopback HTTP API and never loads foreign models. Fixed versions (2.9.1 /
2.10.0 / 2.13.0) do not exist as Tegra CUDA wheels for this JetPack, and the voice stack
("fixed models are rocks") is optimised around this exact build. Accepted; revisit only when
the Jetson torch wheel moves for its own reasons.

## Operator apply step (live box)

**zoe-auth (PyJWT)** — a Docker service, not a venv (`docker-compose.yml` → `build:
./services/zoe-auth`, `Dockerfile` does `pip install -r requirements.txt`). The CD lane does
this on merge: `deploy.yml` sees `services/zoe-auth/` changed since the last deployed SHA
and runs `docker compose up -d --no-deps --build zoe-auth` behind its 350 MB
free-memory gate, then polls `http://localhost:8002/health`. If the gate refuses (box too
tight), the manual equivalent from the live checkout after the ff-only sync is:

```sh
cd /home/zoe/assistant
docker compose up -d --no-deps --build zoe-auth
for i in $(seq 1 12); do curl -sf http://localhost:8002/health && break; sleep 5; done
docker exec zoe-auth python -c "import importlib.metadata as m; print(m.version('pyjwt'))"  # expect 2.15.0
```

Keep `--no-deps` — without it compose also reconciles `zoe-database` (pinned by
`tests/unit/test_compose_loopback_binds.py`).

**zoe-core (brace-expansion)** — no runtime step; `services/zoe-core` deliberately has no
`node_modules` on the box (see above). Any future `npm ci` picks up 5.0.12.

**chromadb / torch** — nothing to apply.

## Not verifiable from the worktree

- The running container's installed PyJWT version (docker reads are outside this agent's
  lane). The Dockerfile pins it from `requirements.txt`, so a rebuild is the proof.
- `npm ci` for zoe-core (see above): resolvable + in sync, not installed.
- The 4 zoe-auth test modules that need zoe-data's `sqlite_compat` on `PYTHONPATH` — CI's
  lane runs them; they do not touch JWTs.
