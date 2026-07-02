#!/usr/bin/env python3
"""
Retire Tagged Files
Deletes tagged files from the working tree after confirmation. Git history keeps
committed files recoverable.
"""

import os
import json
import shutil
from datetime import datetime
from pathlib import Path

class FileRetirer:
    def __init__(self, project_root="/home/zoe/assistant"):
        self.project_root = Path(project_root)
        self.tags_file = self.project_root / "scripts/maintenance/file_tags.json"

    def load_tagged_files(self):
        """Load tagged files from JSON"""
        if not self.tags_file.exists():
            print("❌ No tagged files found. Run tag_unused_files.py first.")
            return {}
        
        try:
            with open(self.tags_file, 'r') as f:
                data = json.load(f)
                return data.get("tagged_files", {})
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def show_tagged_files(self, tagged_files):
        """Show tagged files for review"""
        if not tagged_files:
            print("✅ No files tagged for retirement.")
            return False
        
        print(f"📋 Files tagged for retirement ({len(tagged_files)} files):")
        print("=" * 60)
        
        total_size = 0
        for file_path, info in tagged_files.items():
            size_mb = info["size_bytes"] / (1024 * 1024)
            total_size += size_mb
            last_access = info["last_accessed"][:10]
            print(f"📄 {file_path} ({size_mb:.2f}MB) - Last accessed: {last_access}")
        
        print(f"\n📊 Total size to retire: {total_size:.2f}MB")
        return True

    def confirm_retirement(self, tagged_files):
        """Ask user to confirm deletion from the working tree."""
        print("\n" + "=" * 60)
        print("🤔 CONFIRMATION REQUIRED")
        print("=" * 60)
        
        response = input("Do you want to delete these files from the working tree? (y/N): ").strip().lower()
        return response in ['y', 'yes']

    def retire_files(self, tagged_files):
        """Delete the tagged files. Git history keeps prior committed contents."""
        if not tagged_files:
            return 0
        
        retired_count = 0
        
        for file_path, info in tagged_files.items():
            source_path = self.project_root / file_path
            if not source_path.exists():
                print(f"⚠️  File not found: {file_path}")
                continue

            try:
                if source_path.is_dir():
                    shutil.rmtree(str(source_path))
                else:
                    source_path.unlink()
                print(f"✅ Retired: {file_path}")
                retired_count += 1
            except Exception as e:
                print(f"❌ Failed to retire {file_path}: {e}")
        
        return retired_count

    def update_tags_file(self, retired_count):
        """Update tags file after retirement."""
        if retired_count > 0:
            # Clear tagged files after successful retirement.
            tags_data = {
                "last_scan": datetime.now().isoformat(),
                "total_tagged": 0,
                "tagged_files": {},
                "last_retirement": {
                    "date": datetime.now().isoformat(),
                    "files_retired": retired_count,
                }
            }
            
            with open(self.tags_file, 'w') as f:
                json.dump(tags_data, f, indent=2)

def main():
    print("📦 File Retirement System")
    print("=" * 30)
    
    retirer = FileRetirer()
    
    # Load tagged files
    tagged_files = retirer.load_tagged_files()
    
    # Show files for review
    if not retirer.show_tagged_files(tagged_files):
        return
    
    # Confirm retirement
    if not retirer.confirm_retirement(tagged_files):
        print("❌ Retirement cancelled.")
        return
    
    # Retire files
    print("\n📦 Retiring files...")
    retired_count = retirer.retire_files(tagged_files)
    
    if retired_count > 0:
        # Update tags file
        retirer.update_tags_file(retired_count)
        
        print(f"\n✅ Successfully retired {retired_count} files")
        print("💡 Committed files can be restored from git history if needed")
    else:
        print("❌ No files were retired")

if __name__ == "__main__":
    main()
