#!/bin/bash
# Emergency Disk Space Cleanup Script
# Safely cleans up space when disk usage is critical
# Created: 2025-11-05

set -e

BACKUP_DIR="/home/zoe/assistant/data/backups"
DATA_DIR="/home/zoe/assistant/data"
LOG_FILE="/home/zoe/assistant/data/cleanup.log"

# Minimum backups to keep per database (safety)
MIN_BACKUPS=7  # Keep at least 7 most recent backups (42 hours)

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m'

log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

echo -e "${RED}========================================"
echo "   Emergency Disk Cleanup"
echo "   $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================${NC}"
echo ""

# Get current disk usage
DISK_USAGE=$(df / | awk 'NR==2 {print $5}' | sed 's/%//')
DISK_AVAIL=$(df -h / | awk 'NR==2 {print $4}')
log_message "Starting cleanup - Disk usage: ${DISK_USAGE}%, Available: $DISK_AVAIL"

# 1. Clean up old backups (keep only MIN_BACKUPS most recent per database)
echo -e "${YELLOW}🧹 Cleaning old database backups...${NC}"
if [ -d "$BACKUP_DIR" ]; then
    BACKUP_SIZE_BEFORE=$(du -sm "$BACKUP_DIR" | cut -f1)
    
    cd "$BACKUP_DIR"
    for db_prefix in zoe memory training; do
        file_count=$(ls -1 ${db_prefix}_*.db 2>/dev/null | wc -l)
        if [ "$file_count" -gt "$MIN_BACKUPS" ]; then
            files_to_delete=$((file_count - MIN_BACKUPS))
            echo "  Deleting $files_to_delete old ${db_prefix} backups (keeping $MIN_BACKUPS most recent)"
            ls -t ${db_prefix}_*.db 2>/dev/null | tail -n +$((MIN_BACKUPS + 1)) | xargs -r rm
            log_message "Deleted $files_to_delete old ${db_prefix} backups"
        else
            echo "  ${db_prefix}: $file_count backups (under minimum, keeping all)"
        fi
    done
    
    BACKUP_SIZE_AFTER=$(du -sm "$BACKUP_DIR" | cut -f1)
    SAVED=$((BACKUP_SIZE_BEFORE - BACKUP_SIZE_AFTER))
    echo -e "${GREEN}  Freed ${SAVED}MB from backups${NC}"
    log_message "Freed ${SAVED}MB from backup cleanup"
else
    echo "  Backup directory not found"
fi

# 2. Clean up temp files older than 7 days
echo -e "${YELLOW}🧹 Cleaning old temp files...${NC}"
TEMP_CLEANED=$(find /tmp -type f -mtime +7 -user pi -delete -print 2>/dev/null | wc -l)
echo -e "${GREEN}  Removed $TEMP_CLEANED old temp files${NC}"
log_message "Removed $TEMP_CLEANED temp files"

# 3. Docker cleanup (if Docker is running)
if systemctl is-active --quiet docker 2>/dev/null; then
    echo -e "${YELLOW}🧹 Cleaning Docker artifacts...${NC}"
    
    # Remove stopped containers
    STOPPED_CONTAINERS=$(docker ps -aq -f status=exited 2>/dev/null | wc -l)
    if [ "$STOPPED_CONTAINERS" -gt 0 ]; then
        docker rm $(docker ps -aq -f status=exited) 2>/dev/null || true
        echo -e "${GREEN}  Removed $STOPPED_CONTAINERS stopped containers${NC}"
        log_message "Removed $STOPPED_CONTAINERS stopped Docker containers"
    fi
    
    # Remove dangling images
    DANGLING_IMAGES=$(docker images -qf dangling=true 2>/dev/null | wc -l)
    if [ "$DANGLING_IMAGES" -gt 0 ]; then
        docker rmi $(docker images -qf dangling=true) 2>/dev/null || true
        echo -e "${GREEN}  Removed $DANGLING_IMAGES dangling images${NC}"
        log_message "Removed $DANGLING_IMAGES dangling Docker images"
    fi
    
    # Remove unused volumes
    docker volume prune -f 2>/dev/null || true
    echo "  Pruned unused Docker volumes"
    
    # Remove build cache (keep last 24h)
    docker builder prune -f --filter "until=24h" 2>/dev/null || true
    echo "  Pruned old Docker build cache"
    
    log_message "Docker cleanup completed"
else
    echo "  Docker not running, skipping"
fi

# 4. Rotate systemd journal (keep last 100MB)
echo -e "${YELLOW}🧹 Rotating systemd journal...${NC}"
sudo journalctl --vacuum-size=100M 2>/dev/null || true
echo -e "${GREEN}  Journal rotated to 100MB max${NC}"
log_message "Systemd journal rotated"

# 5. Clean APT cache
echo -e "${YELLOW}🧹 Cleaning APT cache...${NC}"
sudo apt-get clean 2>/dev/null || true
echo -e "${GREEN}  APT cache cleaned${NC}"
log_message "APT cache cleaned"

# 6. Remove old database backups in data dir (outside backups/)
echo -e "${YELLOW}🧹 Checking for old database backups in data dir...${NC}"
OLD_BACKUPS=$(find "$DATA_DIR" -maxdepth 1 -name "*.db.backup-*" -mtime +30 2>/dev/null | wc -l)
if [ "$OLD_BACKUPS" -gt 0 ]; then
    find "$DATA_DIR" -maxdepth 1 -name "*.db.backup-*" -mtime +30 -delete 2>/dev/null
    echo -e "${GREEN}  Removed $OLD_BACKUPS old database backup files${NC}"
    log_message "Removed $OLD_BACKUPS old database backups from data dir"
else
    echo "  No old database backups found"
fi

# Final status
echo ""
echo -e "${GREEN}========================================"
FINAL_USAGE=$(df / | awk 'NR==2 {print $5}' | sed 's/%//')
FINAL_AVAIL=$(df -h / | awk 'NR==2 {print $4}')
FREED=$((DISK_USAGE - FINAL_USAGE))
echo "Cleanup Complete!"
echo "Before: ${DISK_USAGE}% used"
echo "After:  ${FINAL_USAGE}% used"
echo "Freed:  ${FREED}% ($FINAL_AVAIL available)"
echo -e "========================================${NC}"

log_message "Emergency cleanup completed - Freed ${FREED}% disk space"

