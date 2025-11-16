#!/usr/bin/env python3
"""
Weekly Preference Update Script
Analyzes feedback patterns and updates user preferences
Runs every Sunday at 1am
"""
import sys
import os
import asyncio
import logging

sys.path.append('/home/zoe/assistant/services/zoe-core')
sys.path.append('/app')

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/zoe-preferences.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

from preference_learner import preference_learner


async def update_preferences_for_all_users():
    """Analyze and update preferences for all users"""
    
    logger.info("📊 Starting weekly preference analysis")
    
    # TODO: Get all users from database
    # For now, just default user
    users = ["default"]
    
    for user_id in users:
        try:
            logger.info(f"🔍 Analyzing preferences for {user_id}")
            
            # Analyze feedback patterns
            preferences = await preference_learner.analyze_feedback_patterns(user_id)
            
            if preferences:
                logger.info(f"✅ Preferences updated for {user_id}:")
                logger.info(f"   Length: {preferences.get('response_length')}")
                logger.info(f"   Tone: {preferences.get('tone_preference')}")
                logger.info(f"   Emoji: {preferences.get('emoji_usage')}")
                logger.info(f"   Detail: {preferences.get('detail_level')}")
            else:
                logger.info(f"⏭️  Not enough data yet for {user_id}")
                
        except Exception as e:
            logger.error(f"❌ Preference update failed for {user_id}: {e}")
    
    logger.info("🌅 Preference analysis complete!")


def main():
    """Entry point"""
    try:
        asyncio.run(update_preferences_for_all_users())
        return 0
    except Exception as e:
        logger.error(f"❌ Preference update failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())












