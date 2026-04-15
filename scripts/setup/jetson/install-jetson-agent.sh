#!/usr/bin/env bash
# install-jetson-agent.sh — Deploy Pi Agent on Jetson and wire systemd
# Run as: bash scripts/setup/jetson/install-jetson-agent.sh
# Must be run from /home/zoe/assistant on the jetson branch.

set -euo pipefail
ZOE_HOME="/home/zoe"
REPO_DIR="$ZOE_HOME/assistant"
DATA_DIR="$REPO_DIR/services/zoe-data"
LIVE_DIR="$ZOE_HOME/zoe-data"

echo "=== Zoe Jetson Agent Install ==="
echo "Repo: $REPO_DIR"
echo "Live: $LIVE_DIR"
echo ""

# ── 1. Ensure we are on the jetson branch ────────────────────────────────────
cd "$REPO_DIR"
CURRENT=$(git branch --show-current)
if [[ "$CURRENT" != "jetson" ]]; then
  echo "ERROR: Not on jetson branch (current: $CURRENT). Run: git checkout jetson"
  exit 1
fi
echo "✅ On jetson branch"

# ── 2. Symlink /home/zoe/zoe-data → repo services/zoe-data ──────────────────
if [[ -L "$LIVE_DIR" ]]; then
  echo "✅ $LIVE_DIR already a symlink — skipping"
elif [[ -d "$LIVE_DIR" ]]; then
  echo "Backing up $LIVE_DIR → ${LIVE_DIR}.bak …"
  mv "$LIVE_DIR" "${LIVE_DIR}.bak"
  # Preserve .env and data/ from backup
  if [[ -f "${LIVE_DIR}.bak/.env" && ! -f "$DATA_DIR/.env" ]]; then
    cp "${LIVE_DIR}.bak/.env" "$DATA_DIR/.env"
    echo "  Copied .env from backup"
  fi
  if [[ -d "${LIVE_DIR}.bak/data" && ! -d "$DATA_DIR/data" ]]; then
    cp -r "${LIVE_DIR}.bak/data" "$DATA_DIR/data"
    echo "  Copied data/ from backup"
  fi
  ln -s "$DATA_DIR" "$LIVE_DIR"
  echo "✅ Symlinked $LIVE_DIR → $DATA_DIR"
else
  echo "No existing $LIVE_DIR — creating symlink"
  ln -s "$DATA_DIR" "$LIVE_DIR"
  echo "✅ Symlinked $LIVE_DIR → $DATA_DIR"
fi

# ── 3. Install MemPalace + ChromaDB ─────────────────────────────────────────
echo ""
echo "Installing MemPalace + ChromaDB …"
VENV="$DATA_DIR/venv"
if [[ -d "$VENV" ]]; then
  "$VENV/bin/pip" install --quiet mempalace chromadb
else
  pip3 install --quiet mempalace chromadb
fi
mkdir -p "$ZOE_HOME/.mempalace"
echo "✅ MemPalace installed, data dir: $ZOE_HOME/.mempalace"

# ── 4. Update zoe-data.service with Jetson Agent env vars ───────────────────
SERVICE_FILE="$HOME/.config/systemd/user/zoe-data.service"
if [[ ! -f "$SERVICE_FILE" ]]; then
  echo "WARNING: $SERVICE_FILE not found — create it manually or via existing setup scripts"
else
  echo ""
  echo "Patching $SERVICE_FILE with Jetson Agent env vars …"

  # Remove any existing conflicting env declarations then add ours
  TMPFILE=$(mktemp)
  grep -v "HERMES_FAST_PATH\|JETSON_AGENT_MODE\|GEMMA_SERVER_URL\|MEMPALACE_DATA_DIR\|PI_AGENT_LLM_TIMEOUT" "$SERVICE_FILE" > "$TMPFILE" || true

  # Insert new env lines after [Service] section
  awk '/^\[Service\]/{print; print "Environment=HERMES_FAST_PATH=false"; print "Environment=JETSON_AGENT_MODE=true"; print "Environment=GEMMA_SERVER_URL=http://127.0.0.1:11434/v1"; print "Environment=MEMPALACE_DATA_DIR=/home/zoe/.mempalace"; print "Environment=PI_AGENT_LLM_TIMEOUT=30.0"; next}1' "$TMPFILE" > "$SERVICE_FILE"
  rm "$TMPFILE"

  systemctl --user daemon-reload
  echo "✅ zoe-data.service updated with Jetson Agent env vars"
  echo ""
  echo "Restarting zoe-data …"
  systemctl --user restart zoe-data
  sleep 3
  systemctl --user status zoe-data --no-pager -l | head -20
fi

# ── 5. Install weekly self-review timer ─────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMER_DIR="$HOME/.config/systemd/user"
mkdir -p "$TIMER_DIR"

for unit in zoe-self-review.service zoe-self-review.timer; do
    src="$SCRIPT_DIR/$unit"
    dst="$TIMER_DIR/$unit"
    if [[ -f "$src" ]]; then
        cp "$src" "$dst"
        echo "✅ Installed $unit"
    else
        echo "WARNING: $src not found — skipping $unit"
    fi
done

systemctl --user daemon-reload
systemctl --user enable --now zoe-self-review.timer 2>/dev/null && echo "✅ zoe-self-review.timer enabled" || true

echo ""
echo "=== Install complete ==="
echo ""
echo "Verify with:"
echo "  systemctl --user status zoe-data"
echo "  journalctl --user -u zoe-data -f"
echo "  curl -s http://localhost:8000/api/health"
echo "  systemctl --user list-timers"
