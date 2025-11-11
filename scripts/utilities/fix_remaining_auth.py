#!/usr/bin/env python3
"""
Security Fix: Complete the remaining authentication fixes across all routers
"""
import re
from pathlib import Path

# Remaining routers to fix
REMAINING_ROUTERS = [
    '/home/zoe/assistant/services/zoe-core/routers/weather.py',
    '/home/zoe/assistant/services/zoe-core/routers/chat_sessions.py',
    '/home/zoe/assistant/services/zoe-core/routers/push.py',
    '/home/zoe/assistant/services/zoe-core/routers/workflows.py',
    '/home/zoe/assistant/services/zoe-core/routers/journeys.py',
    '/home/zoe/assistant/services/zoe-core/routers/onboarding.py',
    '/home/zoe/assistant/services/zoe-core/routers/self_awareness.py',
    '/home/zoe/assistant/services/zoe-core/routers/proactive_insights.py',
    '/home/zoe/assistant/services/zoe-core/routers/location.py',
    '/home/zoe/assistant/services/zoe-core/routers/media.py',
    '/home/zoe/assistant/services/zoe-core/routers/orchestrator.py',
]

def fix_router(filepath):
    """Fix a single router file"""
    print(f"\n📝 Processing: {Path(filepath).name}")
    
    if not Path(filepath).exists():
        print(f"  ⚠️  File not found, skipping")
        return 0
    
    with open(filepath, 'r') as f:
        content = f.read()
    
    original_content = content
    changes = 0
    
    # Pattern 1: user_id: str = Query("default", ...)
    pattern1 = r'user_id:\s*str\s*=\s*Query\("default"[^)]*\)'
    matches1 = list(re.finditer(pattern1, content))
    if matches1:
        print(f"  Found {len(matches1)} instances of user_id=Query(\"default\")")
        content = re.sub(pattern1, 'session: AuthenticatedSession = Depends(validate_session)', content)
        changes += len(matches1)
    
    # Pattern 2: user_id = Query("default", ...)
    pattern2 = r',\s*user_id\s*=\s*Query\("default"[^)]*\)'
    matches2 = list(re.finditer(pattern2, content))
    if matches2:
        print(f"  Found {len(matches2)} standalone instances")
        content = re.sub(pattern2, ', session: AuthenticatedSession = Depends(validate_session)', content)
        changes += len(matches2)
    
    if content != original_content:
        # Write changes
        with open(filepath, 'w') as f:
            f.write(content)
        
        # Now add user_id = session.user_id to each fixed function
        add_user_id_extraction(filepath)
        
        print(f"  ✅ Made {changes} replacements and added user_id extraction")
        return changes
    else:
        print("  ℹ️  No changes needed")
        return 0

def add_user_id_extraction(filepath):
    """Add user_id = session.user_id after each function with AuthenticatedSession"""
    with open(filepath, 'r') as f:
        lines = f.readlines()
    
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        new_lines.append(line)
        
        # If this line has our AuthenticatedSession dependency
        if 'session: AuthenticatedSession = Depends(validate_session)' in line:
            # Look for the closing ) and opening :
            j = i
            found_function_start = False
            while j < len(lines) and not found_function_start:
                if '):\n' in lines[j] or '):  \n' in lines[j]:
                    # Found function definition end
                    # Now find where to insert user_id
                    k = j + 1
                    # Skip docstring if present
                    if k < len(lines) and '"""' in lines[k]:
                        k += 1
                        while k < len(lines) and '"""' not in lines[k]:
                            k += 1
                        k += 1
                    
                    # Skip empty lines and comments
                    while k < len(lines) and (not lines[k].strip() or lines[k].strip().startswith('#')):
                        k += 1
                    
                    # Check if user_id = session.user_id already exists
                    if k < len(lines) and 'user_id = session.user_id' not in lines[k]:
                        # Get indentation from the next non-empty line
                        indent = '    '
                        if k < len(lines) and lines[k].strip():
                            indent = re.match(r'(\s*)', lines[k]).group(1)
                        
                        # Insert the lines we skipped, then add user_id
                        for idx in range(j + 1, k):
                            if idx < len(new_lines):
                                continue
                            new_lines.append(lines[idx])
                        
                        new_lines.append(f'{indent}user_id = session.user_id\n')
                        i = k - 1
                    found_function_start = True
                j += 1
        
        i += 1
    
    with open(filepath, 'w') as f:
        f.writelines(new_lines)

def main():
    total_changes = 0
    
    print("=" * 80)
    print("🔒 COMPLETING USER ISOLATION SECURITY FIX")
    print("=" * 80)
    
    for router_path in REMAINING_ROUTERS:
        changes = fix_router(router_path)
        total_changes += changes
    
    print("\n" + "=" * 80)
    print(f"✅ Total replacements made: {total_changes}")
    print("=" * 80)
    print("\n✅ All routers processed!")
    print("Next: Restart services and verify functionality\n")

if __name__ == "__main__":
    main()

