#!/bin/bash
# Disk Space Monitoring Script for Zoe
# Alerts when disk usage exceeds thresholds

# Thresholds
WARN_THRESHOLD=80
CRIT_THRESHOLD=90
LOG_FILE="/var/log/disk-monitor.log"
ALERT_LOG="/home/zoe/assistant/logs/disk-alerts.log"

# Ensure log directory exists
mkdir -p "$(dirname "$ALERT_LOG")"

# Get root filesystem usage percentage (without % sign)
USAGE=$(df / | tail -1 | awk '{print $5}' | sed 's/%//')

# Get timestamp
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# Log current status
echo "[$TIMESTAMP] Disk usage: ${USAGE}%" >> "$LOG_FILE"

# Check thresholds and alert
if [[ $USAGE -ge $CRIT_THRESHOLD ]]; then
    echo "[$TIMESTAMP] CRITICAL: Disk usage at ${USAGE}% (threshold: ${CRIT_THRESHOLD}%)" | tee -a "$LOG_FILE"
    logger -t disk-monitor -p user.crit "CRITICAL: Root filesystem at ${USAGE}%"
    # Send notification to Zoe logs
    echo "[$TIMESTAMP] CRITICAL ALERT: Disk space at ${USAGE}%" >> "$ALERT_LOG"
elif [[ $USAGE -ge $WARN_THRESHOLD ]]; then
    echo "[$TIMESTAMP] WARNING: Disk usage at ${USAGE}% (threshold: ${WARN_THRESHOLD}%)" | tee -a "$LOG_FILE"
    logger -t disk-monitor -p user.warning "WARNING: Root filesystem at ${USAGE}%"
    echo "[$TIMESTAMP] WARNING: Disk space at ${USAGE}%" >> "$ALERT_LOG"
else
    echo "[$TIMESTAMP] OK: Disk usage at ${USAGE}%" >> "$LOG_FILE"
fi

# Rotate log if it gets too large (keep last 1000 lines)
if [[ -f "$LOG_FILE" ]] && [[ $(wc -l < "$LOG_FILE") -gt 1000 ]]; then
    tail -1000 "$LOG_FILE" > "$LOG_FILE.tmp"
    mv "$LOG_FILE.tmp" "$LOG_FILE"
fi

exit 0

