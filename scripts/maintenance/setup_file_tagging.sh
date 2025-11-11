#!/bin/bash
"""
Setup File Tagging System
Creates cron job to automatically tag unused files weekly
"""

echo "🏷️  Setting up File Tagging System"
echo "=================================="

# Make scripts executable
chmod +x /home/zoe/assistant/scripts/maintenance/tag_unused_files.py
chmod +x /home/zoe/assistant/scripts/maintenance/archive_tagged_files.py

# Create cron job to run weekly (every Sunday at 2 AM)
CRON_JOB="0 2 * * 0 cd /home/zoe/assistant && python3 scripts/maintenance/tag_unused_files.py >> scripts/maintenance/tagging.log 2>&1"

# Add to crontab if not already present
(crontab -l 2>/dev/null | grep -v "tag_unused_files.py"; echo "$CRON_JOB") | crontab -

echo "✅ File tagging system setup complete!"
echo ""
echo "📋 What was configured:"
echo "   - Weekly tagging of unused files (Sundays at 2 AM)"
echo "   - Logs saved to: scripts/maintenance/tagging.log"
echo ""
echo "🔧 Manual commands:"
echo "   - Tag unused files: python3 scripts/maintenance/tag_unused_files.py"
echo "   - Archive tagged files: python3 scripts/maintenance/archive_tagged_files.py"
echo ""
echo "📁 Files will be tagged if not accessed in 7 days"
echo "   - Essential files are automatically excluded"
echo "   - Manual confirmation required for archival"
