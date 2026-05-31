# Kanban worker GitHub authentication

Hermes kanban workers (`zoe-coder`, `zoe-reviewer`, `zoe-planner`) run terminal commands in a **profile-isolated** subprocess environment. Terminal tooling sets `HOME` to `{HERMES_HOME}/profiles/<profile>/home/`, which does **not** contain the operator's `gh` login state under `~/.config/gh/`.

Symptoms when auth is missing:

- `gh auth status` → "You are not logged into any GitHub hosts"
- `git push -u origin HEAD` → exit 128 (HTTPS credential failure)
- Worker handoff: `BLOCKER=Missing GitHub authentication`

The host shell may be fully authenticated (`gh auth status` as the operator user) while workers still fail — that is expected without the bridge below.

## Fix (Hermes spawn)

Kanban dispatch injects the operator config directory into each worker spawn:

```text
XDG_CONFIG_HOME=${HOME}/.config   # gateway operator HOME, not profile HOME
```

Implemented in `~/.hermes/hermes-agent/hermes_cli/kanban_db.py` (`_inject_operator_github_config_env`, called from `_default_spawn`).

After updating Hermes, restart the gateway:

```bash
systemctl --user restart hermes-agent.service
```

## Verify (no live dispatch)

From the Zoe repo:

```bash
bash scripts/maintenance/verify_kanban_github_auth.sh zoe-coder
```

Optional second argument: path to an existing kanban worktree for `git push --dry-run`.

## Operator prerequisites

1. **Host `gh` login** (one-time, operator user):

   ```bash
   gh auth login
   gh auth status
   ```

2. **Do not rely on `GH_TOKEN` in worker terminals.** Hermes intentionally blocks `GH_TOKEN` / `GITHUB_TOKEN` from terminal/execute_code child env (provider credential scrubbing). Use `gh auth login` on the host instead.

3. **Non-standard HOME layouts:** if the gateway runs under a service user whose `HOME` is not where `gh` credentials live, set in `~/.config/systemd/user/hermes-agent.service` (or a drop-in):

   ```ini
   Environment=HERMES_OPERATOR_HOME=/path/to/operator/home
   ```

   Spawn derives `XDG_CONFIG_HOME=${HERMES_OPERATOR_HOME}/.config`. No secrets in unit files required when host `gh auth login` is used.

## Related

- Kanban adapter push/PR instructions: `services/zoe-data/executors/kanban_adapter.py`
- Git credential helper in repo: `credential.helper = !gh auth git-credential` (works once `gh` sees the operator config via `XDG_CONFIG_HOME`)
