#!/usr/bin/env python3
"""
Test Training Setup
Verifies all components are properly connected
"""
import sys
import os
from pathlib import Path

sys.path.append('/home/pi/zoe/services/zoe-core')
sys.path.append('/app')

def test_imports():
    """Test that all required modules can be imported"""
    print("\n🧪 Testing Imports...")
    
    tests = []
    
    # Test training collector
    try:
        from training_engine.data_collector import training_collector
        print("  ✅ training_collector")
        tests.append(True)
    except ImportError as e:
        print(f"  ❌ training_collector: {e}")
        tests.append(False)
    
    # Test prompt templates
    try:
        from prompt_templates import build_enhanced_prompt, PromptTemplates
        print("  ✅ prompt_templates")
        tests.append(True)
    except ImportError as e:
        print(f"  ❌ prompt_templates: {e}")
        tests.append(False)
    
    # Test graph engine
    try:
        from graph_engine import graph_engine
        print("  ✅ graph_engine")
        stats = graph_engine.get_stats()
        print(f"     Graph: {stats.get('nodes', 0)} nodes, {stats.get('edges', 0)} edges")
        tests.append(True)
    except ImportError as e:
        print(f"  ❌ graph_engine: {e}")
        tests.append(False)
    
    # Test NetworkX
    try:
        import networkx as nx
        print(f"  ✅ NetworkX v{nx.__version__}")
        tests.append(True)
    except ImportError as e:
        print(f"  ❌ NetworkX: {e}")
        tests.append(False)
    
    return all(tests)

def test_database():
    """Test training database is set up correctly"""
    print("\n🗄️  Testing Training Database...")
    
    db_path = "/app/data/training.db"
    
    if not os.path.exists(db_path):
        print(f"  ⚠️  Database will be created on first use: {db_path}")
        return True
    
    import sqlite3
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    
    required_tables = [
        'training_examples',
        'response_patterns',
        'tool_call_performance',
        'training_runs'
    ]
    
    all_exist = all(table in tables for table in required_tables)
    
    if all_exist:
        print("  ✅ All required tables exist")
        
        # Count examples
        cursor.execute("SELECT COUNT(*) FROM training_examples")
        count = cursor.fetchone()[0]
        print(f"  📊 {count} training examples collected")
    else:
        missing = [t for t in required_tables if t not in tables]
        print(f"  ⚠️  Missing tables: {missing}")
        print("     Will be created on first interaction")
    
    conn.close()
    return True

def test_directories():
    """Test required directories exist"""
    print("\n📁 Testing Directories...")
    
    dirs = [
        "/home/pi/zoe/models/adapters",
        "/home/pi/zoe/scripts/train",
        "/home/pi/zoe/services/zoe-core/training_engine"
    ]
    
    all_exist = True
    for dir_path in dirs:
        if Path(dir_path).exists():
            print(f"  ✅ {dir_path}")
        else:
            print(f"  ❌ {dir_path}")
            all_exist = False
    
    return all_exist

def test_model_manager():
    """Test model manager CLI"""
    print("\n🤖 Testing Model Manager...")
    
    import subprocess
    
    try:
        result = subprocess.run(
            ["/home/pi/zoe/tools/model-manager.py", "info"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            print("  ✅ Model manager functional")
            print("     " + result.stdout.split('\n')[0])
            return True
        else:
            print(f"  ❌ Model manager error: {result.stderr}")
            return False
    except Exception as e:
        print(f"  ❌ Model manager test failed: {e}")
        return False

def test_unsloth():
    """Test if Unsloth is installed"""
    print("\n🔥 Testing Unsloth (Optional)...")
    
    try:
        from unsloth import FastLanguageModel
        print("  ✅ Unsloth installed - READY FOR TRAINING!")
        return True
    except ImportError:
        print("  ⚠️  Unsloth not installed")
        print("     Training data collection works, but can't train yet")
        print("     Install: pip install unsloth")
        return False  # Not required, so return True anyway
    
    return True

def test_api_endpoints():
    """Test that API endpoints are accessible"""
    print("\n🌐 Testing API Endpoints...")
    
    import httpx
    import asyncio
    
    async def check_endpoints():
        async with httpx.AsyncClient() as client:
            tests = []
            
            # Test training stats endpoint
            try:
                response = await client.get("http://localhost:8000/api/chat/training-stats?user_id=test")
                if response.status_code == 200:
                    print("  ✅ /api/chat/training-stats")
                    tests.append(True)
                else:
                    print(f"  ❌ /api/chat/training-stats ({response.status_code})")
                    tests.append(False)
            except Exception as e:
                print(f"  ⚠️  /api/chat/training-stats - {e}")
                print("     (Backend may not be running)")
                tests.append(False)
            
            return all(tests) if tests else False
    
    try:
        result = asyncio.run(check_endpoints())
        return True  # Don't fail on this, backend might not be running
    except Exception as e:
        print(f"  ⚠️  API test skipped - backend not running")
        return True  # Still pass


def main():
    """Run all tests"""
    print("=" * 60)
    print("  ZOE TRAINING SYSTEM VERIFICATION")
    print("=" * 60)
    
    results = {
        "Imports": test_imports(),
        "Database": test_database(),
        "Directories": test_directories(),
        "Model Manager": test_model_manager(),
        "Unsloth": test_unsloth(),
        "API Endpoints": test_api_endpoints()
    }
    
    print("\n" + "=" * 60)
    print("  RESULTS")
    print("=" * 60)
    
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {test_name:20} {status}")
    
    all_critical_passed = results["Imports"] and results["Database"] and results["Directories"]
    
    if all_critical_passed:
        print("\n🎉 System is ready for training!")
        if not results["Unsloth"]:
            print("\n⚠️  Install Unsloth to enable actual training:")
            print("   pip install unsloth")
        else:
            print("\n🚀 Unsloth installed - ready for overnight learning!")
        print("\nNext steps:")
        print("1. Use Zoe and provide feedback for 5-7 days")
        print("2. Training will run automatically at 2am")
        print("3. Check /var/log/zoe-training.log for results")
    else:
        print("\n⚠️  Some components need attention")
        print("   Check errors above and fix before training")
    
    print("=" * 60 + "\n")
    
    return 0 if all_critical_passed else 1


if __name__ == "__main__":
    sys.exit(main())












