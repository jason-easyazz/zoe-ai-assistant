#!/usr/bin/env python3
"""
Comprehensive Root Directory Cleanup

Analyzes and organizes ALL files in the root directory according to project rules.
"""

import os
import json
from pathlib import Path

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
from datetime import datetime

class RootCleaner:
    def __init__(self, root_path=None):
        self.root = Path(root_path)
        self.report = {
            "scripts_to_move": [],
            "configs_to_move": [],
            "temp_files_to_delete": [],
            "tests_to_move": [],
            "kept_in_root": [],
            "unknown": []
        }
    
    def analyze(self):
        """Analyze all files in root"""
        for item in self.root.iterdir():
            if item.is_dir():
                # Directories are OK
                continue
            
            filename = item.name
            
            # Skip allowed files in root
            if filename in [
                'README.md', 'CHANGELOG.md', 'QUICK-START.md', 'PROJECT_STATUS.md',
                'GOVERNANCE.md', 'PROJECT_STRUCTURE_RULES.md', 'MAINTENANCE.md', 'FIXES_APPLIED.md',
                'docker-compose.yml', 'docker-compose.mem-agent.yml', 'pytest.ini',
                'verify_updates.sh', '.gitignore', '.env', '.cursorrules',
                'test_architecture.py'  # Only allowed test
            ]:
                self.report["kept_in_root"].append(filename)
                continue
            
            # Python scripts → tools/ or scripts/
            if filename.endswith('.py'):
                if 'test' in filename.lower():
                    self.report["tests_to_move"].append({
                        "file": filename,
                        "dest": "tests/unit/"
                    })
                elif any(word in filename for word in ['analyze', 'audit', 'enforce']):
                    self.report["scripts_to_move"].append({
                        "file": filename,
                        "dest": "tools/audit/"
                    })
                elif any(word in filename for word in ['cleanup', 'migrate', 'fix', 'update', 'organize']):
                    self.report["scripts_to_move"].append({
                        "file": filename,
                        "dest": "tools/cleanup/"
                    })
                elif any(word in filename for word in ['benchmark', 'comprehensive', 'demonstrate']):
                    self.report["scripts_to_move"].append({
                        "file": filename,
                        "dest": "scripts/utilities/"
                    })
                elif 'create_' in filename:
                    self.report["scripts_to_move"].append({
                        "file": filename,
                        "dest": "tools/generators/"
                    })
                else:
                    self.report["scripts_to_move"].append({
                        "file": filename,
                        "dest": "scripts/utilities/"
                    })
            
            # Config files → config/
            elif filename.endswith(('.yml', '.yaml', '.conf')) and filename != 'docker-compose.yml':
                self.report["configs_to_move"].append({
                    "file": filename,
                    "dest": "config/"
                })
            
            # Temp/backup files → DELETE
            elif filename.endswith(('.tmp', '.backup', '.old', '.cache')) or 'temp' in filename.lower():
                self.report["temp_files_to_delete"].append(filename)
            
            # JSON result files → tests/ or archive
            elif filename.endswith('.json') and any(word in filename for word in ['test', 'result', 'report', 'analysis']):
                self.report["temp_files_to_delete"].append(filename)
            
            # SQL files → tools/
            elif filename.endswith('.sql'):
                self.report["scripts_to_move"].append({
                    "file": filename,
                    "dest": "tools/generators/"
                })
            
            # Text files
            elif filename.endswith('.txt'):
                if 'prompt' in filename.lower() or 'continuation' in filename.lower():
                    self.report["scripts_to_move"].append({
                        "file": filename,
                        "dest": "docs/developer/"
                    })
                else:
                    self.report["unknown"].append(filename)
            
            # PEM files (certificates)
            elif filename.endswith('.pem'):
                self.report["configs_to_move"].append({
                    "file": filename,
                    "dest": "ssl/"
                })
            
            else:
                self.report["unknown"].append(filename)
    
    def print_report(self):
        """Print analysis report"""
        print("\n" + "="*70)
        print("🔍 ROOT DIRECTORY CLEANUP ANALYSIS")
        print("="*70 + "\n")
        
        print(f"✅ Files that should STAY in root ({len(self.report['kept_in_root'])}):")
        for f in sorted(self.report['kept_in_root'])[:10]:
            print(f"   • {f}")
        if len(self.report['kept_in_root']) > 10:
            print(f"   ... and {len(self.report['kept_in_root']) - 10} more")
        
        print(f"\n📜 Python scripts to move ({len(self.report['scripts_to_move'])}):")
        for item in self.report['scripts_to_move'][:10]:
            print(f"   • {item['file']} → {item['dest']}")
        if len(self.report['scripts_to_move']) > 10:
            print(f"   ... and {len(self.report['scripts_to_move']) - 10} more")
        
        print(f"\n⚙️  Config files to move ({len(self.report['configs_to_move'])}):")
        for item in self.report['configs_to_move'][:10]:
            print(f"   • {item['file']} → {item['dest']}")
        
        print(f"\n🗑️  Temp files to DELETE ({len(self.report['temp_files_to_delete'])}):")
        for f in self.report['temp_files_to_delete'][:10]:
            print(f"   • {f}")
        
        print(f"\n❓ Unknown files ({len(self.report['unknown'])}):")
        for f in self.report['unknown']:
            print(f"   • {f}")
        
        print("\n" + "="*70)
        print(f"📊 TOTAL FILES TO ORGANIZE: {len(self.report['scripts_to_move']) + len(self.report['configs_to_move']) + len(self.report['tests_to_move']) + len(self.report['temp_files_to_delete'])}")
        print("="*70 + "\n")
        
        return self.report
    
    def execute(self, dry_run=False):
        """Execute the cleanup"""
        if dry_run:
            print("🏃 DRY RUN MODE - No files will be moved\n")
            return
        
        print("\n🚀 EXECUTING CLEANUP...\n")
        
        # Move scripts
        for item in self.report['scripts_to_move']:
            src = self.root / item['file']
            dest_dir = self.root / item['dest']
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / item['file']
            if src.exists():
                src.rename(dest)
                print(f"✅ Moved: {item['file']} → {item['dest']}")
        
        # Move configs
        for item in self.report['configs_to_move']:
            src = self.root / item['file']
            dest_dir = self.root / item['dest']
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / item['file']
            if src.exists():
                src.rename(dest)
                print(f"✅ Moved: {item['file']} → {item['dest']}")
        
        # Move tests
        for item in self.report['tests_to_move']:
            src = self.root / item['file']
            dest_dir = self.root / item['dest']
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / item['file']
            if src.exists():
                src.rename(dest)
                print(f"✅ Moved: {item['file']} → {item['dest']}")
        
        # Delete temp files
        for filename in self.report['temp_files_to_delete']:
            src = self.root / filename
            if src.exists():
                src.unlink()
                print(f"🗑️  Deleted: {filename}")
        
        print("\n✅ CLEANUP COMPLETE!")

if __name__ == "__main__":
    import sys
    
    cleaner = RootCleaner()
    cleaner.analyze()
    report = cleaner.print_report()
    
    if '--execute' in sys.argv:
        cleaner.execute(dry_run=False)
    else:
        print("ℹ️  This was a DRY RUN. Add --execute to actually move files.")
        print("   Example: python3 tools/cleanup/comprehensive_root_cleanup.py --execute")

