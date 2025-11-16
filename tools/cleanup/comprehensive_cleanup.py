#!/usr/bin/env python3
"""
Comprehensive Zoe Project Cleanup Script
Analyzes and cleans up the entire project structure
"""

import os
import shutil
from pathlib import Path
from datetime import datetime
import json

# Colors
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

class ProjectCleaner:
    def __init__(self):
        self.to_remove = []
        self.to_consolidate = []
        self.report = {
            "timestamp": datetime.now().isoformat(),
            "files_analyzed": 0,
            "files_to_remove": 0,
            "space_to_free": 0,
            "categories": {}
        }
    
    def analyze_project(self):
        """Analyze entire project structure"""
        print(f"\n{BLUE}{'='*60}{RESET}")
        print(f"{BLUE}PROJECT STRUCTURE ANALYSIS{RESET}")
        print(f"{BLUE}{'='*60}{RESET}\n")
        
        self.find_backup_files()
        self.find_duplicate_routers()
        self.find_temp_files()
        self.find_old_documentation()
        self.find_mac_files()
        self.find_archive_folders()
        self.find_broken_files()
        
        return self.report
    
    def find_backup_files(self):
        """Find all backup files"""
        print(f"{YELLOW}🔍 Scanning for backup files...{RESET}")
        
        patterns = ['*.backup', '*.bak', '*_backup*', '*-backup*', '*.old', '*_old*']
        backups = []
        
        for pattern in patterns:
            backups.extend(PROJECT_ROOT.rglob(pattern))
        
        # Also check for dated backups
        for f in PROJECT_ROOT.rglob("*"):
            if f.is_file():
                name = f.name
                if any(x in name.lower() for x in ['-20250', '-20240', '_20250', '_20240']):
                    if not any(skip in str(f) for skip in ['node_modules', '.git', '__pycache__']):
                        backups.append(f)
        
        backups = list(set(backups))  # Remove duplicates
        
        for f in backups:
            size = f.stat().st_size
            self.to_remove.append({
                "path": str(f),
                "reason": "backup_file",
                "size": size
            })
        
        print(f"  Found {GREEN}{len(backups)}{RESET} backup files")
        self.report["categories"]["backups"] = len(backups)
    
    def find_duplicate_routers(self):
        """Find duplicate router files in archive folder"""
        print(f"{YELLOW}🔍 Scanning for duplicate routers...{RESET}")
        
        router_path = PROJECT_ROOT / "services/zoe-core/routers"
        archive_path = router_path / "archive"
        
        if not archive_path.exists():
            print(f"  {GREEN}No archive folder{RESET}")
            return
        
        archive_files = list(archive_path.glob("*.py"))
        
        for f in archive_files:
            size = f.stat().st_size
            self.to_remove.append({
                "path": str(f),
                "reason": "archived_router",
                "size": size
            })
        
        print(f"  Found {GREEN}{len(archive_files)}{RESET} archived router files")
        self.report["categories"]["archived_routers"] = len(archive_files)
    
    def find_temp_files(self):
        """Find temporary files"""
        print(f"{YELLOW}🔍 Scanning for temp files...{RESET}")
        
        temp_patterns = ['*.tmp', '*.temp', '*.cache', '*.log']
        temp_files = []
        
        for pattern in temp_patterns:
            temp_files.extend(PROJECT_ROOT.rglob(pattern))
        
        # Exclude certain directories
        temp_files = [f for f in temp_files if not any(
            skip in str(f) for skip in ['node_modules', '.git', '__pycache__', 'venv', 'logs']
        )]
        
        for f in temp_files:
            if f.is_file():
                size = f.stat().st_size
                self.to_remove.append({
                    "path": str(f),
                    "reason": "temp_file",
                    "size": size
                })
        
        print(f"  Found {GREEN}{len(temp_files)}{RESET} temp files")
        self.report["categories"]["temp_files"] = len(temp_files)
    
    def find_old_documentation(self):
        """Find outdated/redundant documentation"""
        print(f"{YELLOW}🔍 Scanning documentation...{RESET}")
        
        docs = list(PROJECT_ROOT.glob("*.md"))
        
        # Categorize docs
        important_docs = [
            "README.md", "CHANGELOG.md", "QUICK-START.md",
            "FIXES_APPLIED.md", "CLEANUP_COMPLETE_SUMMARY.md"
        ]
        
        redundant = []
        for doc in docs:
            if doc.name not in important_docs:
                # Check if it's a status/progress/complete type doc
                name_lower = doc.name.lower()
                if any(x in name_lower for x in [
                    '_complete', '_status', '_progress', '_ready',
                    '_report', '_summary', '_final'
                ]):
                    redundant.append(doc)
        
        for doc in redundant:
            size = doc.stat().st_size
            self.to_consolidate.append({
                "path": str(doc),
                "reason": "redundant_doc",
                "size": size
            })
        
        print(f"  Found {GREEN}{len(redundant)}{RESET} potentially redundant docs")
        print(f"  Total docs: {len(docs)}, Important: {len(important_docs)}")
        self.report["categories"]["redundant_docs"] = len(redundant)
    
    def find_mac_files(self):
        """Find Mac OS resource fork files"""
        print(f"{YELLOW}🔍 Scanning for Mac OS files...{RESET}")
        
        mac_files = []
        for f in PROJECT_ROOT.rglob("._*"):
            if f.is_file():
                mac_files.append(f)
        
        for f in PROJECT_ROOT.rglob(".DS_Store"):
            if f.is_file():
                mac_files.append(f)
        
        for f in mac_files:
            size = f.stat().st_size
            self.to_remove.append({
                "path": str(f),
                "reason": "mac_system_file",
                "size": size
            })
        
        print(f"  Found {GREEN}{len(mac_files)}{RESET} Mac system files")
        self.report["categories"]["mac_files"] = len(mac_files)
    
    def find_archive_folders(self):
        """Find archive folders that can be removed"""
        print(f"{YELLOW}🔍 Scanning archive folders...{RESET}")
        
        archives = []
        for f in PROJECT_ROOT.rglob("archive"):
            if f.is_dir():
                archives.append(f)
        
        for f in PROJECT_ROOT.rglob("archived"):
            if f.is_dir():
                archives.append(f)
        
        print(f"  Found {GREEN}{len(archives)}{RESET} archive folders")
        
        for archive in archives:
            # Calculate total size
            total_size = sum(f.stat().st_size for f in archive.rglob('*') if f.is_file())
            self.to_remove.append({
                "path": str(archive),
                "reason": "archive_folder",
                "size": total_size
            })
        
        self.report["categories"]["archive_folders"] = len(archives)
    
    def find_broken_files(self):
        """Find files that are known to be broken"""
        print(f"{YELLOW}🔍 Scanning for broken files...{RESET}")
        
        ui_dist = PROJECT_ROOT / "services/zoe-ui/dist"
        broken_files = []
        
        # From audit: ._agui_chat_html.html is corrupted
        corrupted = ui_dist / "._agui_chat_html.html"
        if corrupted.exists():
            broken_files.append(corrupted)
        
        # Check for .broken files
        for f in ui_dist.rglob("*.broken"):
            broken_files.append(f)
        
        for f in broken_files:
            if f.exists():
                size = f.stat().st_size
                self.to_remove.append({
                    "path": str(f),
                    "reason": "broken_file",
                    "size": size
                })
        
        print(f"  Found {GREEN}{len(broken_files)}{RESET} broken files")
        self.report["categories"]["broken_files"] = len(broken_files)
    
    def generate_report(self):
        """Generate cleanup report"""
        print(f"\n{BLUE}{'='*60}{RESET}")
        print(f"{BLUE}CLEANUP ANALYSIS REPORT{RESET}")
        print(f"{BLUE}{'='*60}{RESET}\n")
        
        total_files = len(self.to_remove) + len(self.to_consolidate)
        total_size = sum(item['size'] for item in self.to_remove + self.to_consolidate)
        
        print(f"📊 SUMMARY:")
        print(f"  • Files to remove: {RED}{len(self.to_remove)}{RESET}")
        print(f"  • Files to consolidate: {YELLOW}{len(self.to_consolidate)}{RESET}")
        print(f"  • Total space to free: {GREEN}{self.format_size(total_size)}{RESET}")
        
        print(f"\n📁 BY CATEGORY:")
        for category, count in self.report["categories"].items():
            print(f"  • {category.replace('_', ' ').title()}: {count}")
        
        print(f"\n{RED}FILES TO REMOVE:{RESET}")
        by_reason = {}
        for item in self.to_remove:
            reason = item['reason']
            if reason not in by_reason:
                by_reason[reason] = []
            by_reason[reason].append(item)
        
        for reason, items in by_reason.items():
            print(f"\n  {reason.replace('_', ' ').title()} ({len(items)} files):")
            for item in items[:5]:  # Show first 5
                rel_path = item['path'].replace(str(PROJECT_ROOT), '')
                print(f"    - {rel_path}")
            if len(items) > 5:
                print(f"    ... and {len(items) - 5} more")
        
        self.report["files_to_remove"] = len(self.to_remove)
        self.report["space_to_free"] = total_size
        
        # Save report
        with open(PROJECT_ROOT / "cleanup_analysis.json", "w") as f:
            json.dump({
                "report": self.report,
                "to_remove": self.to_remove,
                "to_consolidate": self.to_consolidate
            }, f, indent=2)
        
        print(f"\n📄 Full report saved to: cleanup_analysis.json")
    
    def format_size(self, size):
        """Format file size"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} TB"
    
    def execute_cleanup(self, dry_run=True):
        """Execute the cleanup"""
        if dry_run:
            print(f"\n{YELLOW}{'='*60}{RESET}")
            print(f"{YELLOW}DRY RUN MODE - No files will be deleted{RESET}")
            print(f"{YELLOW}{'='*60}{RESET}\n")
            return
        
        print(f"\n{RED}{'='*60}{RESET}")
        print(f"{RED}EXECUTING CLEANUP{RESET}")
        print(f"{RED}{'='*60}{RESET}\n")
        
        removed_count = 0
        for item in self.to_remove:
            try:
                path = Path(item['path'])
                if path.is_file():
                    path.unlink()
                    print(f"{GREEN}✓{RESET} Removed file: {path.name}")
                elif path.is_dir():
                    shutil.rmtree(path)
                    print(f"{GREEN}✓{RESET} Removed folder: {path.name}")
                removed_count += 1
            except Exception as e:
                print(f"{RED}✗{RESET} Failed to remove {path.name}: {e}")
        
        print(f"\n{GREEN}✓ Cleanup complete!{RESET}")
        print(f"  Removed {removed_count} items")

if __name__ == "__main__":
    import sys
    
    cleaner = ProjectCleaner()
    
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}ZOE PROJECT COMPREHENSIVE CLEANUP{RESET}")
    print(f"{BLUE}{'='*60}{RESET}")
    
    # Analyze
    cleaner.analyze_project()
    cleaner.generate_report()
    
    # Ask for confirmation
    if len(sys.argv) > 1 and sys.argv[1] == "--execute":
        cleaner.execute_cleanup(dry_run=False)
    else:
        print(f"\n{YELLOW}{'='*60}{RESET}")
        print(f"{YELLOW}This was a DRY RUN - no files were deleted{RESET}")
        print(f"{YELLOW}{'='*60}{RESET}")
        print(f"\nTo execute cleanup, run:")
        print(f"  {GREEN}python3 comprehensive_cleanup.py --execute{RESET}")
    
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{GREEN}✓ Analysis Complete!{RESET}")
    print(f"{BLUE}{'='*60}{RESET}\n")

