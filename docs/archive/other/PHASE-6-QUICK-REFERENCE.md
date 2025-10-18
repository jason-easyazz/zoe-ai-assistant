# Phase 6 memvid Quick Reference

**Status**: ✅ OPERATIONAL  
**Date**: October 18, 2025

## Quick Commands

```bash
# Load productivity scripts
source /home/pi/zoe/scripts/utilities/zoe-superpowers.sh

# Check what you were working on
zoe-what-was-i-doing

# View storage
zoe-storage

# Check memvid system
curl http://localhost:8000/api/archives/health | jq .

# Trigger quarterly archival (dry-run safe)
curl -X POST http://localhost:8000/api/archives/create \
  -H "Content-Type: application/json" \
  -d '{"year": 2025, "quarter": 3, "dry_run": true}' | jq .

# Analyze user history
curl http://localhost:8000/api/archives/learning/analyze/system | jq .

# Get predictions
curl http://localhost:8000/api/archives/predictions/system | jq .
```

## What Gets Archived

✅ Chats (>90 days)  
✅ Journals (>90 days)  
✅ Completed tasks (>90 days)  
✅ Patterns discovered  
✅ Photos metadata  
✅ Voice transcriptions  
✅ Home automation events  

❌ Active tasks/lists  
❌ Future events  
❌ Current people data  
❌ Recent 90 days  

## Key Files

- `/services/zoe-core/memvid_archiver.py` - Archival engine
- `/services/zoe-core/routers/memvid_archives.py` - API router
- `/services/zoe-core/unified_learner.py` - Pattern analysis
- `/services/zoe-core/predictive_intelligence.py` - Predictions
- `/scripts/maintenance/quarterly_archival.sh` - Automation

## Next Steps

1. **Wait** for Q4 2025 data to accumulate (Nov-Dec)
2. **Jan 1, 2026**: First automatic quarterly archival runs
3. **Monitor**: Archives created, learning patterns discovered
4. **Evolve**: Trigger evolution monthly from archives

---

**Full docs**: `/docs/guides/memvid-integration.md`



