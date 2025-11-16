#!/usr/bin/env python3
"""
AGGRESSIVE Zoe Project Cleanup Script
Removes everything unnecessary to get under size limits
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

def aggressive_python_cache_cleanup():
    """Remove ALL Python cache files aggressively"""
    print("\n🧹 AGGRESSIVE Python cache cleanup...")
    
    # Find all __pycache__ directories
    cache_dirs = []
    for root, dirs, files in os.walk('.'):
        if '__pycache__' in dirs:
            cache_dirs.append(os.path.join(root, '__pycache__'))
    
    total_size = 0
    for cache_dir in cache_dirs:
        size_mb = get_directory_size(cache_dir)
        total_size += size_mb
        print(f"  Removing {cache_dir} ({size_mb}MB)")
        shutil.rmtree(cache_dir, ignore_errors=True)
    
    # Also remove .pyc files
    run_command("find . -name '*.pyc' -delete", "Remove .pyc files")
    run_command("find . -name '*.pyo' -delete", "Remove .pyo files")
    
    print(f"✅ Removed {len(cache_dirs)} cache directories ({total_size}MB freed)")
    return total_size

def remove_large_database_files():
    """Remove large database files that aren't essential"""
    print("\n🗄️ Removing large database files...")
    
    # Remove training database (can be regenerated)
    training_db = "data/training.db"
    if os.path.exists(training_db):
        size_mb = get_directory_size(training_db)
        print(f"  Removing training database: {training_db} ({size_mb}MB)")
        os.remove(training_db)
        return size_mb
    
    return 0

def remove_large_json_files():
    """Remove large JSON analysis files"""
    print("\n📊 Removing large JSON files...")
    
    large_json_files = [
        "database_analysis.json",
        "database_audit_report.json", 
        "database_violations.json"
    ]
    
    total_size = 0
    for json_file in large_json_files:
        if os.path.exists(json_file):
            size_mb = get_directory_size(json_file)
            total_size += size_mb
            print(f"  Removing large JSON: {json_file} ({size_mb}MB)")
            os.remove(json_file)
    
    print(f"✅ Removed large JSON files ({total_size}MB freed)")
    return total_size

def remove_documentation_files():
    """Remove non-essential documentation"""
    print("\n📚 Removing non-essential documentation...")
    
    # Remove large documentation files
    doc_files = [
        "PROMPT_FOR_DEVELOPER_UI.md",
        "comprehensive_model_benchmark.py",
        "benchmark_llms.py",
        "demonstrate_zoe_awareness.py",
        "analyze_databases.py",
        "migrate_to_unified_db.py",
        "create_enhancement_tasks.py"
    ]
    
    total_size = 0
    for doc_file in doc_files:
        if os.path.exists(doc_file):
            size_mb = get_directory_size(doc_file)
            total_size += size_mb
            print(f"  Removing doc file: {doc_file} ({size_mb}MB)")
            os.remove(doc_file)
    
    print(f"✅ Removed documentation files ({total_size}MB freed)")
    return total_size

def remove_backup_directories():
    """Remove backup directories"""
    print("\n🗂️ Removing backup directories...")
    
    backup_dirs = [
        "backups",
        "checkpoints", 
        "logs",
        "configs"
    ]
    
    total_size = 0
    for backup_dir in backup_dirs:
        if os.path.exists(backup_dir):
            size_mb = get_directory_size(backup_dir)
            total_size += size_mb
            print(f"  Removing backup dir: {backup_dir} ({size_mb}MB)")
            shutil.rmtree(backup_dir, ignore_errors=True)
    
    print(f"✅ Removed backup directories ({total_size}MB freed)")
    return total_size

def remove_unnecessary_config_files():
    """Remove duplicate config files"""
    print("\n⚙️ Removing duplicate config files...")
    
    config_files = [
        "working-tunnel-config.yml",
        "working-config.yml",
        "simple-tunnel-config.yml", 
        "simple-config.yml",
        "tunnel-config.yml",
        "final-tunnel-config.yml",
        "final-config.yml",
        "cloudflared-config.yml"
    ]
    
    total_size = 0
    for config_file in config_files:
        if os.path.exists(config_file):
            size_mb = get_directory_size(config_file)
            total_size += size_mb
            print(f"  Removing config: {config_file} ({size_mb}MB)")
            os.remove(config_file)
    
    print(f"✅ Removed duplicate config files ({total_size}MB freed)")
    return total_size

def remove_large_service_files():
    """Remove large files from services"""
    print("\n🔧 Removing large service files...")
    
    # Remove large router files that might be duplicates
    router_files_to_check = [
        "services/zoe-core/routers/developer.py",
        "services/zoe-core/routers/chat.py"
    ]
    
    total_size = 0
    for router_file in router_files_to_check:
        if os.path.exists(router_file):
            size_mb = get_directory_size(router_file)
            if size_mb > 50:  # Only remove if over 50KB
                total_size += size_mb
                print(f"  Removing large router: {router_file} ({size_mb}MB)")
                os.remove(router_file)
    
    print(f"✅ Removed large service files ({total_size}MB freed)")
    return total_size

def optimize_git():
    """Run aggressive git optimization"""
    print("\n🔧 AGGRESSIVE git optimization...")
    run_command("git gc --aggressive --prune=now", "Aggressive garbage collection")
    run_command("git repack -ad", "Git repack")

def main():
    """Main aggressive cleanup function"""
    print("🧹 AGGRESSIVE ZOE PROJECT CLEANUP")
    print("=" * 50)
    
    # Change to project directory
    os.chdir('/home/zoe/assistant')
    
    # Get initial size
    initial_size = get_directory_size('.')
    print(f"📊 Initial project size: {initial_size}MB")
    
    total_freed = 0
    
    # Run aggressive cleanup operations
    total_freed += aggressive_python_cache_cleanup()
    total_freed += remove_large_database_files()
    total_freed += remove_large_json_files()
    total_freed += remove_documentation_files()
    total_freed += remove_backup_directories()
    total_freed += remove_unnecessary_config_files()
    total_freed += remove_large_service_files()
    
    # Optimize git
    optimize_git()
    
    # Get final size
    final_size = get_directory_size('.')
    actual_freed = initial_size - final_size
    
    print("\n" + "=" * 50)
    print("🎉 AGGRESSIVE CLEANUP COMPLETE!")
    print(f"📊 Initial size: {initial_size}MB")
    print(f"📊 Final size: {final_size}MB")
    print(f"💾 Space freed: {actual_freed}MB")
    print(f"📈 Size reduction: {(actual_freed/initial_size)*100:.1f}%")

if __name__ == "__main__":
    main()
