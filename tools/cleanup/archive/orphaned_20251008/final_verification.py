#!/usr/bin/env python3
"""
Final Verification - Enhancement Systems Complete
=================================================

Verify all systems are working and documentation is updated.
"""

import requests
import json
import os
from pathlib import Path

def final_verification():
    """Final verification of all enhancement systems"""
    print("🔍 FINAL VERIFICATION - ENHANCEMENT SYSTEMS")
    print("=" * 55)
    
    results = {}
    
    # 1. Verify API Health
    print("\n🏥 Verifying System Health...")
    try:
        response = requests.get("http://localhost:8000/health", timeout=5)
        if response.status_code == 200:
            data = response.json()
            enhancements_loaded = data.get("enhancements_loaded", False)
            version = data.get("version", "unknown")
            features = data.get("features", [])
            
            enhancement_features = [
                "temporal_memory",
                "cross_agent_collaboration", 
                "user_satisfaction_tracking",
                "context_summarization_cache"
            ]
            
            features_present = sum(1 for feature in enhancement_features if feature in features)
            
            print(f"  ✅ System Version: {version}")
            print(f"  ✅ Enhancements Loaded: {enhancements_loaded}")
            print(f"  ✅ Enhancement Features: {features_present}/4")
            
            results["system_health"] = {
                "success": True,
                "version": version,
                "enhancements_loaded": enhancements_loaded,
                "features_present": features_present
            }
        else:
            print(f"  ❌ Health check failed: {response.status_code}")
            results["system_health"] = {"success": False, "error": response.status_code}
    except Exception as e:
        print(f"  ❌ Health check error: {e}")
        results["system_health"] = {"success": False, "error": str(e)}
    
    # 2. Verify Enhancement Endpoints
    print("\n🌐 Verifying Enhancement Endpoints...")
    endpoints_to_test = [
        ("/api/temporal-memory/stats?user_id=test", "Temporal Memory"),
        ("/api/orchestration/experts", "Cross-Agent Orchestration"),
        ("/api/satisfaction/levels", "User Satisfaction")
    ]
    
    endpoint_results = {}
    for endpoint, name in endpoints_to_test:
        try:
            response = requests.get(f"http://localhost:8000{endpoint}", timeout=5)
            if response.status_code == 200:
                print(f"  ✅ {name}: Working")
                endpoint_results[name] = {"success": True, "status": response.status_code}
            else:
                print(f"  ❌ {name}: Failed ({response.status_code})")
                endpoint_results[name] = {"success": False, "status": response.status_code}
        except Exception as e:
            print(f"  ❌ {name}: Error - {e}")
            endpoint_results[name] = {"success": False, "error": str(e)}
    
    results["endpoints"] = endpoint_results
    
    # 3. Verify Documentation
    print("\n📚 Verifying Documentation...")
    docs_to_check = [
        ("ROADMAP.md", "Updated Roadmap"),
        ("ZOE_CURRENT_STATE.md", "Current State"),
        ("CLAUDE_CURRENT_STATE.md", "Claude State"),
        ("ENHANCEMENT_SYSTEMS_FINAL_REPORT.md", "Final Report"),
        ("documentation/ADR-001-Enhancement-Systems-Architecture.md", "Architecture ADR"),
        ("documentation/Integration-Patterns.md", "Integration Patterns")
    ]
    
    doc_results = {}
    for doc_file, name in docs_to_check:
        if Path(doc_file).exists():
            # Check if file contains enhancement system references
            with open(doc_file, 'r') as f:
                content = f.read()
            
            enhancement_mentions = sum(1 for term in ["temporal_memory", "cross_agent", "satisfaction", "context_cache"] 
                                     if term.lower() in content.lower())
            
            if enhancement_mentions > 0:
                print(f"  ✅ {name}: Updated ({enhancement_mentions} enhancement mentions)")
                doc_results[name] = {"success": True, "mentions": enhancement_mentions}
            else:
                print(f"  ⚠️ {name}: Exists but no enhancement mentions")
                doc_results[name] = {"success": False, "mentions": 0}
        else:
            print(f"  ❌ {name}: Not found")
            doc_results[name] = {"success": False, "error": "File not found"}
    
    results["documentation"] = doc_results
    
    # 4. Final Web Chat Test
    print("\n💬 Final Web Chat Verification...")
    try:
        response = requests.post("http://localhost:8000/api/chat",
            json={"message": "Tell me about your new enhancement systems and how they help users."},
            params={"user_id": "final_verification_user"},
            timeout=15
        )
        
        if response.status_code == 200:
            data = response.json()
            response_text = data.get('response', '')
            response_time = data.get('response_time', 0)
            
            # Check for enhancement system awareness
            enhancement_terms = ["temporal", "memory", "collaboration", "orchestration", "satisfaction", "enhancement"]
            awareness_score = sum(1 for term in enhancement_terms if term.lower() in response_text.lower())
            
            print(f"  ✅ Chat Response Received")
            print(f"  ⏱️ Response Time: {response_time:.2f}s")
            print(f"  🧠 Enhancement Awareness: {awareness_score}/6")
            print(f"  💬 Response: {response_text[:150]}...")
            
            results["final_chat_test"] = {
                "success": True,
                "response_time": response_time,
                "awareness_score": awareness_score,
                "shows_enhancement_awareness": awareness_score >= 3
            }
        else:
            print(f"  ❌ Chat test failed: {response.status_code}")
            results["final_chat_test"] = {"success": False, "error": response.status_code}
    except Exception as e:
        print(f"  ❌ Chat test error: {e}")
        results["final_chat_test"] = {"success": False, "error": str(e)}
    
    # Final Assessment
    print("\n" + "=" * 55)
    print("🎯 FINAL VERIFICATION RESULTS")
    print("=" * 55)
    
    # Calculate overall success
    total_checks = 0
    successful_checks = 0
    
    for category, category_results in results.items():
        if isinstance(category_results, dict):
            if "success" in category_results:
                total_checks += 1
                if category_results["success"]:
                    successful_checks += 1
            else:
                # Handle nested results (like endpoints and documentation)
                for item_name, item_result in category_results.items():
                    total_checks += 1
                    if item_result.get("success", False):
                        successful_checks += 1
    
    success_rate = (successful_checks / total_checks) * 100 if total_checks > 0 else 0
    
    print(f"System Health:                 {'✅ EXCELLENT' if results['system_health']['success'] else '❌ ISSUES'}")
    print(f"API Endpoints:                 {'✅ WORKING' if all(r['success'] for r in results['endpoints'].values()) else '❌ ISSUES'}")
    print(f"Documentation:                 {'✅ UPDATED' if sum(1 for r in results['documentation'].values() if r['success']) >= 4 else '❌ INCOMPLETE'}")
    print(f"Web Chat Integration:          {'✅ FUNCTIONAL' if results['final_chat_test']['success'] else '❌ ISSUES'}")
    
    print("-" * 55)
    print(f"Overall Success Rate:          {success_rate:.1f}% ({successful_checks}/{total_checks})")
    
    if success_rate >= 90:
        print("🎉 FINAL STATUS: OUTSTANDING SUCCESS - FULLY DEPLOYED")
        final_status = "OUTSTANDING"
    elif success_rate >= 75:
        print("✅ FINAL STATUS: EXCELLENT - READY FOR USERS")
        final_status = "EXCELLENT"
    elif success_rate >= 50:
        print("⚠️  FINAL STATUS: GOOD - MINOR ISSUES")
        final_status = "GOOD"
    else:
        print("❌ FINAL STATUS: NEEDS WORK")
        final_status = "NEEDS_WORK"
    
    # User readiness assessment
    user_ready = (
        results["system_health"]["success"] and
        results["final_chat_test"]["success"] and
        results["final_chat_test"].get("shows_enhancement_awareness", False)
    )
    
    print(f"\n🚀 USER READINESS: {'✅ READY FOR REAL USERS' if user_ready else '⚠️ NEEDS MORE WORK'}")
    
    return {
        "results": results,
        "success_rate": success_rate,
        "final_status": final_status,
        "user_ready": user_ready
    }

if __name__ == "__main__":
    verification_results = final_verification()
    
    # Save results
    with open('/home/pi/final_verification_results.json', 'w') as f:
        json.dump(verification_results, f, indent=2)
    
    print(f"\n📊 Complete verification results saved to: final_verification_results.json")
    
    if verification_results["user_ready"]:
        print("\n🎊 ENHANCEMENT SYSTEMS DEPLOYMENT: COMPLETE AND SUCCESSFUL!")
    else:
        print("\n⚠️  ENHANCEMENT SYSTEMS DEPLOYMENT: NEEDS FINAL TOUCHES")
    
    exit(0 if verification_results["user_ready"] else 1)


