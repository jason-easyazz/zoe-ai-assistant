#!/usr/bin/env python3
"""
File Tagging System for Unused Files
Tags files that haven't been accessed in the last week for potential archival
"""

import os
import time
import json
from datetime import datetime, timedelta
from pathlib import Path

class FileTagger:
    def __init__(self, project_root="/home/pi/zoe"):
        self.project_root = Path(project_root)
        self.tags_file = self.project_root / "scripts/maintenance/file_tags.json"
        self.week_ago = time.time() - (7 * 24 * 60 * 60)  # 7 days ago
        
        # Files/directories to exclude from tagging
        self.exclude_patterns = {
            ".git", ".gitignore", "mcp_test_env", "__pycache__", 
            "*.pyc", "*.log", "*.db", "*.db-shm", "*.db-wal",
            "node_modules", ".env", "*.key", "*.pem"
        }
        
        # Essential files that should never be tagged
        self.essential_files = {
            "README.md", "requirements.txt", "main.py", "Dockerfile",
            "chat.py", "auth.py", "calendar.py", "lists.py", "journal.py"
        }

    def is_excluded(self, file_path):
        """Check if file should be excluded from tagging"""
        path_str = str(file_path)
        
        # Check exclude patterns
        for pattern in self.exclude_patterns:
            if pattern in path_str:
                return True
        
        # Check if essential file
        if file_path.name in self.essential_files:
            return True
            
        return False

    def get_file_stats(self, file_path):
        """Get file access statistics"""
        try:
            stat = file_path.stat()
            return {
                "accessed": stat.st_atime,
                "modified": stat.st_mtime,
                "created": stat.st_ctime,
                "size": stat.st_size
            }
        except (OSError, FileNotFoundError):
            return None

    def tag_unused_files(self):
        """Tag files that haven't been accessed in the last week"""
        tagged_files = {}
        
        for file_path in self.project_root.rglob("*"):
            if not file_path.is_file() or self.is_excluded(file_path):
                continue
                
            stats = self.get_file_stats(file_path)
            if not stats:
                continue
                
            # Check if file hasn't been accessed in the last week
            if stats["accessed"] < self.week_ago:
                relative_path = file_path.relative_to(self.project_root)
                tagged_files[str(relative_path)] = {
                    "last_accessed": datetime.fromtimestamp(stats["accessed"]).isoformat(),
                    "last_modified": datetime.fromtimestamp(stats["modified"]).isoformat(),
                    "size_bytes": stats["size"],
                    "tagged_date": datetime.now().isoformat(),
                    "reason": "Not accessed in 7 days"
                }
        
        return tagged_files

    def save_tags(self, tagged_files):
        """Save tagged files to JSON file"""
        tags_data = {
            "last_scan": datetime.now().isoformat(),
            "total_tagged": len(tagged_files),
            "tagged_files": tagged_files
        }
        
        self.tags_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.tags_file, 'w') as f:
            json.dump(tags_data, f, indent=2)
        
        return len(tagged_files)

    def load_existing_tags(self):
        """Load existing tags from file"""
        if not self.tags_file.exists():
            return {}
        
        try:
            with open(self.tags_file, 'r') as f:
                data = json.load(f)
                return data.get("tagged_files", {})
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def update_tags(self):
        """Update tags based on current file usage"""
        existing_tags = self.load_existing_tags()
        new_tags = self.tag_unused_files()
        
        # Remove tags for files that are now being used
        updated_tags = {}
        for file_path, tag_info in existing_tags.items():
            full_path = self.project_root / file_path
            if full_path.exists() and file_path in new_tags:
                updated_tags[file_path] = tag_info
        
        # Add new tags
        for file_path, tag_info in new_tags.items():
            if file_path not in updated_tags:
                updated_tags[file_path] = tag_info
        
        return updated_tags

    def generate_report(self, tagged_files):
        """Generate a report of tagged files"""
        if not tagged_files:
            print("✅ No unused files found!")
            return
        
        print(f"📋 Found {len(tagged_files)} potentially unused files:")
        print("-" * 60)
        
        # Group by directory
        by_directory = {}
        for file_path, info in tagged_files.items():
            directory = str(Path(file_path).parent)
            if directory not in by_directory:
                by_directory[directory] = []
            by_directory[directory].append((file_path, info))
        
        for directory, files in sorted(by_directory.items()):
            print(f"\n📁 {directory}/")
            for file_path, info in files:
                size_mb = info["size_bytes"] / (1024 * 1024)
                last_access = info["last_accessed"][:10]  # Date only
                print(f"  📄 {Path(file_path).name} ({size_mb:.2f}MB) - Last accessed: {last_access}")

def main():
    print("🏷️  File Tagging System - Tagging Unused Files")
    print("=" * 50)
    
    tagger = FileTagger()
    
    # Update tags
    tagged_files = tagger.update_tags()
    
    # Save updated tags
    total_tagged = tagger.save_tags(tagged_files)
    
    # Generate report
    tagger.generate_report(tagged_files)
    
    print(f"\n📊 Summary: {total_tagged} files tagged for potential archival")
    print(f"📁 Tags saved to: {tagger.tags_file}")
    
    if total_tagged > 0:
        print("\n💡 Next steps:")
        print("   - Review tagged files manually")
        print("   - Run archive script after confirmation")
        print("   - Files will be moved to docs/archive/ if approved")

if __name__ == "__main__":
    main()
