#!/bin/bash
# Quick GitHub sync
cd /home/pi/zoe
echo "🔄 Quick sync to GitHub..."
git add -A
git commit -m "🔄 Quick sync - $(date +%Y%m%d_%H%M%S)" || echo "No changes"
git push || echo "Push failed - check connection"
echo "✅ Sync complete"
