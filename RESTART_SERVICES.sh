#!/bin/bash
# Restart the active Zoe runtime.

set -euo pipefail

ROOT_DIR="/home/zoe/assistant"
cd "$ROOT_DIR"

echo "Restarting Zoe containers..."
docker compose up -d

echo "Restarting zoe-data user service..."
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
systemctl --user restart zoe-data.service

echo "Checking optional host-native agent services..."
for service in openclaw.service llama-server.service hermes.service kokoro-tts.service; do
  if systemctl --user list-unit-files "$service" --no-legend 2>/dev/null | grep -q "$service"; then
    systemctl --user restart "$service" || echo "Warning: could not restart $service"
  else
    echo "Skipping $service (not installed)"
  fi
done

echo "Waiting for services to initialize..."
sleep 6

echo "Validating runtime..."
bash "$ROOT_DIR/tools/docker/validate_networks.sh"
curl -sf http://localhost:8000/health >/dev/null
curl -sf http://localhost:8002/health >/dev/null

echo "Restart complete."
echo "Next steps:"
echo "  1. Check zoe-data logs: journalctl --user -u zoe-data.service -n 100 --no-pager"
echo "  2. Check container logs: docker compose logs --tail=100 zoe-auth zoe-ui zoe-database"
echo "  3. Run tests: PYTHONPATH=services/zoe-data python3 -m pytest services/zoe-data/tests -q"
