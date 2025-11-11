#!/usr/bin/env python3
"""
Audit and update all references to old documentation files
"""

import os
import re
from pathlib import Path

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# Old files that were moved or removed
OLD_FILES = [
    "ZOES_CURRENT_STATE.md",
    "SYSTEM_STATUS.md", 
    "FINAL_STATUS_REPORT.md",
    "SYSTEM_REVIEW_FINAL.md",
    "ALL_PHASES_COMPLETE.md",
    "AUTHENTICATION-READY.md",
    "CLEANUP_COMPLETE_SUMMARY.md",
]

# New canonical files
NEW_FILES = {
    "status": "PROJECT_STATUS.md",
    "cleanup": "CLEANUP_SUMMARY.md",
    "fixes": "FIXES_APPLIED.md",
    "plan": "CLEANUP_PLAN.md"
}

def find_doc_references():
    """Find all references to documentation files in code"""
    
    print("🔍 Scanning for documentation references...\n")
    
    references = []
    code_extensions = ['.py', '.js', '.html', '.md', '.sh', '.yaml', '.yml']
    
    for ext in code_extensions:
        for file_path in PROJECT_ROOT.rglob(f"*{ext}"):
            # Skip certain directories
            if any(skip in str(file_path) for skip in [
                'node_modules', '.git', '__pycache__', 'venv', 
                'tools/aider', 'backups', 'docs/archive'
            ]):
                continue
            
            try:
                content = file_path.read_text()
                
                # Look for references to old files
                for old_file in OLD_FILES:
                    if old_file in content:
                        line_nums = [i+1 for i, line in enumerate(content.split('\n')) 
                                   if old_file in line]
                        references.append({
                            'file': str(file_path.relative_to(PROJECT_ROOT)),
                            'old_ref': old_file,
                            'lines': line_nums
                        })
                        
            except Exception as e:
                pass
    
    return references

def find_hardcoded_paths():
    """Find hardcoded paths to documentation"""
    
    print("🔍 Scanning for hardcoded paths...\n")
    
    paths = []
    patterns = [
        r'/home/pi/zoe/[A-Z_]+\.md',
        r'docs/[A-Za-z_]+\.md',
        r'services/zoe-core/routers/archive',
        r'services/zoe-ui/dist/archived'
    ]
    
    for py_file in PROJECT_ROOT.rglob("*.py"):
        if any(skip in str(py_file) for skip in ['__pycache__', 'venv', 'tools/aider', 'backups']):
            continue
            
        try:
            content = py_file.read_text()
            
            for pattern in patterns:
                matches = re.finditer(pattern, content)
                for match in matches:
                    line_num = content[:match.start()].count('\n') + 1
                    paths.append({
                        'file': str(py_file.relative_to(PROJECT_ROOT)),
                        'line': line_num,
                        'path': match.group(0)
                    })
        except:
            pass
    
    return paths

def check_readme_links():
    """Check if README references are current"""
    
    print("🔍 Checking README links...\n")
    
    issues = []
    
    readme = PROJECT_ROOT / "README.md"
    if readme.exists():
        content = readme.read_text()
        
        # Check for links to documentation
        for old_file in OLD_FILES:
            if old_file in content:
                issues.append({
                    'file': 'README.md',
                    'issue': f'References old file: {old_file}',
                    'fix': 'Update to new documentation structure'
                })
    
    return issues

def generate_report(references, paths, readme_issues):
    """Generate audit report"""
    
    print("="*60)
    print("DOCUMENTATION REFERENCES AUDIT REPORT")
    print("="*60)
    print()
    
    if not references and not paths and not readme_issues:
        print("✅ No issues found! All references are up to date.")
        return
    
    if references:
        print(f"⚠️  Found {len(references)} references to old documentation:\n")
        for ref in references:
            print(f"  📄 {ref['file']}")
            print(f"     References: {ref['old_ref']}")
            print(f"     Lines: {', '.join(map(str, ref['lines']))}")
            print()
    
    if paths:
        print(f"⚠️  Found {len(paths)} hardcoded paths:\n")
        for path in paths:
            print(f"  📄 {path['file']}:{path['line']}")
            print(f"     Path: {path['path']}")
            print()
    
    if readme_issues:
        print(f"⚠️  Found {len(readme_issues)} README issues:\n")
        for issue in readme_issues:
            print(f"  📄 {issue['file']}")
            print(f"     Issue: {issue['issue']}")
            print(f"     Fix: {issue['fix']}")
            print()
    
    print("\n" + "="*60)
    print("RECOMMENDED ACTIONS")
    print("="*60)
    print()
    print("1. Update references to point to new files:")
    for old, new in [
        ("ZOES_CURRENT_STATE.md", "PROJECT_STATUS.md"),
        ("SYSTEM_STATUS.md", "PROJECT_STATUS.md"),
        ("CLEANUP_COMPLETE_SUMMARY.md", "CLEANUP_SUMMARY.md")
    ]:
        print(f"   {old} → {new}")
    print()
    print("2. Update hardcoded paths to use new structure")
    print("3. Test all updated references")
    print()

if __name__ == "__main__":
    print("\n" + "="*60)
    print("AUDITING DOCUMENTATION REFERENCES")
    print("="*60 + "\n")
    
    references = find_doc_references()
    paths = find_hardcoded_paths()
    readme_issues = check_readme_links()
    
    generate_report(references, paths, readme_issues)
    
    print("\n✅ Audit complete!\n")

