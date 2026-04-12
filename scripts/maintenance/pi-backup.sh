#!/usr/bin/env bash
# =============================================================================
# pi-backup.sh — Daily backup of Zoe Pi data to Jetson
# Add to crontab: 0 3 * * * bash ~/assistant/scripts/maintenance/pi-backup.sh
# =============================================================================
set -uo pipefail

JETSON_HOST="${BACKUP_JETSON_HOST:-zoe@192.168.1.58}"
REPO_DIR="${REPO_DIR:-$HOME/assistant}"
REMOTE_DIR="${BACKUP_REMOTE_DIR:-/home/zoe/backups/pi}"
LOG_FILE="${BACKUP_LOG:-$HOME/backup.log}"
MAX_BACKUPS="${BACKUP_KEEP_DAYS:-14}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

log "=== Pi backup starting ==="

DATE_TAG=$(date +%Y%m%d-%H%M)
REMOTE_DATED="$REMOTE_DIR/$DATE_TAG"

# 1. zoe.db
log "Backing up zoe.db..."
rsync -az --timeout=30 \
    "$REPO_DIR/data/zoe.db" \
    "$JETSON_HOST:$REMOTE_DATED/" 2>>"$LOG_FILE" && log "zoe.db OK" || log "[WARN] zoe.db backup failed"

# 2. MemPalace data
log "Backing up ~/.mempalace/..."
rsync -az --timeout=60 \
    "$HOME/.mempalace/" \
    "$JETSON_HOST:$REMOTE_DATED/mempalace/" 2>>"$LOG_FILE" && log "mempalace OK" || log "[WARN] mempalace backup failed"

# 3. HA .storage (device pairings, entity registry)
log "Backing up HA .storage/..."
rsync -az --timeout=60 \
    "$REPO_DIR/homeassistant/.storage/" \
    "$JETSON_HOST:$REMOTE_DATED/ha-storage/" 2>>"$LOG_FILE" && log "ha-storage OK" || log "[WARN] ha-storage backup failed"

# 4. .env (non-sensitive parts — rsync, not pushed to git)
rsync -az --timeout=10 \
    "$REPO_DIR/.env" \
    "$JETSON_HOST:$REMOTE_DATED/" 2>>"$LOG_FILE" && log ".env OK" || true

# 5. Prune old backups on Jetson (keep last N days)
log "Pruning backups older than $MAX_BACKUPS days on Jetson..."
ssh "$JETSON_HOST" "find $REMOTE_DIR -maxdepth 1 -type d -mtime +$MAX_BACKUPS -exec rm -rf {} + 2>/dev/null; echo 'Prune done'" 2>>"$LOG_FILE" || log "[WARN] Prune failed (non-fatal)"

log "=== Backup complete ==="
