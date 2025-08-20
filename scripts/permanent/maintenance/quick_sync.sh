#!/bin/bash
# QUICK GITHUB SYNC
cd /home/pi/zoe

echo "🔄 Quick sync to GitHub..."
git add .
git commit -m "🔄 Quick sync - $(date +%H:%M)" || echo "No changes"
git push || echo "Already up to date"
echo "✅ Sync complete"
