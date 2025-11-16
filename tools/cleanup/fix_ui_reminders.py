#!/usr/bin/env python3
"""
Fix UI pages by removing loadReminders and loadNotifications calls
since these functions are not implemented properly
"""

import re
from pathlib import Path

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
ui_dir = PROJECT_ROOT / "services/zoe-ui/dist"

# Pages that reference reminders
pages_to_fix = [
    "memories.html",
    "dashboard.html",
    "journal.html",
    "settings.html",
    "workflows.html",
    "calendar.html",
    "lists.html"
]

for page_name in pages_to_fix:
    page_path = ui_dir / page_name
    if not page_path.exists():
        print(f"⚠ {page_name} not found")
        continue
    
    try:
        content = page_path.read_text()
        original_content = content
        
        # Remove loadReminders function definition
        content = re.sub(
            r'async function loadReminders\([^)]*\)[^{]*{[^}]*}',
            '// loadReminders function removed - not implemented',
            content,
            flags=re.DOTALL
        )
        
        # Remove loadNotifications function definition
        content = re.sub(
            r'async function loadNotifications\([^)]*\)[^{]*{[^}]*}',
            '// loadNotifications function removed - not implemented',
            content,
            flags=re.DOTALL
        )
        
        # Remove calls to loadReminders
        content = re.sub(
            r'loadReminders\([^)]*\);?',
            '// loadReminders() call removed',
            content
        )
        
        # Remove calls to loadNotifications
        content = re.sub(
            r'loadNotifications\([^)]*\);?',
            '// loadNotifications() call removed',
            content
        )
        
        # Remove await loadReminders
        content = re.sub(
            r'await\s+loadReminders\([^)]*\);?',
            '// await loadReminders() removed',
            content
        )
        
        # Remove await loadNotifications
        content = re.sub(
            r'await\s+loadNotifications\([^)]*\);?',
            '// await loadNotifications() removed',
            content
        )
        
        if content != original_content:
            page_path.write_text(content)
            print(f"✓ Fixed {page_name}")
        else:
            print(f"• {page_name} - no changes needed")
            
    except Exception as e:
        print(f"✗ Error fixing {page_name}: {e}")

print("\n✓ UI pages cleaned!")

