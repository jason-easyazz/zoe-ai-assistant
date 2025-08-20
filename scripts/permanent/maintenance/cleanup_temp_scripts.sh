#!/bin/bash
# Cleanup temporary scripts older than 7 days
find /home/pi/zoe/scripts/temporary -type f -mtime +7 -delete
echo "✅ Cleaned up old temporary scripts"
