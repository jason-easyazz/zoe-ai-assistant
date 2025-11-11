#!/usr/bin/env python3
"""
Daily Memory Consolidation Script
Runs at 2am to create daily summaries
"""
import sys
import os
from datetime import date, timedelta
import asyncio
import logging

sys.path.append('/home/zoe/assistant/services/zoe-core')
sys.path.append('/app')

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/zoe-consolidation.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

from memory_consolidation import memory_consolidator


async def run_daily_consolidation():
    """Run consolidation for all users"""
    
    logger.info("🌙 Starting daily memory consolidation")
    
    # For now, consolidate for default user
    # TODO: Get all users from database
    users = ["default"]
    
    yesterday = date.today() - timedelta(days=1)
    
    for user_id in users:
        try:
            logger.info(f"📝 Consolidating memories for user: {user_id}")
            
            # Create daily summary
            summary = await memory_consolidator.create_daily_summary(user_id, yesterday)
            
            logger.info(f"✅ Daily summary created for {user_id}")
            logger.info(f"   Summary: {summary[:100]}...")
            
        except Exception as e:
            logger.error(f"❌ Consolidation failed for {user_id}: {e}")
    
    # Create weekly summary on Sundays
    if date.today().weekday() == 6:  # Sunday
        logger.info("📅 Sunday - Creating weekly summaries")
        
        for user_id in users:
            try:
                weekly_summary = await memory_consolidator.create_weekly_summary(user_id)
                logger.info(f"✅ Weekly summary created for {user_id}")
            except Exception as e:
                logger.error(f"❌ Weekly summary failed for {user_id}: {e}")
    
    logger.info("🌅 Memory consolidation complete!")


def main():
    """Entry point"""
    try:
        asyncio.run(run_daily_consolidation())
        return 0
    except Exception as e:
        logger.error(f"❌ Consolidation failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())












