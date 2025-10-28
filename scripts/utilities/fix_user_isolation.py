#!/usr/bin/env python3
"""
Security Fix: Replace user_id=Query("default") with proper authentication
Fixes user isolation vulnerability across all routers
"""
import re
import sys
from pathlib import Path

# Routers to fix (in priority order)
CRITICAL_ROUTERS = [
    '/home/pi/zoe/services/zoe-core/routers/calendar.py',
    '/home/pi/zoe/services/zoe-core/routers/lists.py',
    '/home/pi/zoe/services/zoe-core/routers/journal.py',
    '/home/pi/zoe/services/zoe-core/routers/memories.py',
    '/home/pi/zoe/services/zoe-core/routers/reminders.py',
]

def fix_router(filepath):
    """Fix a single router file"""
    print(f"\n📝 Processing: {filepath}")
    
    with open(filepath, 'r') as f:
        content = f.read()
    
    original_content = content
    changes = 0
    
    # Pattern 1: user_id: str = Query("default")
    pattern1 = r'user_id:\s*str\s*=\s*Query\("default"[^)]*\)'
    matches1 = re.findall(pattern1, content)
    if matches1:
        print(f"  Found {len(matches1)} instances of user_id=Query(\"default\")")
        content = re.sub(pattern1, 'session: AuthenticatedSession = Depends(validate_session)', content)
        changes += len(matches1)
    
    # Pattern 2: user_id = Query("default")  
    pattern2 = r'user_id\s*=\s*Query\("default"\)'
    matches2 = re.findall(pattern2, content)
    if matches2:
        print(f"  Found {len(matches2)} standalone instances")
        content = re.sub(pattern2, 'session: AuthenticatedSession = Depends(validate_session)', content)
        changes += len(matches2)
    
    # Pattern 3: user_id: str = Query(None, ...) - these need manual review
    pattern3 = r'user_id:\s*str\s*=\s*Query\(None'
    matches3 = re.findall(pattern3, content)
    if matches3:
        print(f"  ⚠️  Found {len(matches3)} Query(None) instances - NEEDS MANUAL REVIEW")
    
    if content != original_content:
        # Now add user_id = session.user_id after each function definition
        # This is a bit tricky - we need to find functions and add the line
        print(f"  ✅ Made {changes} replacements")
        print(f"  ⚠️  You MUST manually add 'user_id = session.user_id' to each fixed function")
        
        with open(filepath, 'w') as f:
            f.write(content)
        return changes
    else:
        print("  ℹ️  No changes needed")
        return 0

def main():
    total_changes = 0
    
    print("=" * 80)
    print("🔒 USER ISOLATION SECURITY FIX")
    print("=" * 80)
    print("\nThis script fixes the critical security vulnerability where endpoints")
    print("use Query(\"default\") instead of requiring authentication.\n")
    
    for router_path in CRITICAL_ROUTERS:
        if Path(router_path).exists():
            changes = fix_router(router_path)
            total_changes += changes
        else:
            print(f"❌ NOT FOUND: {router_path}")
    
    print("\n" + "=" * 80)
    print(f"✅ Total replacements made: {total_changes}")
    print("=" * 80)
    print("\n⚠️  IMPORTANT: Manual steps still needed:")
    print("1. Add 'user_id = session.user_id' at the start of each fixed function")
    print("2. Verify the AuthenticatedSession import exists")
    print("3. Test each endpoint to ensure auth works")
    print("4. Remove any remaining 'user_id:' parameter declarations")
    print("\n")

if __name__ == "__main__":
    main()

