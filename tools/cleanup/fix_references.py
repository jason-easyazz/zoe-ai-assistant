#!/usr/bin/env python3
"""
Automatically fix all references to old documentation files
"""

from pathlib import Path
import re

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# Mapping of old files to new files
REPLACEMENTS = {
    "ZOES_CURRENT_STATE.md": "PROJECT_STATUS.md",
    "SYSTEM_STATUS.md": "PROJECT_STATUS.md",
    "FINAL_STATUS_REPORT.md": "docs/archive/reports/FINAL_STATUS_REPORT.md",
    "SYSTEM_REVIEW_FINAL.md": "docs/archive/reports/SYSTEM_REVIEW_FINAL.md",
    "ALL_PHASES_COMPLETE.md": "docs/archive/reports/ALL_PHASES_COMPLETE.md",
    "AUTHENTICATION-READY.md": "docs/archive/reports/AUTHENTICATION-READY.md",
    "CLEANUP_COMPLETE_SUMMARY.md": "CLEANUP_SUMMARY.md",
    "SYSTEM_OPTIMIZATION_REPORT.md": "docs/archive/reports/SYSTEM_OPTIMIZATION_REPORT.md",
}

# Files to update (excluding the audit/fix scripts themselves)
FILES_TO_UPDATE = [
    "CLEANUP_PLAN.md",
    "SECURITY_QUICKSTART.md",
    "EVERYTHING_DONE.md",
    "CHANGELOG.md",
    "documentation/DOCUMENTATION_INDEX.md",
    "README.md"
]

def fix_file(file_path):
    """Fix references in a single file"""
    
    if not file_path.exists():
        return False
    
    try:
        content = file_path.read_text()
        original = content
        
        # Replace old references with new ones
        for old, new in REPLACEMENTS.items():
            if old in content:
                content = content.replace(old, new)
                print(f"  ✓ Replaced {old} → {new}")
        
        # Only write if changes were made
        if content != original:
            file_path.write_text(content)
            return True
        
        return False
        
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return False

def update_readme():
    """Update README.md to reference new structure"""
    
    readme = PROJECT_ROOT / "README.md"
    if not readme.exists():
        return
    
    print("\n📝 Updating README.md...")
    
    content = readme.read_text()
    
    # Update documentation section if it exists
    doc_section_old = """### Getting Started
- [**CHANGELOG.md**](CHANGELOG.md) - Version history & new features
- [**FEATURE_INTEGRATION_GUIDE.md**](FEATURE_INTEGRATION_GUIDE.md) - How features work together
- [**EVERYTHING_DONE.md**](EVERYTHING_DONE.md) - Complete implementation status"""

    doc_section_new = """### Getting Started
- [**CHANGELOG.md**](CHANGELOG.md) - Version history & new features  
- [**PROJECT_STATUS.md**](PROJECT_STATUS.md) - Current system status (consolidated)
- [**QUICK-START.md**](QUICK-START.md) - How to start and use Zoe"""

    if doc_section_old in content:
        content = content.replace(doc_section_old, doc_section_new)
        readme.write_text(content)
        print("  ✓ Updated documentation section")

def update_documentation_index():
    """Update documentation/DOCUMENTATION_INDEX.md"""
    
    doc_index = PROJECT_ROOT / "documentation" / "DOCUMENTATION_INDEX.md"
    if not doc_index.exists():
        return
    
    print("\n📝 Updating documentation/DOCUMENTATION_INDEX.md...")
    
    # Create updated index
    new_content = """# Zoe Documentation Index

## 📚 Core Documentation (Project Root)

### Essential Reading
- **README.md** - Project overview and features
- **QUICK-START.md** - How to start and use Zoe  
- **PROJECT_STATUS.md** - ⭐ Current system status (consolidated)
- **CHANGELOG.md** - Version history and updates

### Technical Documentation  
- **FIXES_APPLIED.md** - Recent bug fixes and improvements
- **CLEANUP_PLAN.md** - Project maintenance procedures
- **CLEANUP_SUMMARY.md** - Comprehensive cleanup report

### Specialized Documentation
- **SECURITY_QUICKSTART.md** - Security features and setup
- **EVERYTHING_DONE.md** - Implementation status
- **FEATURE_INTEGRATION_GUIDE.md** - How features work together

---

## 📁 Organized Documentation (`/docs/`)

### `/docs/archive/`
Historical documentation and old status reports:
- **archive/reports/** - System reports, phase completions, status docs
- **archive/technical/** - Technical docs, styling updates, debug files
- **archive/guides/** - Old integration guides and documentation

### `/docs/guides/`
User and developer guides (coming soon)

### `/docs/api/`
API documentation (coming soon)

---

## 🔗 Quick Links

### For New Users
1. Start: **README.md** - What is Zoe?
2. Setup: **QUICK-START.md** - How to use  
3. Status: **PROJECT_STATUS.md** - Current capabilities

### For Developers
1. Architecture: **PROJECT_STATUS.md** - System overview
2. Recent Changes: **FIXES_APPLIED.md**
3. Maintenance: **CLEANUP_PLAN.md**
4. API Docs: http://localhost:8000/docs

### For Troubleshooting
1. Check: **PROJECT_STATUS.md** - Known issues
2. Run: `python3 comprehensive_audit.py`
3. Logs: `docker logs zoe-core --tail 50`

---

## 🛠️ Maintenance Tools

- **comprehensive_audit.py** - Full system health check
- **comprehensive_cleanup.py** - Automated cleanup analysis  
- **consolidate_docs.py** - Documentation organizer
- **fix_references.py** - Update documentation references

---

## 📝 Documentation Guidelines

### When Adding New Docs
1. **Current/Active**: Keep in project root
2. **Historical**: Move to `/docs/archive/` when superseded
3. **Guides**: Place in `/docs/guides/`
4. **API Docs**: Place in `/docs/api/`

### Naming Conventions
- Use `PROJECT_STATUS.md` as single source of truth for status
- Date old docs when archiving: `STATUS_20251008.md`
- Use clear, descriptive names: `FEATURE_NAME_GUIDE.md`

---

*Last Updated: October 8, 2025*  
*This index tracks all Zoe documentation across the project.*
"""
    
    doc_index.write_text(new_content)
    print("  ✓ Completely rewrote with new structure")

def create_docs_index():
    """Create comprehensive docs index in docs/README.md"""
    
    docs_readme = PROJECT_ROOT / "docs" / "README.md"
    
    content = """# Zoe Documentation

## 📖 Main Documentation (Project Root)

For current, active documentation, see the project root:

- **README.md** - Project overview and features
- **QUICK-START.md** - How to start and use Zoe
- **PROJECT_STATUS.md** - ⭐ Current system status (consolidated)
- **CHANGELOG.md** - Version history
- **FIXES_APPLIED.md** - Recent technical fixes  
- **CLEANUP_PLAN.md** - Maintenance procedures
- **CLEANUP_SUMMARY.md** - Project cleanup report

---

## 📁 This Folder (`/docs/`)

### `/docs/archive/`
Historical documentation organized by category:

**Reports** (`archive/reports/`)
- System status reports
- Phase completion docs
- Integration reports  
- Test results

**Technical** (`archive/technical/`)
- API fixes
- Styling updates
- Debug documentation
- Technical specifications

**Guides** (`archive/guides/`)
- Old integration guides
- Installation docs
- Feature documentation

### `/docs/guides/` (Future)
User and developer guides will be placed here

### `/docs/api/` (Future)
API documentation will be placed here

---

## 🎯 Quick Reference

### I Want To...

**Understand Zoe**: Read `../README.md`
**Start Using Zoe**: Read `../QUICK-START.md`
**See Current Status**: Read `../PROJECT_STATUS.md`
**Find Old Reports**: Check `archive/reports/`
**Find Technical Docs**: Check `archive/technical/`
**Troubleshoot**: Run `python3 ../comprehensive_audit.py`

---

## 🔍 Finding Documentation

### By Date
All archived docs are organized chronologically within their categories.

### By Type
- **Status**: `archive/reports/`
- **Technical**: `archive/technical/`  
- **Guides**: `archive/guides/`

### By Topic
Use grep to search: `grep -r "your topic" archive/`

---

## 📝 Documentation Guidelines

### Current vs Archived
- **Current**: Project root (active, maintained)
- **Archived**: `/docs/archive/` (historical reference)

### When to Archive
- When a new status doc supersedes an old one
- When features are deprecated
- When guides are rewritten

### Don't Archive
- Current active documentation
- Frequently referenced guides
- API documentation

---

*For the latest information, always check PROJECT_STATUS.md in the project root.*
"""
    
    docs_readme.write_text(content)
    print("  ✓ Created comprehensive docs/README.md")

if __name__ == "__main__":
    print("\n" + "="*60)
    print("FIXING DOCUMENTATION REFERENCES")
    print("="*60 + "\n")
    
    fixed_count = 0
    
    # Fix individual files
    for file_name in FILES_TO_UPDATE:
        file_path = PROJECT_ROOT / file_name
        if file_path.exists():
            print(f"\n📝 Updating {file_name}...")
            if fix_file(file_path):
                fixed_count += 1
        else:
            print(f"\n⚠️  {file_name} not found (may have been archived)")
    
    # Update README
    update_readme()
    
    # Update documentation index
    update_documentation_index()
    
    # Create/update docs/README.md
    create_docs_index()
    
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"\n✅ Updated {fixed_count} files")
    print("✅ Updated README.md")
    print("✅ Updated documentation/DOCUMENTATION_INDEX.md")
    print("✅ Created docs/README.md")
    print("\n🎉 All references updated to new structure!\n")

