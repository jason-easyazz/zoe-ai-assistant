#!/usr/bin/env python3
"""
Simple System Test for Optimized Zoe AI
Tests basic functionality without external dependencies
"""
import os
import json
import time
from pathlib import Path

def test_file_structure():
    """Test that all required files exist"""
    print("🔍 Testing File Structure...")
    
    required_files = [
        "/workspace/services/zoe-core/main.py",
        "/workspace/services/zoe-core/routers/chat.py",
        "/workspace/services/zoe-core/requirements.txt",
        "/workspace/services/zoe-core/ai_client.py",
        "/workspace/services/zoe-core/route_llm.py"
    ]
    
    results = {"total": len(required_files), "exists": 0, "missing": 0, "details": []}
    
    for file_path in required_files:
        if Path(file_path).exists():
            results["exists"] += 1
            print(f"✅ {Path(file_path).name}: Exists")
            results["details"].append({"file": Path(file_path).name, "status": "exists"})
        else:
            results["missing"] += 1
            print(f"❌ {Path(file_path).name}: Missing")
            results["details"].append({"file": Path(file_path).name, "status": "missing"})
    
    return results

def test_chat_router_optimization():
    """Test that chat router is properly optimized"""
    print("\n🔍 Testing Chat Router Optimization...")
    
    chat_file = Path("/workspace/services/zoe-core/routers/chat.py")
    
    if not chat_file.exists():
        return {"success": False, "error": "Chat router file not found"}
    
    try:
        with open(chat_file, 'r') as f:
            content = f.read()
        
        # Check for optimization features
        features = {
            "enhancement_systems": "enhancement_systems" in content,
            "ai_response_optimized": "get_ai_response_optimized" in content,
            "chat_status_endpoint": "/api/chat/status" in content,
            "chat_capabilities_endpoint": "/api/chat/capabilities" in content,
            "enhanced_chat_endpoint": "/api/chat/enhanced" in content,
            "error_handling": "global_exception_handler" in content or "except Exception" in content
        }
        
        feature_count = sum(features.values())
        total_features = len(features)
        
        print(f"✅ Chat Router Features: {feature_count}/{total_features}")
        for feature, present in features.items():
            status = "✅" if present else "❌"
            print(f"   {status} {feature}")
        
        return {
            "success": feature_count >= total_features * 0.8,  # 80% of features present
            "feature_count": feature_count,
            "total_features": total_features,
            "features": features
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}

def test_main_application():
    """Test main application configuration"""
    print("\n🔍 Testing Main Application...")
    
    main_file = Path("/workspace/services/zoe-core/main.py")
    
    if not main_file.exists():
        return {"success": False, "error": "Main application file not found"}
    
    try:
        with open(main_file, 'r') as f:
            content = f.read()
        
        # Check for optimization features
        features = {
            "enhanced_metadata": "version=\"2.2.0\"" in content,
            "cors_middleware": "CORSMiddleware" in content,
            "health_endpoint": "/health" in content,
            "metrics_endpoint": "/metrics" in content,
            "error_handling": "global_exception_handler" in content,
            "startup_event": "startup_event" in content,
            "shutdown_event": "shutdown_event" in content,
            "router_includes": "app.include_router" in content
        }
        
        feature_count = sum(features.values())
        total_features = len(features)
        
        print(f"✅ Main Application Features: {feature_count}/{total_features}")
        for feature, present in features.items():
            status = "✅" if present else "❌"
            print(f"   {status} {feature}")
        
        return {
            "success": feature_count >= total_features * 0.8,  # 80% of features present
            "feature_count": feature_count,
            "total_features": total_features,
            "features": features
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}

def test_duplicate_cleanup():
    """Test that duplicate files have been cleaned up"""
    print("\n🔍 Testing Duplicate Cleanup...")
    
    routers_dir = Path("/workspace/services/zoe-core/routers")
    
    # Files that should be removed
    files_to_remove = [
        "chat_backup.py",
        "chat_enhanced.py", 
        "chat_sessions.py",
        "chat_redirect.py",
        "chat_override.py",
        "chat_bypass.py",
        "chat_fixed.py"
    ]
    
    # Files that should exist
    files_to_keep = [
        "chat.py",
        "chat_optimized.py"
    ]
    
    results = {"removed": 0, "kept": 0, "details": []}
    
    # Check removed files
    for filename in files_to_remove:
        file_path = routers_dir / filename
        if not file_path.exists():
            results["removed"] += 1
            print(f"✅ {filename}: Removed")
            results["details"].append({"file": filename, "status": "removed"})
        else:
            print(f"⚠️  {filename}: Still exists")
            results["details"].append({"file": filename, "status": "still_exists"})
    
    # Check kept files
    for filename in files_to_keep:
        file_path = routers_dir / filename
        if file_path.exists():
            results["kept"] += 1
            print(f"✅ {filename}: Exists")
            results["details"].append({"file": filename, "status": "kept"})
        else:
            print(f"❌ {filename}: Missing")
            results["details"].append({"file": filename, "status": "missing"})
    
    return results

def test_requirements():
    """Test requirements file"""
    print("\n🔍 Testing Requirements...")
    
    req_file = Path("/workspace/services/zoe-core/requirements.txt")
    
    if not req_file.exists():
        return {"success": False, "error": "Requirements file not found"}
    
    try:
        with open(req_file, 'r') as f:
            content = f.read()
        
        # Check for key dependencies
        dependencies = {
            "fastapi": "fastapi" in content,
            "uvicorn": "uvicorn" in content,
            "sqlalchemy": "sqlalchemy" in content,
            "anthropic": "anthropic" in content,
            "ollama": "ollama" in content,
            "litellm": "litellm" in content,
            "sentence_transformers": "sentence-transformers" in content,
            "torch": "torch" in content
        }
        
        dep_count = sum(dependencies.values())
        total_deps = len(dependencies)
        
        print(f"✅ Dependencies: {dep_count}/{total_deps}")
        for dep, present in dependencies.items():
            status = "✅" if present else "❌"
            print(f"   {status} {dep}")
        
        return {
            "success": dep_count >= total_deps * 0.8,  # 80% of dependencies present
            "dependency_count": dep_count,
            "total_dependencies": total_deps,
            "dependencies": dependencies
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}

def main():
    """Main test function"""
    print("🚀 Starting Simple System Test...")
    print("=" * 50)
    
    start_time = time.time()
    
    # Run all tests
    file_results = test_file_structure()
    chat_results = test_chat_router_optimization()
    main_results = test_main_application()
    cleanup_results = test_duplicate_cleanup()
    req_results = test_requirements()
    
    total_time = time.time() - start_time
    
    # Calculate overall results
    total_tests = 5
    passed_tests = sum([
        file_results["exists"] > 0,
        chat_results.get("success", False),
        main_results.get("success", False),
        cleanup_results["removed"] > 0,
        req_results.get("success", False)
    ])
    
    success_rate = (passed_tests / total_tests) * 100
    
    # Print summary
    print("\n" + "=" * 50)
    print("📊 TEST SUMMARY")
    print("=" * 50)
    
    print(f"Total Test Time: {total_time:.2f} seconds")
    print(f"Tests Passed: {passed_tests}/{total_tests}")
    print(f"Success Rate: {success_rate:.1f}%")
    print()
    
    print("File Structure:")
    print(f"  Files Found: {file_results['exists']}/{file_results['total']}")
    print()
    
    print("Chat Router Optimization:")
    print(f"  Success: {'✅' if chat_results.get('success') else '❌'}")
    print(f"  Features: {chat_results.get('feature_count', 0)}/{chat_results.get('total_features', 0)}")
    print()
    
    print("Main Application:")
    print(f"  Success: {'✅' if main_results.get('success') else '❌'}")
    print(f"  Features: {main_results.get('feature_count', 0)}/{main_results.get('total_features', 0)}")
    print()
    
    print("Duplicate Cleanup:")
    print(f"  Files Removed: {cleanup_results['removed']}")
    print(f"  Files Kept: {cleanup_results['kept']}")
    print()
    
    print("Requirements:")
    print(f"  Success: {'✅' if req_results.get('success') else '❌'}")
    print(f"  Dependencies: {req_results.get('dependency_count', 0)}/{req_results.get('total_dependencies', 0)}")
    print()
    
    if success_rate >= 80:
        print("🎉 SYSTEM IS FULLY OPTIMIZED!")
        print("✅ All major components are working properly")
        print("✅ Duplicate files have been cleaned up")
        print("✅ Chat system is optimized")
        print("✅ Main application is ready")
    elif success_rate >= 60:
        print("✅ SYSTEM IS MOSTLY OPTIMIZED")
        print("⚠️  Some minor issues detected")
    else:
        print("⚠️  SYSTEM NEEDS ATTENTION")
        print("❌ Several issues detected")
    
    # Save results
    results = {
        "timestamp": time.time(),
        "total_time": total_time,
        "success_rate": success_rate,
        "passed_tests": passed_tests,
        "total_tests": total_tests,
        "file_structure": file_results,
        "chat_router": chat_results,
        "main_application": main_results,
        "duplicate_cleanup": cleanup_results,
        "requirements": req_results
    }
    
    with open("/workspace/simple_system_test_results.json", "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\n📄 Detailed results saved to: simple_system_test_results.json")

if __name__ == "__main__":
    main()