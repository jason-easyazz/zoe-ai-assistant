#!/usr/bin/env python3
"""
Archive Tagged Files
Moves tagged files to archive directory after confirmation
"""

import os
import json
import shutil
from datetime import datetime
from pathlib import Path

class FileArchiver:
    def __init__(self, project_root="/home/pi/zoe"):
        self.project_root = Path(project_root)
        self.tags_file = self.project_root / "scripts/maintenance/file_tags.json"
        self.archive_root = self.project_root / "docs/archive/tagged_files"
        
        # Create archive directory with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.archive_dir = self.archive_root / timestamp

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
            print("✅ No files tagged for archival.")
            return False
        
        print(f"📋 Files tagged for archival ({len(tagged_files)} files):")
        print("=" * 60)
        
        total_size = 0
        for file_path, info in tagged_files.items():
            size_mb = info["size_bytes"] / (1024 * 1024)
            total_size += size_mb
            last_access = info["last_accessed"][:10]
            print(f"📄 {file_path} ({size_mb:.2f}MB) - Last accessed: {last_access}")
        
        print(f"\n📊 Total size to archive: {total_size:.2f}MB")
        return True

    def confirm_archival(self, tagged_files):
        """Ask user to confirm archival"""
        print("\n" + "=" * 60)
        print("🤔 CONFIRMATION REQUIRED")
        print("=" * 60)
        
        response = input("Do you want to archive these files? (y/N): ").strip().lower()
        return response in ['y', 'yes']

    def archive_files(self, tagged_files):
        """Archive the tagged files"""
        if not tagged_files:
            return 0
        
        # Create archive directory
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        
        # Create manifest file
        manifest = {
            "archived_date": datetime.now().isoformat(),
            "total_files": len(tagged_files),
            "archive_location": str(self.archive_dir),
            "files": tagged_files
        }
        
        manifest_file = self.archive_dir / "archive_manifest.json"
        with open(manifest_file, 'w') as f:
            json.dump(manifest, f, indent=2)
        
        archived_count = 0
        
        for file_path, info in tagged_files.items():
            source_path = self.project_root / file_path
            if not source_path.exists():
                print(f"⚠️  File not found: {file_path}")
                continue
            
            # Create destination directory structure
            dest_path = self.archive_dir / file_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            
            try:
                # Move file to archive
                shutil.move(str(source_path), str(dest_path))
                print(f"✅ Archived: {file_path}")
                archived_count += 1
            except Exception as e:
                print(f"❌ Failed to archive {file_path}: {e}")
        
        return archived_count

    def update_tags_file(self, archived_count):
        """Update tags file after archival"""
        if archived_count > 0:
            # Clear tagged files after successful archival
            tags_data = {
                "last_scan": datetime.now().isoformat(),
                "total_tagged": 0,
                "tagged_files": {},
                "last_archive": {
                    "date": datetime.now().isoformat(),
                    "files_archived": archived_count,
                    "archive_location": str(self.archive_dir)
                }
            }
            
            with open(self.tags_file, 'w') as f:
                json.dump(tags_data, f, indent=2)

def main():
    print("📦 File Archival System")
    print("=" * 30)
    
    archiver = FileArchiver()
    
    # Load tagged files
    tagged_files = archiver.load_tagged_files()
    
    # Show files for review
    if not archiver.show_tagged_files(tagged_files):
        return
    
    # Confirm archival
    if not archiver.confirm_archival(tagged_files):
        print("❌ Archival cancelled.")
        return
    
    # Archive files
    print("\n📦 Archiving files...")
    archived_count = archiver.archive_files(tagged_files)
    
    if archived_count > 0:
        # Update tags file
        archiver.update_tags_file(archived_count)
        
        print(f"\n✅ Successfully archived {archived_count} files")
        print(f"📁 Archive location: {archiver.archive_dir}")
        print("💡 Files can be restored from the archive if needed")
    else:
        print("❌ No files were archived")

if __name__ == "__main__":
    main()
