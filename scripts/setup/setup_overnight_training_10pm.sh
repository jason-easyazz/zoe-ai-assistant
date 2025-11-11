#!/bin/bash
#
# Setup Overnight Training at 10pm (CPU-Only)
# Training takes 8-12 hours, completes by 6-10am
#

echo "🌙 Setting up overnight training for 10pm start..."

# Remove old 2am cron jobs if they exist
(crontab -l 2>/dev/null | grep -v "zoe/scripts/train/nightly_training.sh"; echo "# Zoe Intelligence - Overnight Training (10pm)"; echo "0 22 * * * /home/zoe/assistant/scripts/train/nightly_training.sh >> /var/log/zoe-training.log 2>&1"; echo "# Zoe Intelligence - Daily Memory Consolidation (9:30pm)"; echo "30 21 * * * /home/zoe/assistant/scripts/maintenance/daily_consolidation.py >> /var/log/zoe-consolidation.log 2>&1"; echo "# Zoe Intelligence - Weekly Preference Updates (9pm Sundays)"; echo "0 21 * * 0 /home/zoe/assistant/scripts/maintenance/weekly_preference_update.py >> /var/log/zoe-preferences.log 2>&1") | crontab -

echo "✅ Cron jobs updated for 10pm training:"
echo "   - 10:00 PM daily: Nightly CPU training (8-12 hours)"
echo "   - 9:30 PM daily: Memory consolidation"  
echo "   - 9:00 PM Sundays: Preference updates"
echo ""
echo "📋 Current crontab:"
crontab -l | grep -i zoe
echo ""
echo "🎉 Training schedule updated!"
echo ""
echo "Timeline:"
echo "  9:00 PM - Preference update (Sundays)"
echo "  9:30 PM - Memory consolidation"
echo "  10:00 PM - Training starts (CPU-only)"
echo "  6-10 AM - Training completes"
echo ""
echo "Next steps:"
echo "1. Use Zoe and provide feedback during the day"
echo "2. Training runs automatically tonight at 10pm"
echo "3. Check logs tomorrow: tail -f /var/log/zoe-training.log"
echo ""
echo "To monitor training progress:"
echo "  watch -n 60 'tail -20 /var/log/zoe-training.log'"












