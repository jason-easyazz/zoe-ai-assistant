#!/bin/bash
# Disk Space Monitor with Alerts
# Monitors disk usage and sends alerts when thresholds are exceeded
# Created: 2025-11-05

set -e

LOG_FILE="/home/zoe/assistant/data/disk_monitor.log"
ALERT_FILE="/home/zoe/assistant/data/disk_alerts.log"

# Thresholds
DISK_WARNING_PERCENT=70
DISK_CRITICAL_PERCENT=85
BACKUP_DIR_MAX_MB=200
DOCKER_LOG_MAX_MB=500

# Colors for output
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

alert_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 🚨 ALERT: $1" | tee -a "$ALERT_FILE" "$LOG_FILE"
}

# Check overall disk usage
check_disk_usage() {
    local usage=$(df / | awk 'NR==2 {print $5}' | sed 's/%//')
    local avail=$(df -h / | awk 'NR==2 {print $4}')
    
    echo -e "${GREEN}Disk Usage Check:${NC} $usage% used, $avail available"
    
    if [ "$usage" -ge "$DISK_CRITICAL_PERCENT" ]; then
        alert_message "CRITICAL: Disk usage at ${usage}% (threshold: ${DISK_CRITICAL_PERCENT}%)"
        return 2
    elif [ "$usage" -ge "$DISK_WARNING_PERCENT" ]; then
        alert_message "WARNING: Disk usage at ${usage}% (threshold: ${DISK_WARNING_PERCENT}%)"
        return 1
    else
        log_message "Disk usage OK: ${usage}%"
        return 0
    fi
}

# Check backup directory size
check_backup_size() {
    local backup_dir="/home/zoe/assistant/data/backups"
    if [ -d "$backup_dir" ]; then
        local size_mb=$(du -sm "$backup_dir" | cut -f1)
        local file_count=$(find "$backup_dir" -type f | wc -l)
        
        echo -e "${GREEN}Backup Directory:${NC} $size_mb MB, $file_count files"
        
        if [ "$size_mb" -gt "$BACKUP_DIR_MAX_MB" ]; then
            alert_message "Backup directory exceeds ${BACKUP_DIR_MAX_MB}MB: ${size_mb}MB with $file_count files"
            return 1
        else
            log_message "Backup directory OK: ${size_mb}MB"
            return 0
        fi
    fi
}

# Check Docker usage (if running)
check_docker_usage() {
    if systemctl is-active --quiet docker 2>/dev/null; then
        local docker_usage=$(docker system df --format "{{.Size}}" 2>/dev/null || echo "N/A")
        echo -e "${GREEN}Docker Usage:${NC} $docker_usage"
        log_message "Docker system usage: $docker_usage"
        
        # Check for large container logs
        if [ -d "/var/lib/docker/containers" ]; then
            local large_logs=$(sudo find /var/lib/docker/containers -name "*-json.log" -size +100M -exec ls -lh {} \; 2>/dev/null | wc -l)
            if [ "$large_logs" -gt 0 ]; then
                alert_message "Found $large_logs Docker container logs over 100MB"
                return 1
            fi
        fi
    else
        echo -e "${YELLOW}Docker:${NC} Not running"
    fi
    return 0
}

# Check for temp files and cache
check_temp_files() {
    local tmp_size=$(du -sm /tmp 2>/dev/null | cut -f1 || echo "0")
    echo -e "${GREEN}Temp Files (/tmp):${NC} $tmp_size MB"
    
    if [ "$tmp_size" -gt 1000 ]; then
        alert_message "Temp directory is large: ${tmp_size}MB"
        return 1
    fi
    return 0
}

# Check systemd journal size
check_journal_size() {
    local journal_size=$(sudo journalctl --disk-usage 2>/dev/null | grep -oP '\d+(\.\d+)?[MGT]' | head -1 || echo "0M")
    echo -e "${GREEN}Systemd Journal:${NC} $journal_size"
    log_message "Journal size: $journal_size"
}

# Main execution
main() {
    echo "========================================"
    echo "   Zoe Disk Space Monitor"
    echo "   $(date '+%Y-%m-%d %H:%M:%S')"
    echo "========================================"
    echo ""
    
    check_disk_usage
    disk_status=$?
    
    check_backup_size
    backup_status=$?
    
    check_docker_usage
    docker_status=$?
    
    check_temp_files
    temp_status=$?
    
    check_journal_size
    
    echo ""
    echo "========================================"
    
    # If any critical issues found, trigger cleanup
    if [ $disk_status -eq 2 ]; then
        echo -e "${RED}⚠️  CRITICAL: Running emergency cleanup!${NC}"
        /home/zoe/assistant/scripts/maintenance/emergency_cleanup.sh
    elif [ $disk_status -eq 1 ] || [ $backup_status -eq 1 ]; then
        echo -e "${YELLOW}⚠️  WARNING: Consider running cleanup${NC}"
        echo "  Run: /home/zoe/assistant/scripts/maintenance/emergency_cleanup.sh"
    else
        echo -e "${GREEN}✅ All disk space checks passed${NC}"
    fi
    
    log_message "Disk space check completed with status: disk=$disk_status, backup=$backup_status, docker=$docker_status"
}

main "$@"

