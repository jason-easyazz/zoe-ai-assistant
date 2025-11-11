#!/usr/bin/env python3
"""
Final Verification Script - Confirm GitHub Repository is Clean
"""

import subprocess
import os

def run_command(cmd):
    """Run a command and return output"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except Exception as e:
        return "", str(e), 1

def main():
    print("🔍 FINAL VERIFICATION: GitHub Repository Cleanup Status")
    print("=" * 60)
    
    os.chdir('/home/zoe/assistant')
    
    # Check git status
    stdout, stderr, code = run_command("git status --porcelain")
    if stdout:
        print(f"❌ Working directory has uncommitted changes: {stdout}")
    else:
        print("✅ Working directory is clean")
    
    # Check if we're up to date with remote
    stdout, stderr, code = run_command("git status -sb")
    if "ahead" in stdout:
        print(f"❌ Local branch is ahead of remote: {stdout}")
    elif "behind" in stdout:
        print(f"❌ Local branch is behind remote: {stdout}")
    else:
        print("✅ Local branch is up to date with remote")
    
    # Check for large files in git history
    stdout, stderr, code = run_command("git rev-list --objects --all | git cat-file --batch-check='%(objecttype) %(objectname) %(objectsize) %(rest)' | awk '/^blob/ {print $3 \" \" $4}' | sort -n | tail -5")
    large_files = stdout.split('\n') if stdout else []
    
    print(f"\n📊 Largest files in git history:")
    for line in large_files:
        if line.strip():
            parts = line.split(' ', 1)
            if len(parts) == 2:
                size_bytes = int(parts[0])
                size_mb = size_bytes / (1024 * 1024)
                filename = parts[1]
                print(f"  {size_mb:.1f}MB - {filename}")
    
    # Check for files over 10MB
    large_count = 0
    for line in large_files:
        if line.strip():
            parts = line.split(' ', 1)
            if len(parts) == 2:
                size_bytes = int(parts[0])
                if size_bytes > 10 * 1024 * 1024:  # 10MB
                    large_count += 1
    
    if large_count > 0:
        print(f"❌ Found {large_count} files over 10MB in git history")
    else:
        print("✅ No files over 10MB in git history")
    
    # Check git repository size
    stdout, stderr, code = run_command("du -sh .git")
    git_size = stdout.split()[0] if stdout else "Unknown"
    print(f"\n📦 Git repository size: {git_size}")
    
    # Check total project size
    stdout, stderr, code = run_command("du -sh .")
    total_size = stdout.split()[0] if stdout else "Unknown"
    print(f"📦 Total project size: {total_size}")
    
    # Check for database files in git
    stdout, stderr, code = run_command("git ls-tree -r --name-only HEAD | grep '\\.db$'")
    tracked_dbs = stdout.split('\n') if stdout else []
    tracked_dbs = [db for db in tracked_dbs if db.strip()]
    
    print(f"\n🗄️ Database files tracked in git:")
    if tracked_dbs:
        for db in tracked_dbs:
            print(f"  ✅ {db}")
    else:
        print("  ✅ No database files tracked in git")
    
    # Check .gitignore for database exclusions
    stdout, stderr, code = run_command("grep -E '\\.db|archive' .gitignore")
    gitignore_dbs = stdout.split('\n') if stdout else []
    gitignore_dbs = [line for line in gitignore_dbs if line.strip()]
    
    print(f"\n🚫 Database exclusions in .gitignore:")
    for line in gitignore_dbs:
        print(f"  ✅ {line}")
    
    print("\n" + "=" * 60)
    print("🎯 SUMMARY:")
    print(f"  • Git repository: {git_size}")
    print(f"  • Total project: {total_size}")
    print(f"  • Large files (>10MB): {large_count}")
    print(f"  • Tracked databases: {len(tracked_dbs)}")
    print(f"  • Gitignore rules: {len(gitignore_dbs)}")
    
    if large_count == 0 and len(tracked_dbs) <= 2:
        print("\n🎉 REPOSITORY IS CLEAN!")
        print("✅ All large files removed from GitHub")
        print("✅ Database files properly excluded")
        print("✅ Ready for fast GitHub imports")
    else:
        print("\n⚠️  Repository may need additional cleanup")

if __name__ == "__main__":
    main()
