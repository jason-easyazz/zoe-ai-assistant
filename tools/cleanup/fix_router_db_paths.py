#!/usr/bin/env python3
"""
Mass fix for router database paths
Updates all routers to use os.getenv("DATABASE_PATH") instead of hardcoded paths
"""

import os
import re
from pathlib import Path

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

def fix_router_file(filepath: Path) -> bool:
    """Fix a single router file's database paths"""
    
    print(f"\n📄 Processing: {filepath.name}")
    
    content = filepath.read_text()
    original_content = content
    modified = False
    
    # Check if os is already imported
    has_os_import = re.search(r'^import os$', content, re.MULTILINE)
    
    # Pattern 1: DB_PATH = "/app/data/zoe.db" or "/home/pi/zoe/data/zoe.db" or any hardcoded path
    pattern1 = r'(DB_PATH|DATABASE_PATH|MEMORY_DB_PATH|LEGACY_DB_PATH)\s*=\s*"(/app/data/|/home/zoe/assistant/data/|/home/zoe/assistant/data/)[^"]*\.db"'
    matches1 = list(re.finditer(pattern1, content))
    
    if matches1:
        for match in matches1:
            var_name = match.group(1)
            old_line = match.group(0)
            
            # Replace with os.getenv version
            if "memory.db" in old_line:
                new_line = f'{var_name} = os.getenv("MEMORY_DB_PATH", "/app/data/memory.db")'
            else:
                new_line = f'{var_name} = os.getenv("DATABASE_PATH", "/app/data/zoe.db")'
            
            print(f"  ✏️  {old_line}")
            print(f"  ✅  {new_line}")
            
            content = content.replace(old_line, new_line)
            modified = True
    
    # Add os import if needed and modifications were made
    if modified and not has_os_import:
        # Find the first import statement and add os import after it
        import_match = re.search(r'^(from .+ import .+|import .+)$', content, re.MULTILINE)
        if import_match:
            first_import = import_match.group(0)
            # Add os import after the first import
            content = content.replace(first_import, f"{first_import}\nimport os", 1)
            print(f"  ➕ Added: import os")
    
    if modified:
        filepath.write_text(content)
        print(f"  ✅ Saved changes")
        return True
    else:
        print(f"  ⏭️  No changes needed")
        return False

def main():
    """Fix all routers in the routers directory"""
    
    routers_dir = PROJECT_ROOT / "services/zoe-core/routers"
    
    print("=" * 70)
    print("🔧 MASS FIX: Router Database Paths")
    print("=" * 70)
    print(f"Directory: {routers_dir}")
    print()
    
    # Find all Python files
    router_files = list(routers_dir.glob("*.py"))
    router_files = [f for f in router_files if f.name != "__init__.py"]
    
    print(f"Found {len(router_files)} router files")
    print()
    
    fixed_count = 0
    skipped_count = 0
    
    for router_file in sorted(router_files):
        if fix_router_file(router_file):
            fixed_count += 1
        else:
            skipped_count += 1
    
    print()
    print("=" * 70)
    print("📊 SUMMARY")
    print("=" * 70)
    print(f"✅ Fixed: {fixed_count} files")
    print(f"⏭️  Skipped: {skipped_count} files (no changes needed)")
    print(f"📁 Total: {len(router_files)} files")
    print()
    
    if fixed_count > 0:
        print("🔄 Next steps:")
        print("  1. Restart zoe-core: docker restart zoe-core")
        print("  2. Restart nginx: docker restart zoe-ui")
        print("  3. Test endpoints")
        print()

if __name__ == "__main__":
    main()




