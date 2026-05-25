#!/bin/bash
# Restart the active Zoe runtime.

set -euo pipefail

APP_DIR="${APP_DIR:-/home/zoe/assistant}"
cd "$APP_DIR"

echo "Restarting Docker-managed services..."
docker compose up -d zoe-database zoe-auth zoe-ui homeassistant homeassistant-mcp-bridge

echo "Restarting host-native zoe-data..."
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
systemctl --user restart zoe-data.service

for service in openclaw.service llama-server.service hermes.service kokoro-tts.service; do
  if systemctl --user list-unit-files "$service" >/dev/null 2>&1; then
    echo "Restarting optional $service..."
    systemctl --user restart "$service" || true
  fi
done

echo "Waiting for services..."
sleep 6

curl -sf http://localhost:8000/health >/dev/null
curl -sf http://localhost:8002/health >/dev/null
docker compose ps

echo "Restart complete."
