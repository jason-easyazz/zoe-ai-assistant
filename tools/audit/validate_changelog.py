#!/usr/bin/env python3
"""
Validate CHANGELOG.md is properly maintained
"""
import sys
from pathlib import Path
from datetime import datetime

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
CHANGELOG = PROJECT_ROOT / "CHANGELOG.md"

def validate_changelog():
    """Ensure CHANGELOG exists and is updated"""
    
    if not CHANGELOG.exists():
        print("❌ CHANGELOG.md not found!")
        return False
    
    # Read changelog
    content = CHANGELOG.read_text()
    
    # Check for proper format
    if "# Changelog" not in content and "# CHANGELOG" not in content:
        print("❌ CHANGELOG.md missing proper header")
        return False
    
    # Check for version section
    if not content.strip():
        print("❌ CHANGELOG.md is empty")
        return False
    
    # Check for unreleased section or recent version
    current_year = datetime.now().year
    if str(current_year) not in content:
        print(f"⚠️  WARNING: CHANGELOG has no entries from {current_year}")
        print("   Consider adding recent changes to CHANGELOG.md")
        # Don't fail, just warn
        return True
    
    print("✓ CHANGELOG.md is present and has recent content")
    return True

if __name__ == "__main__":
    success = validate_changelog()
    sys.exit(0 if success else 1)

