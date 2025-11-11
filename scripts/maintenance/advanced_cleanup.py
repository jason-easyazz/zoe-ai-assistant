#!/usr/bin/env python3
"""
Advanced Zoe Project Cleanup Script
Removes additional unnecessary files to further reduce project size
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
    
    # Find all __pycache__ directories
    cache_dirs = []
    for root, dirs, files in os.walk('services/'):
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

def cleanup_ui_dist_files():
    """Clean up unnecessary UI distribution files"""
    print("\n🎨 Cleaning UI distribution files...")
    
    ui_dist = Path("services/zoe-ui/dist")
    if not ui_dist.exists():
        print("  No UI dist directory found")
        return 0
    
    # Remove backup files
    backup_files = list(ui_dist.glob("*.backup"))
    total_size = 0
    
    for backup_file in backup_files:
        size_mb = get_directory_size(str(backup_file))
        total_size += size_mb
        print(f"  Removing backup file: {backup_file.name} ({size_mb}MB)")
        backup_file.unlink()
    
    # Remove old design files
    old_design_files = [
        "zoe-journal-design-v2.html",
        "zoe-journal-design-v3.html", 
        "zoe-journal-complete-design.html"
    ]
    
    for design_file in old_design_files:
        file_path = ui_dist / design_file
        if file_path.exists():
            size_mb = get_directory_size(str(file_path))
            total_size += size_mb
            print(f"  Removing old design file: {design_file} ({size_mb}MB)")
            file_path.unlink()
    
    print(f"✅ Cleaned UI distribution files ({total_size}MB freed)")
    return total_size

def cleanup_large_audio_files():
    """Remove large audio sample files"""
    print("\n🎵 Cleaning large audio files...")
    
    audio_files = [
        "services/zoe-tts/samples/dave.wav",
        "services/zoe-tts/samples/jo.wav"
    ]
    
    total_size = 0
    for audio_file in audio_files:
        if os.path.exists(audio_file):
            size_mb = get_directory_size(audio_file)
            total_size += size_mb
            print(f"  Removing large audio file: {audio_file} ({size_mb}MB)")
            os.remove(audio_file)
    
    print(f"✅ Removed large audio files ({total_size}MB freed)")
    return total_size

def cleanup_duplicate_files():
    """Remove duplicate or redundant files"""
    print("\n🔄 Cleaning duplicate files...")
    
    # Remove duplicate HTML files
    duplicate_patterns = [
        "services/zoe-ui/dist/dashboard.html.backup",
        "services/zoe-ui/dist/*-design*.html"
    ]
    
    total_size = 0
    for pattern in duplicate_patterns:
        # Use find command to locate files
        result = subprocess.run(['find', 'services/zoe-ui/dist/', '-name', pattern.split('/')[-1]], 
                              capture_output=True, text=True)
        if result.returncode == 0:
            files = result.stdout.strip().split('\n')
            for file_path in files:
                if file_path and os.path.exists(file_path):
                    size_mb = get_directory_size(file_path)
                    total_size += size_mb
                    print(f"  Removing duplicate: {file_path} ({size_mb}MB)")
                    os.remove(file_path)
    
    print(f"✅ Removed duplicate files ({total_size}MB freed)")
    return total_size

def cleanup_documentation_files():
    """Remove redundant documentation files"""
    print("\n📚 Cleaning redundant documentation...")
    
    # Remove implementation docs from dist
    doc_files = [
        "services/zoe-ui/dist/JOURNAL_IMPLEMENTATION.md",
        "services/zoe-ui/dist/CALENDAR_IMPLEMENTATION.md", 
        "services/zoe-ui/dist/FRONTEND_CONNECTION_STATUS.md"
    ]
    
    total_size = 0
    for doc_file in doc_files:
        if os.path.exists(doc_file):
            size_mb = get_directory_size(doc_file)
            total_size += size_mb
            print(f"  Removing redundant doc: {doc_file} ({size_mb}MB)")
            os.remove(doc_file)
    
    print(f"✅ Removed redundant documentation ({total_size}MB freed)")
    return total_size

def optimize_git():
    """Run git garbage collection"""
    print("\n🔧 Optimizing git repository...")
    run_command("git gc --aggressive --prune=now", "Git garbage collection")

def main():
    """Main cleanup function"""
    print("🧹 ADVANCED ZOE PROJECT CLEANUP")
    print("=" * 50)
    
    # Change to project directory
    os.chdir('/home/zoe/assistant')
    
    # Get initial size
    initial_size = get_directory_size('.')
    print(f"📊 Initial project size: {initial_size}MB")
    
    total_freed = 0
    
    # Run cleanup operations
    total_freed += cleanup_python_cache()
    total_freed += cleanup_ui_dist_files()
    total_freed += cleanup_large_audio_files()
    total_freed += cleanup_duplicate_files()
    total_freed += cleanup_documentation_files()
    
    # Optimize git
    optimize_git()
    
    # Get final size
    final_size = get_directory_size('.')
    actual_freed = initial_size - final_size
    
    print("\n" + "=" * 50)
    print("🎉 ADVANCED CLEANUP COMPLETE!")
    print(f"📊 Initial size: {initial_size}MB")
    print(f"📊 Final size: {final_size}MB")
    print(f"💾 Space freed: {actual_freed}MB")
    print(f"📈 Size reduction: {(actual_freed/initial_size)*100:.1f}%")

if __name__ == "__main__":
    main()
