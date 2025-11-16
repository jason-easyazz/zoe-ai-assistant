# Disk Space Protection System

**Created:** 2025-11-05  
**Purpose:** Prevent drive from filling up (218GB Docker issue resolution)

## Problem History

- **Occurred:** Twice within a week
- **Symptom:** Drive filled to 93% (218GB of 235GB used by Docker)
- **Root Cause:** Docker images/layers accumulating without cleanup
  - 16 services with `build:` directives in docker-compose.yml
  - Prune services existed but weren't running effectively (Docker not running when timers fired)
  - No proactive monitoring to catch issues before critical

## Implemented Solutions

### 1. Automated Docker Monitoring & Cleanup

**Script:** `/home/zoe/assistant/scripts/maintenance/docker_monitor_and_cleanup.sh`

**Runs:** Daily at 2:00 AM (systemd timer: `docker-prune-daily.timer`)

**Thresholds:**
- **Warning:** 30GB Docker usage or 70% disk usage
- **Critical:** 50GB Docker usage or 85% disk usage

**Actions:**
- **Healthy:** Light cleanup (3+ day old cache)
- **Warning:** Standard cleanup (stopped containers, dangling images, unused volumes)
- **Critical:** Aggressive cleanup (stop all containers, remove all unused images/volumes/cache)

**Verification:**
```bash
systemctl status docker-prune-daily.timer
journalctl -u docker-prune-daily.service -f
```

### 2. Disk Space Monitoring

**Script:** `/home/zoe/assistant/scripts/maintenance/disk_space_monitor.sh`

**Runs:** Every 6 hours via cron

**Monitors:**
- Overall disk usage (threshold: 70% warning, 85% critical)
- Backup directory size (threshold: 200MB)
- Docker system usage
- Temp files in /tmp (threshold: 1GB)
- Systemd journal size

**Alerts:** Writes to `/home/zoe/assistant/data/disk_alerts.log`

**Automatic Action:** Triggers emergency cleanup if disk hits 85%

### 3. Emergency Cleanup Script

**Script:** `/home/zoe/assistant/scripts/maintenance/emergency_cleanup.sh`

**Triggered:** Automatically by disk monitor when critical, or run manually

**Actions:**
1. Clean old database backups (keep 7 most recent)
2. Remove temp files older than 7 days
3. Docker cleanup (containers, images, volumes, cache)
4. Rotate systemd journal to 100MB
5. Clean APT cache
6. Remove old database backup files (30+ days)

**Manual Usage:**
```bash
/home/zoe/assistant/scripts/maintenance/emergency_cleanup.sh
```

### 4. Backup Retention Policy

**Updated:** `/home/zoe/assistant/scripts/maintenance/auto_backup.sh`

**Previous:** Keep 48 backups per database (12 days @ 6-hourly = ~3.6GB accumulated)  
**Current:** Keep 14 backups per database (3.5 days @ 6-hourly = ~1GB max)

**Backup Schedule:** Every 6 hours (00:00, 06:00, 12:00, 18:00)

**Current State:** 42 backups, 105MB (healthy)

### 5. Docker Prune Services (Fixed)

**Services:**
- `docker-prune-daily.service` - NEW: Intelligent monitoring & cleanup (daily @ 2 AM)
- `docker-prune-weekly.service` - Standard cleanup (Sundays @ 3 AM)
- `docker-prune-monthly.service` - Aggressive cleanup (1st of month @ midnight)

**Fix Applied:** All services now ensure Docker is running before executing

**Verification:**
```bash
systemctl list-timers --all | grep docker
```

## Monitoring & Alerts

### Check Current Status

```bash
# Overall disk usage
df -h /

# Docker usage
sudo du -sh /var/lib/docker
docker system df

# Backup usage
du -sh /home/zoe/assistant/data/backups

# View alerts
cat /home/zoe/assistant/data/disk_alerts.log

# View monitoring log
tail -f /home/zoe/assistant/data/disk_monitor.log
```

### Manual Cleanup (if needed)

```bash
# Emergency cleanup
/home/zoe/assistant/scripts/maintenance/emergency_cleanup.sh

# Docker-specific cleanup
/home/zoe/assistant/scripts/maintenance/docker_monitor_and_cleanup.sh

# Backup cleanup
cd /home/zoe/assistant/data/backups && \
ls -t zoe_*.db | tail -n +15 | xargs rm
```

## Prevention Measures

### Docker Best Practices

1. **Avoid frequent rebuilds** - Use `docker-compose up` instead of `docker-compose up --build`
2. **Use pre-built images** where possible instead of `build:` directives
3. **Monitor volume growth** - Check `docker volume ls` and `docker system df -v`
4. **Regular cleanup** - Let automated systems run, don't disable timers

### Backup Best Practices

1. **Keep backups lean** - 14 backups @ 6-hourly = 84-hour coverage
2. **Monitor backup directory** - Alert at 200MB threshold
3. **Off-site backups** - Consider rsync to external storage for disaster recovery

### General System Health

1. **Check disk daily** - `df -h` should stay under 70%
2. **Review alerts weekly** - Check `/home/zoe/assistant/data/disk_alerts.log`
3. **Test emergency cleanup** - Run quarterly to ensure it works
4. **Update thresholds** - Adjust if usage patterns change

## Automated Schedule Summary

| Time | Task | Purpose |
|------|------|---------|
| Every 6 hours | Database backups | Data protection |
| Every 6 hours | Disk monitoring | Early warning system |
| Daily @ 2:00 AM | Docker monitoring & cleanup | Prevent Docker bloat |
| Weekly @ 3:00 AM | Docker prune | Remove unused resources |
| Monthly @ midnight | Aggressive Docker cleanup | Deep clean |
| Daily @ 2:00 AM | Temp file cleanup | General maintenance |

## Troubleshooting

### Disk Still Filling Up?

1. Check Docker: `sudo du -sh /var/lib/docker/*`
2. Check backups: `du -sh /home/zoe/assistant/data/backups`
3. Check logs: `sudo journalctl --disk-usage`
4. Check temp: `du -sh /tmp`
5. Find large files: `find /home/pi -type f -size +100M 2>/dev/null`

### Docker Cleanup Not Running?

```bash
# Check timer status
systemctl status docker-prune-daily.timer

# Check service logs
journalctl -u docker-prune-daily.service -n 50

# Check if Docker is running
systemctl status docker

# Manual trigger
sudo systemctl start docker-prune-daily.service
```

### Backup Cleanup Not Working?

```bash
# Check auto_backup logs
tail -f /home/zoe/assistant/data/backup.log

# Verify cron job
crontab -l | grep auto_backup

# Manual cleanup
cd /home/zoe/assistant/data/backups
for db in zoe memory training; do
  ls -t ${db}_*.db | tail -n +15 | xargs rm
done
```

## Success Metrics

- ✅ Disk usage stays under 70%
- ✅ Docker directory stays under 30GB
- ✅ Backups stay under 200MB
- ✅ No manual intervention needed for 30+ days
- ✅ Alerts trigger before critical state
- ✅ Emergency cleanup recovers 20%+ disk space

## Contact & Support

If disk fills up again despite these safeguards:
1. Check `/home/zoe/assistant/data/disk_alerts.log` for patterns
2. Review Docker usage with `docker system df -v`
3. Verify all timers are running: `systemctl list-timers`
4. Run emergency cleanup manually
5. Investigate unusual growth in specific directories

