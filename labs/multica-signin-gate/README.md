# Lab: the Multica board behind Zoe's sign-in

Proves the nginx `auth_request` gate in front of `/multica/`, `/multica-api/` and `/_next/`
(`docs/knowledge/multica-zoe-signin.md`) against the **real Multica images** the box runs, with the
repo's **real** nginx conf and the **real** zoe-data gate code. Hand-run only; never wired into CI, a
unit or zoe-data.

```
labs/multica-signin-gate/run.sh
```

`run.sh` takes the shared test-lane lock, then refuses unless `MemAvailable` >= 2000 MB and
`~/.zoe/agent-tools/night_busy.sh test` does not say pause. Nothing is pulled.

## Topology (one `--internal` docker network, nothing published)

| container | what | cap |
|---|---|---|
| `pg` | `pgvector/pgvector:0.8.6-pg17`, tmpfs; DB `multica` + DB `zoe` with `auth_users` / `member_modes` (synthetic people) | 256 MB |
| `mbackend` (alias `zoe-multica-backend`) | `multica-backend@sha256:3eab9527...` = the compose pin (v0.3.1); runs its own migrations | 256 MB |
| `mweb` (alias `zoe-multica-web`) | `multica-web@sha256:f05d7572...` = what the live container runs | 256 MB |
| `gate` | `python:3.12-slim-bookworm` + the zoe-data py312 venv (read-only): the REAL `routers/multica_ui.py`, `multica_ui_gate.py`, `path_gate.py`, `auth.get_current_user`, `manner_block.eligible` over the real `db_pool`; a zoe-auth stand-in answers `GET /api/auth/user` in zoe-auth's `UserResponse` shape | 384 MB |
| `nginx` | the worktree's `nginx.conf` + `nginx.d/` + `dist/`; only upstream HOSTS re-pointed at the lab (`host.docker.internal:8000/8080/3000` -> `gate` / the Multica aliases; the container-name form from #2130 needs no rewrite) | 64 MB |
| `nginx-nogate` | NEGATIVE CONTROL: the same conf with the three board `auth_request` lines removed | 64 MB |
| `driver` | stdlib client (`lab/driver.py`) | 128 MB |

Teardown removes every container and the network and fails the run if anything is left.

## Cells

- **A** anonymous: the board page 302s to `/board.html`; `/multica-api/`, `/_next/` and the WebSocket
  handshake get nginx's own 401 (Multica never sees them); Multica's code login (send-code AND
  verify-code with the REAL dev code) is unreachable; `/_zoe/multica-gate` is 404 (internal);
  `/board.html` is served.
- **B** adult member: mints three HttpOnly SameSite=Strict cookies on the three paths; the board page,
  a `/_next/` asset from it, `/multica-api/health` and the WebSocket all reach Multica; Multica's own
  dev-code login works as the SECOND step (`/api/me` returns the email); a header-only caller and the
  household admin pass.
- **C** refused (negative controls of B): a child role, an adult role with a minor `member_modes` row and
  an unknown role get 403; a guest session gets 401; none gets a cookie, the page bounces to the
  launcher, and the code login stays unreachable. An invalid session is 401.
- **D** the cookie: a token signed with the lab's real key passes (control); the wrong secret, another
  purpose's token and an expired one are 401 (the "wrong client secret" cell of this design).
- **E** gate down: a valid cookie and anonymous calls get 5xx; the page is not the board (closed).
- **N** negative control nginx: the same anonymous requests reach Multica, so A measured the gate.

## Not covered here

- The real zoe-auth service (a stand-in serves `/api/auth/user`); the zoe-auth -> zoe-data session
  contract is pinned in zoe-data's own tests.
- A real browser running `board.html` + `auth.js` (the driver mints the cookie with the same request
  the page sends); the page's redirect sanitiser runs in node in `tests/unit/test_multica_ui_gate_nginx.py`.
- A panel device token (covered by `services/zoe-data/tests/test_multica_ui_gate.py`).

## Record

Not yet run: every attempt on 2026-10-19 (02:12 to 05:15) was refused by the `MemAvailable >= 2000 MB`
gate (the box sat at 0.9 to 1.98 GB with the brain and Kokoro resident). Run it in a window with RAM
headroom and record the cell table here.
