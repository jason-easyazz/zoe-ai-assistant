#!/bin/bash
#
# Setup Overnight Training Automation
# Adds cron jobs for nightly training, consolidation, and preference updates
#

echo "🌙 Setting up overnight training automation..."

# Create temporary cron file with existing crontab + new jobs
(crontab -l 2>/dev/null; echo "# Zoe Intelligence - Overnight Training"; echo "0 2 * * * /home/zoe/assistant/scripts/train/nightly_training.sh >> /var/log/zoe-training.log 2>&1"; echo "# Zoe Intelligence - Daily Memory Consolidation"; echo "30 1 * * * /home/zoe/assistant/scripts/maintenance/daily_consolidation.py >> /var/log/zoe-consolidation.log 2>&1"; echo "# Zoe Intelligence - Weekly Preference Updates"; echo "0 1 * * 0 /home/zoe/assistant/scripts/maintenance/weekly_preference_update.py >> /var/log/zoe-preferences.log 2>&1") | crontab -

echo "✅ Cron jobs added:"
echo "   - 2:00 AM daily: Nightly training"
echo "   - 1:30 AM daily: Memory consolidation"
echo "   - 1:00 AM Sundays: Preference updates"
echo ""
echo "📋 Current crontab:"
crontab -l | grep -i zoe
echo ""
echo "🎉 Overnight training is now enabled!"
echo ""
echo "Next steps:"
echo "1. Use Zoe and provide feedback for 5-7 days"
echo "2. First training will run automatically when you have 20+ examples"
echo "3. Check logs: tail -f /var/log/zoe-training.log"
echo ""
echo "To disable: crontab -e (and remove Zoe lines)"












