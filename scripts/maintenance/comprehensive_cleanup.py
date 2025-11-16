#!/usr/bin/env python3
"""
Comprehensive Zoe Project Cleanup Script
Removes cache files, old backups, and unnecessary files to reduce project size
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from datetime import datetime

def run_command(cmd, description):
    """Run a command and return success status"""
    print(f"🔄 {description}...")
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"✅ {description} - Success")
            return True
        else:
            print(f"❌ {description} - Failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"❌ {description} - Error: {e}")
        return False

def get_directory_size(path):
    """Get directory size in MB"""
    try:
        result = subprocess.run(['du', '-sm', path], capture_output=True, text=True)
        if result.returncode == 0:
            size_mb = int(result.stdout.split()[0])
            return size_mb
    except:
        pass
    return 0

def cleanup_python_cache():
    """Remove all Python cache files"""
    print("\n🧹 Cleaning Python cache files...")
    
    # Find all __pycache__ directories (excluding venv and mcp_test_env)
    cache_dirs = []
    for root, dirs, files in os.walk('.'):
        # Skip virtual environments
        if 'venv' in root or 'mcp_test_env' in root:
            continue
        if '__pycache__' in dirs:
            cache_dirs.append(os.path.join(root, '__pycache__'))
    
    total_size = 0
    for cache_dir in cache_dirs:
        size_mb = get_directory_size(cache_dir)
        total_size += size_mb
        print(f"  Removing {cache_dir} ({size_mb}MB)")
        shutil.rmtree(cache_dir, ignore_errors=True)
    
    print(f"✅ Removed {len(cache_dirs)} cache directories ({total_size}MB freed)")
    return total_size

def cleanup_old_backups():
    """Archive old database backups"""
    print("\n🗄️ Archiving old database backups...")
    
    backup_dir = Path("data/backup")
    if not backup_dir.exists():
        print("  No backup directory found")
        return 0
    
    # Create archive directory
    archive_dir = Path("data/archive")
    archive_dir.mkdir(exist_ok=True)
    
    total_size = 0
    for backup_file in backup_dir.iterdir():
        if backup_file.is_file():
            size_mb = get_directory_size(str(backup_file))
            total_size += size_mb
            
            # Move to archive
            archive_path = archive_dir / backup_file.name
            print(f"  Archiving {backup_file.name} ({size_mb}MB)")
            shutil.move(str(backup_file), str(archive_path))
    
    # Archive the pre-consolidation backup directory
    pre_consolidation = backup_dir / "pre-consolidation-20251009_150213"
    if pre_consolidation.exists():
        size_mb = get_directory_size(str(pre_consolidation))
        total_size += size_mb
        
        archive_path = archive_dir / "pre-consolidation-20251009_150213"
        print(f"  Archiving pre-consolidation backup ({size_mb}MB)")
        shutil.move(str(pre_consolidation), str(archive_path))
    
    print(f"✅ Archived {total_size}MB of old backups")
    return total_size

def cleanup_large_files():
    """Remove or compress large unnecessary files"""
    print("\n📁 Cleaning large files...")
    
    total_size = 0
    
    # Remove large audio samples (keep only essential ones)
    audio_samples = [
        "services/zoe-tts/samples/dave.wav",  # 1.3MB
    ]
    
    for audio_file in audio_samples:
        if os.path.exists(audio_file):
            size_mb = get_directory_size(audio_file)
            total_size += size_mb
            print(f"  Removing large audio sample: {audio_file} ({size_mb}MB)")
            os.remove(audio_file)
    
    # Remove old log files
    log_files = [
        "homeassistant/home-assistant.log",
        "homeassistant/home-assistant.log.1",
    ]
    
    for log_file in log_files:
        if os.path.exists(log_file):
            size_mb = get_directory_size(log_file)
            total_size += size_mb
            print(f"  Removing old log file: {log_file} ({size_mb}MB)")
            os.remove(log_file)
    
    print(f"✅ Removed {total_size}MB of large files")
    return total_size

def cleanup_archive_docs():
    """Review and clean archive documentation"""
    print("\n📚 Cleaning archive documentation...")
    
    archive_dir = Path("docs/archive")
    if not archive_dir.exists():
        print("  No archive directory found")
        return 0
    
    # Remove old UI prototypes (likely outdated)
    ui_prototypes = archive_dir / "ui-prototypes"
    if ui_prototypes.exists():
        size_mb = get_directory_size(str(ui_prototypes))
        print(f"  Removing old UI prototypes ({size_mb}MB)")
        shutil.rmtree(str(ui_prototypes))
        return size_mb
    
    return 0

def optimize_git():
    """Run git garbage collection to optimize repository"""
    print("\n🔧 Optimizing git repository...")
    
    commands = [
        ("git gc --aggressive --prune=now", "Git garbage collection"),
        ("git repack -ad", "Git repack"),
    ]
    
    for cmd, desc in commands:
        run_command(cmd, desc)

def main():
    """Main cleanup function"""
    print("🧹 ZOE PROJECT COMPREHENSIVE CLEANUP")
    print("=" * 50)
    
    # Change to project directory
    os.chdir('/home/zoe/assistant')
    
    # Get initial size
    initial_size = get_directory_size('.')
    print(f"📊 Initial project size: {initial_size}MB")
    
    total_freed = 0
    
    # Run cleanup operations
    total_freed += cleanup_python_cache()
    total_freed += cleanup_old_backups()
    total_freed += cleanup_large_files()
    total_freed += cleanup_archive_docs()
    
    # Optimize git
    optimize_git()
    
    # Get final size
    final_size = get_directory_size('.')
    actual_freed = initial_size - final_size
    
    print("\n" + "=" * 50)
    print("🎉 CLEANUP COMPLETE!")
    print(f"📊 Initial size: {initial_size}MB")
    print(f"📊 Final size: {final_size}MB")
    print(f"💾 Space freed: {actual_freed}MB")
    print(f"📈 Size reduction: {(actual_freed/initial_size)*100:.1f}%")

if __name__ == "__main__":
    main()
