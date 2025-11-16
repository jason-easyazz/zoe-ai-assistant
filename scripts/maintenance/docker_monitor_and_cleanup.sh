#!/bin/bash
# Docker Disk Space Monitor and Cleanup
# Prevents Docker from filling the drive
# Runs daily to catch issues before they become critical
# Created: 2025-11-05

set -e

LOG_FILE="/home/zoe/assistant/data/docker_monitor.log"
ALERT_FILE="/home/zoe/assistant/data/disk_alerts.log"

# Thresholds
DOCKER_CRITICAL_GB=50  # If Docker uses more than 50GB, trigger aggressive cleanup
DOCKER_WARNING_GB=30   # If Docker uses more than 30GB, trigger warning
DISK_CRITICAL_PERCENT=85
DISK_WARNING_PERCENT=70

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m'

log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

alert_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 🚨 ALERT: $1" | tee -a "$ALERT_FILE" "$LOG_FILE"
}

# Ensure Docker is running
ensure_docker_running() {
    if ! systemctl is-active --quiet docker; then
        log_message "Docker not running, starting it..."
        sudo systemctl start docker
        sleep 5
    fi
}

# Get Docker disk usage in GB
get_docker_usage_gb() {
    local docker_dir_size=$(sudo du -sb /var/lib/docker 2>/dev/null | cut -f1)
    echo $((docker_dir_size / 1024 / 1024 / 1024))
}

# Main monitoring and cleanup
main() {
    echo -e "${GREEN}========================================"
    echo "   Docker Monitor & Cleanup"
    echo "   $(date '+%Y-%m-%d %H:%M:%S')"
    echo -e "========================================${NC}"
    
    # Ensure Docker is running
    ensure_docker_running
    
    # Check overall disk usage
    DISK_USAGE=$(df / | awk 'NR==2 {print $5}' | sed 's/%//')
    DISK_AVAIL=$(df -h / | awk 'NR==2 {print $4}')
    echo -e "Disk Usage: ${DISK_USAGE}% (${DISK_AVAIL} available)"
    
    # Check Docker-specific usage
    DOCKER_SIZE_GB=$(get_docker_usage_gb)
    echo -e "Docker Directory: ${DOCKER_SIZE_GB}GB"
    
    # Get Docker system usage
    echo -e "\nDocker System Breakdown:"
    docker system df
    
    # Decision logic
    CRITICAL=false
    WARNING=false
    
    if [ "$DISK_USAGE" -ge "$DISK_CRITICAL_PERCENT" ]; then
        alert_message "CRITICAL: Disk usage at ${DISK_USAGE}%"
        CRITICAL=true
    elif [ "$DISK_USAGE" -ge "$DISK_WARNING_PERCENT" ]; then
        alert_message "WARNING: Disk usage at ${DISK_USAGE}%"
        WARNING=true
    fi
    
    if [ "$DOCKER_SIZE_GB" -ge "$DOCKER_CRITICAL_GB" ]; then
        alert_message "CRITICAL: Docker using ${DOCKER_SIZE_GB}GB (threshold: ${DOCKER_CRITICAL_GB}GB)"
        CRITICAL=true
    elif [ "$DOCKER_SIZE_GB" -ge "$DOCKER_WARNING_GB" ]; then
        alert_message "WARNING: Docker using ${DOCKER_SIZE_GB}GB (threshold: ${DOCKER_WARNING_GB}GB)"
        WARNING=true
    fi
    
    # Cleanup actions
    if [ "$CRITICAL" = true ]; then
        echo -e "\n${RED}🚨 CRITICAL: Running aggressive Docker cleanup${NC}"
        log_message "Running aggressive Docker cleanup"
        
        # Stop containers
        echo "Stopping all containers..."
        docker ps -q | xargs -r docker stop
        
        # Remove stopped containers
        echo "Removing stopped containers..."
        docker ps -aq | xargs -r docker rm
        
        # Remove all unused images (not just dangling)
        echo "Removing unused images..."
        docker images -q | xargs -r docker rmi -f 2>/dev/null || true
        
        # Remove all unused volumes
        echo "Removing unused volumes..."
        docker volume prune -af
        
        # Remove all build cache
        echo "Removing build cache..."
        docker builder prune -af
        
        # Full system prune
        echo "Running full system prune..."
        docker system prune -af --volumes
        
        log_message "Aggressive cleanup completed"
        
    elif [ "$WARNING" = true ]; then
        echo -e "\n${YELLOW}⚠️  WARNING: Running standard Docker cleanup${NC}"
        log_message "Running standard Docker cleanup"
        
        # Remove stopped containers
        docker ps -aq -f status=exited | xargs -r docker rm
        
        # Remove dangling images
        docker images -qf dangling=true | xargs -r docker rmi
        
        # Remove unused volumes
        docker volume prune -f
        
        # Remove old build cache (7+ days)
        docker builder prune -f --filter "until=168h"
        
        # System prune (safe)
        docker system prune -f --volumes
        
        log_message "Standard cleanup completed"
        
    else
        echo -e "\n${GREEN}✅ Docker disk usage is healthy${NC}"
        
        # Still do light maintenance
        echo "Running light maintenance..."
        docker system prune -f --filter "until=72h"
        
        log_message "Light maintenance completed"
    fi
    
    # Report final state
    echo -e "\n${GREEN}========================================"
    FINAL_USAGE=$(df / | awk 'NR==2 {print $5}' | sed 's/%//')
    FINAL_DOCKER_GB=$(get_docker_usage_gb)
    
    echo "After Cleanup:"
    echo "  Disk: ${FINAL_USAGE}% used"
    echo "  Docker: ${FINAL_DOCKER_GB}GB"
    echo -e "========================================${NC}"
    
    log_message "Monitoring complete - Disk: ${FINAL_USAGE}%, Docker: ${FINAL_DOCKER_GB}GB"
}

main "$@"

