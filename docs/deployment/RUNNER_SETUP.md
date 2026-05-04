# Self-hosted GitHub Actions Runner — Jetson Orin Setup

The `deploy.yml` workflow runs on a self-hosted runner installed directly on the Jetson.
This means deployments happen without any inbound network access, public IP, or SSH tunnel —
the runner connects **outbound** to GitHub's job queue.

## One-time installation (run as the `zoe` user on the Jetson)

1. **GitHub repo → Settings → Actions → Runners → New self-hosted runner**
2. Select: **Linux / ARM64**
3. Follow the generated `curl` + `configure` + `run` commands exactly as shown by GitHub
   (the token embedded in the URL expires after 1 hour — register promptly)
4. Install as a user systemd service so it survives reboots:
   ```bash
   ./svc.sh install
   ./svc.sh start
   ```
5. Verify: GitHub repo → Actions → Runners shows the runner as **"Idle"**

## Required: user session bus access

The deploy job restarts `zoe-data` via `systemctl --user`, which needs the D-Bus user session.

**Add to the runner's `.env` file** (in the directory where you installed the runner):
```
XDG_RUNTIME_DIR=/run/user/1000
```
Replace `1000` with the output of `id -u` on the Jetson.

**Enable linger** so the user session bus survives reboots without an active login:
```bash
loginctl enable-linger zoe
```

## GitHub Secrets

Set these in **repo Settings → Secrets and variables → Actions**:

| Secret | Value |
|--------|-------|
| `GH_PAT` | Personal Access Token with `repo` scope. Can reuse the same token from `questionable-decisions`. |

No SSH keys, no `JETSON_HOST`, no tunnel config needed.

## Verifying a deployment

After pushing to `main`, watch the Actions tab. The deploy job will:
1. `git reset --hard origin/main` in `/home/zoe/assistant`
2. `pip3 install --user` the pinned deps
3. `systemctl --user restart zoe-data.service`
4. Wait 6s then hit `GET /api/proactive/schedule` — expects HTTP 401 (auth required = service healthy)

If the health check step passes, deployment succeeded.

## Troubleshooting

**Runner shows as "Offline":** Check `systemctl --user status actions.runner.*.service` on the Jetson.

**`systemctl --user` fails in the job:** Ensure `XDG_RUNTIME_DIR` is set in the runner `.env` and `loginctl enable-linger zoe` has been run.

**`git reset --hard` fails with lock error:** A previous deploy may have crashed mid-run. SSH onto the Jetson and run `git -C /home/zoe/assistant reset --hard origin/main` manually.
