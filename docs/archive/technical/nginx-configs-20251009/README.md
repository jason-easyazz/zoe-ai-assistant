# Archived Nginx Configurations - October 9, 2025

## Why These Were Archived

These nginx configuration files were created as duplicates during development, violating the **Single Source of Truth** principle outlined in Zoe's architecture rules.

## The Problem

We had 5 nginx configs:
- `nginx.conf` - Main config (had hardcoded IPs)
- `nginx-clean.conf` - Clean version with service names
- `nginx-dev.conf` - Development variant
- `nginx-fixed.conf` - ❌ Forbidden suffix variant
- `nginx-hub.conf` - Hub variant

This is the **same anti-pattern** as the chat router duplication issue.

## The Solution

**Consolidated to SINGLE SOURCE OF TRUTH:**
- ✅ `nginx.conf` - Production config (replaced with clean version, uses service names)
- ✅ `nginx-dev.conf` - Development-only config (genuinely different - localhost:8080)
- ❌ All others archived here

## Key Improvements Made

The new `nginx.conf`:
1. **Uses service names** (`zoe-core`, `zoe-auth`) instead of hardcoded IPs
2. **Mass adoption ready** - Optional HTTPS redirect (commented out)
3. **Proper CORS headers** - For auth and API requests
4. **Service discovery** - Touch panel integration support

## Prevention

Updated `/home/pi/zoe/tools/audit/enforce_structure.py` with new check:
- `check_no_duplicate_configs()` - Blocks forbidden suffixes in nginx configs
- Pre-commit hook will catch this automatically

## Files Archived

- `nginx.conf.old` - Old version with hardcoded IP 172.23.0.3
- `nginx-clean.conf` - Used as base for new nginx.conf
- `nginx-fixed.conf` - Had extra proxy headers but forbidden suffix
- `nginx-hub.conf` - Similar to clean version

## Rule Reference

From `PROJECT_STRUCTURE_RULES.md`:
> ❌ FORBIDDEN: Files with suffixes _backup, _old, _new, _v2, _fixed, _optimized, _temp
> 
> If nginx.conf needs modification, MODIFY IT - don't create nginx_v2.conf
> 
> Use git for version history, not file duplication

---
**Date Archived:** October 9, 2025  
**Reason:** Single source of truth consolidation  
**Archived By:** Cursor AI (enforcing architecture rules)

