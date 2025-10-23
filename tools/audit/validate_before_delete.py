#!/usr/bin/env python3
"""
Validate files before deletion
Prevents accidental deletion of critical files

Usage: python3 validate_before_delete.py file1.py file2.py ...
Exit code: 0 = safe to delete, 1 = BLOCKED
"""
import sys
import json
import subprocess
from pathlib import Path

def load_critical_files():
    """Load critical files manifest"""
    manifest_path = Path(__file__).parent.parent.parent / ".zoe" / "critical-files.json"
    
    if not manifest_path.exists():
        print(f"⚠️  Warning: Critical files manifest not found at {manifest_path}")
        return {}
    
    with open(manifest_path) as f:
        return json.load(f)

def find_references(filepath):
    """Find references to file in codebase"""
    try:
        # Search for imports
        filename = Path(filepath).stem
        result = subprocess.run(
            ["grep", "-r", f"from.*{filename}", ".", "--include=*.py"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent.parent
        )
        imports = result.stdout.strip().split('\n') if result.stdout else []
        
        # Search for direct imports
        result2 = subprocess.run(
            ["grep", "-r", f"import.*{filename}", ".", "--include=*.py"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent.parent
        )
        imports2 = result2.stdout.strip().split('\n') if result2.stdout else []
        
        return [ref for ref in imports + imports2 if ref and not ref.startswith('Binary')]
    except Exception as e:
        print(f"⚠️  Could not search for references: {e}")
        return []

def check_file(filepath, manifest):
    """Check if file is safe to delete"""
    filepath = str(filepath)
    
    # Check critical routers
    if filepath in manifest.get("critical_routers", []):
        return False, "🚫 CRITICAL ROUTER - NEVER DELETE"
    
    # Check critical utilities
    if filepath in manifest.get("critical_utilities", []):
        return False, "🚫 CRITICAL UTILITY - NEVER DELETE"
    
    # Check critical frontend
    if filepath in manifest.get("critical_frontend", []):
        return False, "🚫 CRITICAL FRONTEND FILE - NEVER DELETE"
    
    # Check critical config
    if filepath in manifest.get("critical_config", []):
        return False, "🚫 CRITICAL CONFIG - NEVER DELETE"
    
    # Check patterns
    for pattern in manifest.get("never_delete_patterns", []):
        if Path(filepath).match(pattern):
            return False, f"🚫 MATCHES PROTECTED PATTERN: {pattern}"
    
    # Find references
    refs = find_references(filepath)
    if len(refs) > 3:
        return False, f"⚠️  WARNING: File has {len(refs)} references. Review dependencies first."
    elif len(refs) > 0:
        return True, f"⚠️  CAUTION: File has {len(refs)} reference(s). Consider carefully."
    
    return True, "✅ No critical dependencies found"

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 validate_before_delete.py file1.py file2.py ...")
        sys.exit(1)
    
    manifest = load_critical_files()
    
    blocked = []
    warnings = []
    safe = []
    
    print("=" * 80)
    print("🔍 DELETION SAFETY CHECK")
    print("=" * 80)
    
    for filepath in sys.argv[1:]:
        is_safe, reason = check_file(filepath, manifest)
        
        print(f"\n📄 {filepath}")
        print(f"   {reason}")
        
        if not is_safe:
            blocked.append(filepath)
        elif "WARNING" in reason or "CAUTION" in reason:
            warnings.append(filepath)
        else:
            safe.append(filepath)
    
    print("\n" + "=" * 80)
    print("📊 SUMMARY")
    print("=" * 80)
    print(f"🚫 BLOCKED: {len(blocked)} files")
    print(f"⚠️  WARNINGS: {len(warnings)} files")
    print(f"✅ SAFE: {len(safe)} files")
    
    if blocked:
        print("\n🚫 DELETION BLOCKED - The following files MUST NOT be deleted:")
        for f in blocked:
            print(f"   - {f}")
        print("\nIf you MUST delete these files:")
        print("1. Create a backup branch")
        print("2. Update critical-files.json if file is no longer critical")
        print("3. Test thoroughly")
        print("4. Document why in commit message")
        sys.exit(1)
    
    if warnings:
        print("\n⚠️  PROCEED WITH CAUTION - Review these files carefully:")
        for f in warnings:
            print(f"   - {f}")
        print("\nRecommended:")
        print("1. Check references manually")
        print("2. Test with files removed")
        print("3. Create backup branch first")
    
    print("\n" + "=" * 80)
    sys.exit(0)

if __name__ == "__main__":
    main()









