# Zoe Operator Runbook

**Last updated:** 2026-04-04  
**Platform:** Jetson Orin (Zoe server) + Raspberry Pi (touch panel)

---

## Service Start/Stop Order

Services must be started in dependency order:

```
1. docker compose up -d zoe-database      # PostgreSQL + pgvector (port 5432)
2. docker compose up -d zoe-auth          # Authentication (port 8002)
3. systemctl --user start llama-server    # Local LLM (port 11434)
4. systemctl --user start hermes-agent     # Hermes agent gateway (port 8642)
5. systemctl --user start openclaw-gateway # OpenClaw gateway (port 18789)
6. systemctl --user start kokoro-tts       # TTS sidecar (port 10201)
7. systemctl --user start zoe-data         # Data backend (port 8000)
8. docker compose up -d homeassistant homeassistant-mcp-bridge
9. docker compose up -d zoe-ui             # Nginx + UI (port 80/443)
```

**Stop order** (reverse):
```
docker compose stop zoe-ui homeassistant-mcp-bridge homeassistant
systemctl --user stop zoe-data kokoro-tts openclaw-gateway hermes-agent llama-server
docker compose stop zoe-auth zoe-database
```

**Single restart** (most common):
```bash
systemctl --user restart zoe-data         # After Python code changes
docker compose restart zoe-ui             # After nginx.conf or HTML changes
systemctl --user restart openclaw-gateway # After openclaw.json changes
```

---

## Environment Variables (Key Settings)

| Variable | Service | Default | Notes |
|---|---|---|---|
| `ZOE_UNAUTHENTICATED_ROLE` | zoe-data | (admin) | Set to `guest` for kiosk/public endpoints |
| `OPENCLAW_AGENT_TIMEOUT_S` | zoe-data | 120 | Seconds before OpenClaw CLI times out |
| `HA_ACCESS_TOKEN` | .env | — | Home Assistant long-lived token (named `zoe-data`) |
| `ZOE_WAKE_ACK_PHRASE` | zoe-data + Pi daemon | — | Instant websocket wake transcript and optional TTS phrase after wake word |
| `ZOE_WAKE_ACK_AUDIO_PATH` | zoe-data + Pi daemon | — | Pre-generated wake ack audio file served from cache before live TTS |
| `ZOE_WAKE_ACK_PHRASES` / `ZOE_WAKE_ACK_AUDIO_PATHS` | zoe-data + Pi daemon | — | Pipe-separated cached wake response bank; phrase/audio entries are index-aligned. Quote pipe-separated values in `.env`, e.g. `ZOE_WAKE_ACK_PHRASES="Yes Jason.|Hi Jason."` |
| `ZOE_WAKE_ACK_VARIANT_LABELS` | zoe-data + Pi daemon | — | Optional pipe-aligned labels (`default`, `morning`, `afternoon`, `evening`, `night`) for deterministic time-aware wake acks; quote when using pipes, e.g. `"default|morning|evening"` |
| `ZOE_PROCESSING_ACK_PHRASES` | zoe-data | `"Let me check.|One moment.|I will check that."` | Pipe-separated transition phrases for slow voice turns; quote in `.env` so maintenance probes can shell-source the file safely |
| `ZOE_DEVICE_TOKEN_SECRET` | zoe-data | — | Secret for device token HMAC (optional extra layer) |
| `ZOE_PIN_MAX_ATTEMPTS` | zoe-data | 5 | PIN lockout attempts |
| `ZOE_PIN_CHALLENGE_TTL_S` | zoe-data | 120 | PIN challenge expiry in seconds |
| `ZOE_CHAT_URL` | Pi daemon | `http://localhost:8000` | Base URL for voice daemon to reach zoe-data |
| `VERIFY_SSL` | Pi daemon | `true` | Set `false` on Pi if using self-signed Jetson cert |

Full env matrix lives in `services/zoe-data/.env.example` and `assistant/.env`.

---

## "What to restart after X"

| Change | Restart |
|---|---|
| Edit any `.py` file in `services/zoe-data/` | `systemctl --user restart zoe-data` |
| Edit `nginx.conf` | `docker compose restart zoe-ui` |
| Edit `dist/**/*.html` or `dist/**/*.js` | `docker compose restart zoe-ui` |
| Edit `openclaw.json` or skills | `systemctl --user restart openclaw-gateway` |
| Edit `homeassistant/configuration.yaml` | `docker restart homeassistant` |
| Edit `assistant/.env` (HA_ACCESS_TOKEN etc.) | `docker compose restart homeassistant-mcp-bridge` and `systemctl --user restart zoe-data` |
| Update llama-server model | `systemctl --user restart llama-server` |
| Issue/revoke device token | Service auto-loads at startup; restart `zoe-data` to pick up changes |
| Edit Pi voice daemon `.env.voice` | `systemctl --user restart zoe-voice` (on Pi) |
| Edit `scripts/setup/zoe_voice_daemon.py` or `zoe_voice_announce.py` | `scp` **both** files to `zoe-pi:~/.zoe-voice/` then `ssh zoe-pi systemctl --user restart zoe-voice` (they deploy as a pair — the daemon disables announce polling with a WARNING if the companion module is missing) |

---

## Issuing a Panel Device Token

1. Ensure the panel is registered:
   ```bash
   curl -s -X POST https://zoe.local/api/panels/register \
     -H "X-Session-ID: <admin-session>" \
     -H "Content-Type: application/json" \
     -d '{"panel_id":"zoe-touch-pi","name":"Living Room Panel","location":"Living Room"}'
   ```

2. Issue the token:
   ```bash
   curl -s -X POST https://zoe.local/api/panels/zoe-touch-pi/token \
     -H "X-Session-ID: <admin-session>" \
     -H "Content-Type: application/json" \
     -d '{"name":"voice-daemon","role":"voice-daemon","scopes":["voice"]}'
   ```
   The response includes `"token": "..."` — **copy it now, it is never shown again**.

3. On the Pi, set `DEVICE_TOKEN=<token>` in `~/.zoe-voice/.env.voice` and restart:
   ```bash
   systemctl --user restart zoe-voice
   ```

4. To revoke:
   ```bash
   curl -s -X DELETE https://zoe.local/api/panels/zoe-touch-pi/token/<token_id> \
     -H "X-Session-ID: <admin-session>"
   ```

---

## Troubleshooting

### OpenClaw returns generic answer instead of doing the task
- Check `services/zoe-data/intent_router.py` — the `HA_FULL_SETUP_OPENCLAW_MESSAGE` should expand the phrase.
- Check `~/.openclaw/workspace/skills/home-assistant/SKILL.md` for the "Shorthand = full bootstrap" section.
- Run: `systemctl --user restart openclaw-gateway`

### OpenClaw 401 / token error
```bash
systemctl --user status openclaw-gateway
journalctl --user -u openclaw-gateway --since "10 min ago"
# Check token in ~/.openclaw/openclaw.json → api.token
```

### HA returns 400 from nginx `/ha/` proxy
- Check `homeassistant/configuration.yaml` has `trusted_proxies` block.
- Run: `docker restart homeassistant`
- Test: `curl -I https://zoe.local/ha/` — expect 302 or 200 (not 400/403).

### HA bridge returns 0 entities
```bash
docker logs homeassistant-mcp-bridge --tail 30
# Token likely expired or wrong. Regenerate via browser:
# 1. Open http://localhost:8123/profile/security
# 2. Delete old 'zoe-data' token, create new one
# 3. sed -i "s|^HA_ACCESS_TOKEN=.*|HA_ACCESS_TOKEN=NEW_TOKEN|" assistant/.env
# 4. docker restart homeassistant-mcp-bridge
```

### Pi voice daemon not waking
```bash
# On the Pi:
journalctl --user -u zoe-voice -f
# Check DEVICE_TOKEN is set in ~/.zoe-voice/.env.voice
# Check AUDIO_DEVICE with: arecord -l
# Test mic: arecord -d 3 -r 16000 -c 1 /tmp/test.wav && aplay /tmp/test.wav
```

### Voice STT returns 503 (`/api/voice/transcribe`)
Jetson must run **whisper.cpp** with paths set on **zoe-data** (host or container env):
```bash
# Example (adjust paths):
export ZOE_WHISPER_CPP_BIN=/home/zoe/whisper.cpp/build/bin/whisper-cli
export ZOE_WHISPER_MODEL=/path/to/ggml-base.en.bin
systemctl --user restart zoe-data
```
The Pi daemon POSTs base64 WAV with header **`X-Device-Token`**. After issuing a new device token, restart **zoe-data** so the in-memory token cache reloads (or rely on cache update from the issue-token API).

### zoe-data won't start (import error)
```bash
systemctl --user status zoe-data
journalctl --user -u zoe-data --since "10 min ago"
# Common: missing pip dependency → install/refresh host Python deps, then restart:
pip3 install --user -r services/zoe-data/requirements.txt
systemctl --user restart zoe-data
```

### Chat stays on "Zoe is typing…" indefinitely
- OpenClaw timeout: check `OPENCLAW_AGENT_TIMEOUT_S` (default 120s).
- Check: `journalctl --user -u openclaw-gateway --since "5 min ago"`
- Nginx timeout: check `proxy_read_timeout` in `nginx.conf` (should be ≥ 130s for `/api/` routes).

### llama-server not responding
```bash
systemctl --user status llama-server
journalctl --user -u llama-server --since "10 min ago"
# Check GPU memory: nvidia-smi  (llama-server needs ~8 GB VRAM)
systemctl --user restart llama-server
```

---

## Backup / Rollback

### Before HA config edits (automated by OpenClaw)
```bash
cp assistant/homeassistant/configuration.yaml \
   assistant/homeassistant/configuration.yaml.bak.$(date +%Y%m%d)
```

### Before `.env` token changes
```bash
cp assistant/.env assistant/.env.bak.$(date +%Y%m%d)
```

### Rollback a bad `.env` change
```bash
cp assistant/.env.bak.YYYYMMDD assistant/.env
docker compose restart homeassistant-mcp-bridge
systemctl --user restart zoe-data
```

### Database backup
```bash
scripts/maintenance/postgres-nightly-backup.sh
```

### Restore from backup
```bash
# Stop writers first
systemctl --user stop zoe-data
# Example restore, adjust dump path and database name.
gunzip -c ~/.zoe-backups/postgres/zoe-YYYYMMDD-HHMMSS.dump.gz \
  | docker exec -i zoe-database pg_restore -U zoe -d zoe --clean --if-exists
systemctl --user start zoe-data
```

---

## Health Check Endpoints

| Service | URL | Expect |
|---|---|---|
| zoe-data | `http://localhost:8000/health` | `{"status":"ok"}` |
| zoe-auth | `http://localhost:8002/health` | `{"status":"ok"}` |
| HA bridge | `http://localhost:8007/entities` | `{"count": N}` (N > 0) |
| HA | `http://localhost:8123` | HA login page |
| llama-server | `http://localhost:11434/health` | `{"status":"ok"}` |
| Hermes | `http://localhost:8642/health` | service-specific health response |
| OpenClaw | `http://localhost:18789/health` | `{"ok":true}` |
| nginx (HTTP) | `http://localhost/health` | 200 |
| nginx (HTTPS) | `https://zoe.local/health` | 200 |

Quick health check script:
```bash
for url in \
  "http://localhost:8000/health" \
  "http://localhost:8002/health" \
  "http://localhost:8007/entities" \
  "http://localhost:11434/health" \
  "http://localhost:18789/health"; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$url")
  echo "$STATUS  $url"
done
```

---

## Production Checklist (per release)

- [ ] Run `python3 -m pytest services/zoe-data/tests` from repo root — all pass
- [ ] `python3 tools/audit/validate_structure.py` — clean
- [ ] `python3 tools/audit/validate_critical_files.py` — clean
- [ ] Manual smoke: send one chat message → streaming response visible
- [ ] Manual smoke: say "setup home assistant" → OpenClaw opens browser
- [ ] HA proxy test: `curl -I https://zoe.local/ha/` → not 400/403
- [ ] Panel test (if hardware present): tap orb → voice command → TTS reply
- [ ] Check nginx timeouts ≥ `OPENCLAW_AGENT_TIMEOUT_S + 10s`
- [ ] No secrets in git: `git diff --name-only | grep -E "\.env$|token|secret"`

---

## Network / Firewall Notes

| Port | Service | Who connects |
|---|---|---|
| 8000 | zoe-data | zoe-ui nginx, OpenClaw, Pi daemon |
| 8002 | zoe-auth | zoe-data |
| 8007 | homeassistant-mcp-bridge | zoe-data, OpenClaw |
| 8123 | Home Assistant | nginx (proxy to /ha/), OpenClaw browser |
| 8642 | Hermes agent | zoe-data, background agent workflows |
| 11434 | llama-server | zoe-data, Hermes |
| 18789 | openclaw-gateway | zoe-data explicit/manual fallback |
| 80/443 | nginx | browser, Pi kiosk |

Pi → Jetson: needs HTTPS (443) for `/api/voice/*` and wss for WebSocket.  
If using self-signed cert on Jetson, set `VERIFY_SSL=false` in Pi `.env.voice` and add the cert to Pi trust store.

---

## Privacy Notes

- Camera/mic indicator: ensure the touch panel shows a visual indicator when mic is active.
- Wake word model runs locally on Pi — audio never leaves the device until command intent is confirmed.
- After wake word fires, WAV audio is sent to Jetson for STT — kept in memory only, not stored.
- Presence events (`panel_presence_events` table) are retained for 30 days by default (no automated purge yet — add cron if required).
- Face encodings (if vision implemented): delete on user request via admin panel or `DELETE /api/panels/{id}/presence`.
