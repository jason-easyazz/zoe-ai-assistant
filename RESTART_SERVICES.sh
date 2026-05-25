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

wait_for_url() {
  local url="$1"
  curl -sf --retry 12 --retry-delay 5 --retry-connrefused --max-time 5 "$url" >/dev/null
}

echo "Waiting for services..."
wait_for_url http://localhost:8000/health
wait_for_url http://localhost:8002/health
docker compose ps

echo "Restart complete."
