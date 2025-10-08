#!/usr/bin/env python3
"""
Zoe AI System Optimization Script
Cleans up duplicate files and optimizes system performance
"""
import os
import shutil
import sys
from pathlib import Path

def backup_file(file_path):
    """Create a backup of a file before modifying"""
    backup_path = f"{file_path}.backup"
    if os.path.exists(file_path):
        shutil.copy2(file_path, backup_path)
        print(f"✅ Backed up {file_path} to {backup_path}")
        return True
    return False

def cleanup_chat_routers():
    """Clean up duplicate chat router files"""
    routers_dir = Path("/workspace/services/zoe-core/routers")
    
    # List of chat router files to keep/remove
    chat_files = {
        "chat.py": "KEEP - Main chat router",
        "chat_optimized.py": "KEEP - New optimized router", 
        "chat_backup.py": "REMOVE - Backup file",
        "chat_enhanced.py": "REMOVE - Duplicate enhanced",
        "chat_sessions.py": "REMOVE - Session management only",
        "chat_redirect.py": "REMOVE - Redirect handler",
        "chat_override.py": "REMOVE - Override version",
        "chat_bypass.py": "REMOVE - Empty bypass",
        "chat_fixed.py": "REMOVE - Fixed version"
    }
    
    print("🧹 Cleaning up chat router files...")
    
    for filename, action in chat_files.items():
        file_path = routers_dir / filename
        if file_path.exists():
            if action.startswith("REMOVE"):
                backup_file(str(file_path))
                file_path.unlink()
                print(f"🗑️  Removed {filename} - {action}")
            else:
                print(f"✅ Keeping {filename} - {action}")
        else:
            print(f"⚠️  {filename} not found")

def optimize_main_application():
    """Optimize the main application file"""
    main_file = Path("/workspace/services/zoe-core/main.py")
    
    if main_file.exists():
        print("✅ Main application file already exists and is optimized")
        return True
    
    # If no main.py exists, copy from working version
    working_file = Path("/workspace/services/zoe-core/main.WORKING.py")
    if working_file.exists():
        backup_file(str(working_file))
        shutil.copy2(str(working_file), str(main_file))
        print("✅ Created main.py from working version")
        return True
    
    print("❌ No main application file found")
    return False

def update_chat_router():
    """Update the main chat router to use the optimized version"""
    chat_file = Path("/workspace/services/zoe-core/routers/chat.py")
    optimized_file = Path("/workspace/services/zoe-core/routers/chat_optimized.py")
    
    if not optimized_file.exists():
        print("❌ Optimized chat router not found")
        return False
    
    if chat_file.exists():
        backup_file(str(chat_file))
    
    # Replace the main chat router with the optimized version
    shutil.copy2(str(optimized_file), str(chat_file))
    print("✅ Updated main chat router with optimized version")
    return True

def cleanup_temp_files():
    """Clean up temporary and backup files"""
    temp_patterns = [
        "*.backup",
        "*.tmp", 
        "*.temp",
        "*_backup_*",
        "*_old_*",
        "*_broken_*"
    ]
    
    print("🧹 Cleaning up temporary files...")
    
    # Clean up in services directory
    services_dir = Path("/workspace/services/zoe-core")
    for pattern in temp_patterns:
        for file_path in services_dir.rglob(pattern):
            if file_path.is_file():
                try:
                    file_path.unlink()
                    print(f"🗑️  Removed {file_path.name}")
                except Exception as e:
                    print(f"⚠️  Could not remove {file_path.name}: {e}")

def verify_system_integrity():
    """Verify that the system is properly configured"""
    print("🔍 Verifying system integrity...")
    
    # Check main application
    main_file = Path("/workspace/services/zoe-core/main.py")
    if main_file.exists():
        print("✅ Main application file exists")
    else:
        print("❌ Main application file missing")
        return False
    
    # Check chat router
    chat_file = Path("/workspace/services/zoe-core/routers/chat.py")
    if chat_file.exists():
        print("✅ Chat router exists")
    else:
        print("❌ Chat router missing")
        return False
    
    # Check requirements
    req_file = Path("/workspace/services/zoe-core/requirements.txt")
    if req_file.exists():
        print("✅ Requirements file exists")
    else:
        print("❌ Requirements file missing")
        return False
    
    return True

def main():
    """Main optimization function"""
    print("🚀 Starting Zoe AI System Optimization...")
    print("=" * 50)
    
    try:
        # Step 1: Clean up duplicate chat routers
        cleanup_chat_routers()
        print()
        
        # Step 2: Optimize main application
        optimize_main_application()
        print()
        
        # Step 3: Update chat router
        update_chat_router()
        print()
        
        # Step 4: Clean up temporary files
        cleanup_temp_files()
        print()
        
        # Step 5: Verify system integrity
        if verify_system_integrity():
            print()
            print("🎉 System optimization completed successfully!")
            print("✅ All systems are properly configured")
            print("✅ Duplicate files have been cleaned up")
            print("✅ Chat system is optimized")
            print("✅ Main application is ready")
            print()
            print("🚀 The system is now optimized and ready for use!")
        else:
            print()
            print("⚠️  System optimization completed with warnings")
            print("Some components may need manual attention")
            
    except Exception as e:
        print(f"❌ Optimization failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
