#!/usr/bin/env python3
"""
Git History Cleanup Script
Removes large files from git history and rebuilds repository
"""

import os
import subprocess
import shutil
from pathlib import Path

def run_command(cmd, description):
    """Run a command and return success status"""
    print(f"🔄 {description}...")
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"✅ {description} - Success")
            return True, result.stdout
        else:
            print(f"❌ {description} - Failed: {result.stderr}")
            return False, result.stderr
    except Exception as e:
        print(f"❌ {description} - Error: {e}")
        return False, str(e)

def get_git_size():
    """Get current git repository size"""
    result = subprocess.run(['du', '-sh', '.git'], capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    return "Unknown"

def main():
    """Main git cleanup function"""
    print("🧹 GIT HISTORY COMPREHENSIVE CLEANUP")
    print("=" * 50)
    
    # Change to project directory
    os.chdir('/home/zoe/assistant')
    
    initial_size = get_git_size()
    print(f"📊 Initial git size: {initial_size}")
    
    # Step 1: Remove large files from git history
    print("\n🗑️ Removing large files from git history...")
    
    large_files = [
        "data/backup/performance_20251004_100047.db",
        "data/zoe.db.backup-20251018-142054", 
        "data/zoe.db",
        "mcp_test_env/lib/python3.11/site-packages/pydantic_core/_pydantic_core.cpython-311-aarch64-linux-gnu.so"
    ]
    
    for file_path in large_files:
        if os.path.exists(file_path):
            print(f"  Removing {file_path} from git history...")
            run_command(f"git filter-branch --force --index-filter 'git rm --cached --ignore-unmatch {file_path}' --prune-empty --tag-name-filter cat -- --all", 
                       f"Remove {file_path}")
    
    # Step 2: Clean up git references
    print("\n🧽 Cleaning git references...")
    run_command("git for-each-ref --format='delete %(refname)' refs/original | git update-ref --stdin", "Delete original refs")
    run_command("git reflog expire --expire=now --all", "Expire reflog")
    run_command("git gc --prune=now --aggressive", "Aggressive garbage collection")
    
    # Step 3: Force push to update remote
    print("\n🚀 Updating remote repository...")
    run_command("git push origin --force --all", "Force push all branches")
    run_command("git push origin --force --tags", "Force push tags")
    
    final_size = get_git_size()
    print("\n" + "=" * 50)
    print("🎉 GIT CLEANUP COMPLETE!")
    print(f"📊 Initial git size: {initial_size}")
    print(f"📊 Final git size: {final_size}")

if __name__ == "__main__":
    main()
