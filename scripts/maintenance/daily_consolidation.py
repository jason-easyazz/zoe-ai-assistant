#!/usr/bin/env python3
"""
Daily Memory Consolidation Script
Runs at 2am to create daily summaries (+ weekly summaries on Sundays).

Historically this imported `memory_consolidator` from the retired
`services/zoe-core` SQLite service (archived in docs/archive/retired-services/
and later purged). That module no longer exists; the live replacement is
`memory_digest.py` in `services/zoe-data`, which is Postgres-backed and
already wired into the nightly maintenance path (see
`scripts/maintenance/zoe-nightly-dreaming.py`). This script now calls the
same functions directly so a standalone 2am cron/timer still works.
"""
import sys
import os
from datetime import date
import asyncio
import logging

sys.path.append('/home/zoe/assistant/services/zoe-data')
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


async def run_daily_consolidation():
    """Run daily (and, on Sundays, weekly) memory consolidation for all users."""
    from db_pool import close_pool, get_db_ctx, init_pool
    from memory_digest import run_digest_for_all_active_users, run_weekly_consolidation_for_all

    logger.info("🌙 Starting daily memory consolidation")

    await init_pool()
    try:
        async with get_db_ctx() as db:
            try:
                results = await run_digest_for_all_active_users(db=db)
                logger.info(f"✅ Daily digest complete: {len(results)} user(s) processed")
                for row in results:
                    logger.info(f"   {row}")
            except Exception as e:
                logger.error(f"❌ Daily consolidation failed: {e}")

            if date.today().weekday() == 6:  # Sunday
                logger.info("📅 Sunday - Creating weekly summaries")
                try:
                    weekly_results = await run_weekly_consolidation_for_all(db=db)
                    logger.info(
                        f"✅ Weekly consolidation complete: {len(weekly_results)} user(s) processed"
                    )
                    for row in weekly_results:
                        logger.info(f"   {row}")
                except Exception as e:
                    logger.error(f"❌ Weekly consolidation failed: {e}")
    finally:
        await close_pool()

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
