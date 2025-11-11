#!/usr/bin/env python3
"""
Clean Home Directory - Remove Test/Temp Files from /home/pi
===========================================================

Moves all test scripts, status reports, and temp files from /home/pi 
to appropriate locations in PROJECT_ROOT.
"""

import os
import shutil
from pathlib import Path

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
from datetime import datetime

# Files to keep in /home/pi (system files)
KEEP_IN_HOME = {
    '.bash_history', '.bashrc', '.bash_logout', '.profile', 
    '.gitconfig', '.gitignore', '.lesshst', '.sudo_as_admin_successful',
    '.viminfo', '.inputrc', '.selected_editor', '.wget-hsts'
}

# Categorize files
CATEGORIZATION = {
    # Test scripts
    'test': ['test_*.py', '*_test.py', '*test*.py'],
    # Status reports and summaries
    'status_reports': ['*_STATUS*.md', '*_SUMMARY*.md', '*_REPORT*.md', '*_COMPLETE*.md', '*_READY*.md'],
    # Feature/system documentation
    'feature_docs': ['*_GUIDE*.md', '*_DEMO*.md', '*_DOCUMENTATION*.md', '*CURRENT_STATE*.md'],
    # Config files
    'configs': ['*.conf', '*.yml', '*.yaml', '*-config.yml', 'cloudflared-config.yml'],
    # Scripts
    'scripts': ['*.sh', 'fix_*.py', 'create_*.py', 'update_*.py', 'patch_*.py', 'optimize_*.py', 'demonstrate_*.py', 'migrate_*.py', 'verify_*.py', 'push_*.py'],
    # Test results
    'results': ['*_results.json', '*results*.json', '*test*.json'],
    # Misc project docs
    'misc_docs': ['*.md', '*.txt'],
    # Service files
    'service_files': ['*.service', '*.xml']
}

def clean_home_directory():
    """Clean /home/pi of all test/temp files"""
    home = Path('/home/pi')
    # Auto-detect project root (works for both Pi and Nano)
    zoe_root = Path(__file__).parent.parent.parent.resolve()
    
    # Create timestamp for archive
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    moves = {
        'test': [],
        'status_reports': [],
        'feature_docs': [],
        'configs': [],
        'scripts': [],
        'results': [],
        'misc_docs': [],
        'service_files': [],
        'deleted': []
    }
    
    # Get all files in home directory (not hidden)
    all_files = [f for f in home.iterdir() if f.is_file() and not f.name.startswith('.')]
    
    print(f"📁 Found {len(all_files)} files in /home/pi")
    print("🔍 Categorizing...\n")
    
    for file in all_files:
        if file.name in KEEP_IN_HOME:
            continue
        
        # Categorize
        moved = False
        
        # Test scripts
        if any(file.name.startswith(prefix) for prefix in ['test_', 'achieve_', 'analyze_', 'comprehensive_']):
            if file.suffix == '.py':
                target = zoe_root / 'tests' / 'archive' / f"{timestamp}"
                target.mkdir(parents=True, exist_ok=True)
                moves['test'].append((file, target / file.name))
                moved = True
        
        # Status reports
        if not moved and file.suffix == '.md':
            if any(keyword in file.name.upper() for keyword in ['STATUS', 'SUMMARY', 'REPORT', 'COMPLETE', 'READY', 'DONE', 'FIXED']):
                target = zoe_root / 'docs' / 'archive' / 'reports' / f"{timestamp}"
                target.mkdir(parents=True, exist_ok=True)
                moves['status_reports'].append((file, target / file.name))
                moved = True
            
            # Feature docs
            elif any(keyword in file.name.upper() for keyword in ['GUIDE', 'DEMO', 'DOCUMENTATION', 'CURRENT_STATE', 'INTEGRATION', 'ENHANCEMENT', 'SYSTEM', 'ROADMAP']):
                target = zoe_root / 'docs' / 'archive' / 'technical' / f"{timestamp}"
                target.mkdir(parents=True, exist_ok=True)
                moves['feature_docs'].append((file, target / file.name))
                moved = True
            
            # Other misc docs
            elif file.name not in ['README.md', 'CHANGELOG.md']:
                target = zoe_root / 'docs' / 'archive' / 'misc' / f"{timestamp}"
                target.mkdir(parents=True, exist_ok=True)
                moves['misc_docs'].append((file, target / file.name))
                moved = True
        
        # Config files
        if not moved and file.suffix in ['.conf', '.yml', '.yaml']:
            target = zoe_root / 'config' / 'archive' / f"{timestamp}"
            target.mkdir(parents=True, exist_ok=True)
            moves['configs'].append((file, target / file.name))
            moved = True
        
        # Scripts
        if not moved and (file.suffix == '.sh' or 
                         any(file.name.startswith(prefix) for prefix in ['fix_', 'create_', 'update_', 'patch_', 'optimize_', 'migrate_', 'verify_', 'push_', 'demonstrate_'])):
            target = zoe_root / 'scripts' / 'utilities' / 'archive' / f"{timestamp}"
            target.mkdir(parents=True, exist_ok=True)
            moves['scripts'].append((file, target / file.name))
            moved = True
        
        # Test results
        if not moved and file.suffix == '.json':
            target = zoe_root / 'tests' / 'results' / f"{timestamp}"
            target.mkdir(parents=True, exist_ok=True)
            moves['results'].append((file, target / file.name))
            moved = True
        
        # Service files
        if not moved and file.suffix in ['.service', '.xml']:
            target = zoe_root / 'config' / 'archive' / f"{timestamp}"
            target.mkdir(parents=True, exist_ok=True)
            moves['service_files'].append((file, target / file.name))
            moved = True
    
    # Show what will be moved
    print("📦 PLANNED MOVES:")
    for category, files in moves.items():
        if files and category != 'deleted':
            print(f"\n{category.replace('_', ' ').title()} ({len(files)} files):")
            for src, dst in files[:5]:  # Show first 5
                print(f"  {src.name} → {dst.relative_to(zoe_root)}")
            if len(files) > 5:
                print(f"  ... and {len(files) - 5} more")
    
    # Execute moves
    print("\n\n🚀 EXECUTING CLEANUP...")
    total_moved = 0
    
    for category, files in moves.items():
        for src, dst in files:
            try:
                shutil.move(str(src), str(dst))
                total_moved += 1
            except Exception as e:
                print(f"  ❌ Error moving {src.name}: {e}")
    
    print(f"\n✅ CLEANUP COMPLETE!")
    print(f"📊 Total files moved: {total_moved}")
    print(f"📁 Files remaining in /home/pi: {len([f for f in home.iterdir() if f.is_file() and not f.name.startswith('.')])}")
    
    return total_moved

if __name__ == "__main__":
    total = clean_home_directory()
    print(f"\n🎯 /home/pi is now clean!")
    print(f"💾 All files archived in PROJECT_ROOT with timestamp for recovery")

