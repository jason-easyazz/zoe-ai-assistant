#!/usr/bin/env python3
"""
Update Database References Script
Updates all code to use zoe.db instead of forbidden databases
"""

import os
import re
from pathlib import Path
from typing import List, Tuple

# Database mapping: old_path -> new_path
DATABASE_REPLACEMENTS = {
    'zoe.db': 'zoe.db',
    'zoe.db': 'zoe.db',
    'zoe.db': 'zoe.db',
    'zoe.db': 'zoe.db',
    'zoe.db': 'zoe.db',
    'zoe.db': 'zoe.db',
    'zoe.db': 'zoe.db',
    'zoe.db': 'zoe.db',
    'zoe.db': 'zoe.db',
    'zoe.db': 'zoe.db',
    'zoe.db': 'zoe.db',
    'zoe.db': 'zoe.db',
    'zoe.db': 'zoe.db',
    'zoe.db': 'zoe.db',
    'zoe.db': 'zoe.db',
    'zoe.db': 'zoe.db',
}

# Keep memory.db as is (Light RAG only)
ALLOWED_DATABASES = ['zoe.db', 'memory.db']

class DatabaseReferenceUpdater:
    def __init__(self):
        self.project_root = Path("/home/pi/zoe")
        self.files_updated = []
        self.total_replacements = 0
        self.log_file = Path("/home/pi/zoe/database_consolidation.log")
        
    def log(self, message: str):
        """Log to file and console"""
        print(message)
        with open(self.log_file, 'a') as f:
            f.write(message + "\n")
    
    def update_file(self, file_path: Path) -> Tuple[int, List[str]]:
        """Update database references in a single file"""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            original_content = content
            replacements_made = []
            
            # Replace database references
            for old_db, new_db in DATABASE_REPLACEMENTS.items():
                # Pattern 1: Direct string with /app/data/ or data/
                patterns = [
                    (f'"/app/data/{old_db}"', f'"/app/data/{new_db}"'),
                    (f"'/app/data/{old_db}'", f"'/app/data/{new_db}'"),
                    (f'"data/{old_db}"', f'"data/{new_db}"'),
                    (f"'data/{old_db}'", f"'data/{new_db}'"),
                    (f'"{old_db}"', f'"{new_db}"'),
                    (f"'{old_db}'", f"'{new_db}'"),
                ]
                
                for old_pattern, new_pattern in patterns:
                    if old_pattern in content:
                        content = content.replace(old_pattern, new_pattern)
                        replacements_made.append(f"{old_pattern} -> {new_pattern}")
            
            # Only write if changes were made
            if content != original_content:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                return len(replacements_made), replacements_made
            
            return 0, []
            
        except Exception as e:
            self.log(f"   ⚠️  Error updating {file_path}: {e}")
            return 0, []
    
    def update_all_files(self):
        """Update all Python files in services"""
        self.log("\n📝 Updating database references in all files...")
        self.log("=" * 80)
        
        # Scan services directory
        for file_path in self.project_root.glob("services/**/*.py"):
            if '__pycache__' in str(file_path):
                continue
            
            count, replacements = self.update_file(file_path)
            if count > 0:
                self.files_updated.append(str(file_path))
                self.total_replacements += count
                rel_path = file_path.relative_to(self.project_root)
                self.log(f"\n✅ Updated: {rel_path}")
                for replacement in replacements[:3]:  # Show first 3
                    self.log(f"   - {replacement}")
                if len(replacements) > 3:
                    self.log(f"   ... and {len(replacements) - 3} more")
        
        # Also update scripts
        for file_path in self.project_root.glob("scripts/**/*.py"):
            if '__pycache__' in str(file_path):
                continue
            
            count, replacements = self.update_file(file_path)
            if count > 0:
                self.files_updated.append(str(file_path))
                self.total_replacements += count
                rel_path = file_path.relative_to(self.project_root)
                self.log(f"\n✅ Updated: {rel_path}")
        
        self.log("\n" + "=" * 80)
        self.log(f"📊 SUMMARY:")
        self.log(f"   Files updated: {len(self.files_updated)}")
        self.log(f"   Total replacements: {self.total_replacements}")
        self.log("=" * 80)
    
    def run(self):
        """Run the update process"""
        self.log("\n" + "=" * 80)
        self.log("🔄 UPDATING DATABASE REFERENCES")
        self.log("=" * 80)
        
        self.update_all_files()
        
        self.log("\n✅ Database references updated successfully")
        return len(self.files_updated)

if __name__ == "__main__":
    updater = DatabaseReferenceUpdater()
    files_updated = updater.run()
    exit(0)

