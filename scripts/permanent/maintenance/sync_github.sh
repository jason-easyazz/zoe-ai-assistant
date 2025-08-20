#!/bin/bash
# Quick GitHub sync script

cd /home/pi/zoe

# Add all changes
git add .

# Commit with timestamp or custom message
if [ $# -eq 0 ]; then
    TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
    git commit -m "🔄 Auto-sync: $TIMESTAMP" || echo "No changes to commit"
else
    git commit -m "$1" || echo "No changes to commit"
fi

# Push to GitHub
git push origin main || echo "Push failed - check authentication"

echo "✅ GitHub sync complete"
