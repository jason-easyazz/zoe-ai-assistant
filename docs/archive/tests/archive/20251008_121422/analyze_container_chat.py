#!/usr/bin/env python3
"""
Analyze Container Chat Router
============================

Analyze the complex routing logic in the container's chat router.
"""

import subprocess
import json

def analyze_container_chat():
    """Analyze the chat router in the container"""
    print("🔍 ANALYZING CONTAINER CHAT ROUTER")
    print("=" * 50)
    
    # Get the actual chat router from container
    try:
        result = subprocess.run([
            'docker', 'exec', 'zoe-core-test', 
            'head', '-100', '/app/routers/chat.py'
        ], capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            content = result.stdout
            print("📄 Container Chat Router (first 100 lines):")
            print(content)
            
            # Analyze the routing logic
            routing_indicators = {
                "Enhanced MEM Agent": "enhanced_mem_agent" in content.lower(),
                "Full AI Pipeline": "get_ai_response" in content.lower(),
                "Temporal Integration": "temporal" in content.lower(),
                "Complex Routing": "if" in content and content.count("if") > 5,
                "Multiple Response Paths": "return" in content and content.count("return") > 2
            }
            
            print(f"\n🔍 Routing Analysis:")
            for indicator, present in routing_indicators.items():
                status = "✅ PRESENT" if present else "❌ ABSENT"
                print(f"  {indicator}: {status}")
            
            return content
        else:
            print(f"❌ Could not read container chat router: {result.stderr}")
            return None
    except Exception as e:
        print(f"❌ Analysis error: {e}")
        return None

def find_routing_decision_points(content):
    """Find the decision points in the routing logic"""
    if not content:
        return []
    
    decision_points = []
    lines = content.split('\n')
    
    for i, line in enumerate(lines):
        if 'if' in line.lower() and ('message' in line.lower() or 'request' in line.lower()):
            decision_points.append({
                "line_number": i + 1,
                "condition": line.strip(),
                "type": "routing_decision"
            })
        elif 'mem_agent' in line.lower() or 'enhanced' in line.lower():
            decision_points.append({
                "line_number": i + 1,
                "condition": line.strip(),
                "type": "mem_agent_path"
            })
        elif 'get_ai_response' in line.lower() or 'ollama' in line.lower():
            decision_points.append({
                "line_number": i + 1,
                "condition": line.strip(),
                "type": "full_ai_path"
            })
    
    return decision_points

if __name__ == "__main__":
    content = analyze_container_chat()
    
    if content:
        decision_points = find_routing_decision_points(content)
        
        print(f"\n🎯 ROUTING DECISION POINTS FOUND: {len(decision_points)}")
        for point in decision_points[:10]:  # Show first 10
            print(f"  Line {point['line_number']}: {point['type']} - {point['condition'][:60]}...")
        
        # Save analysis
        with open('/home/pi/chat_routing_analysis.json', 'w') as f:
            json.dump({
                "decision_points": decision_points,
                "content_length": len(content),
                "analysis_timestamp": "2025-10-06T22:00:00Z"
            }, f, indent=2)
        
        print(f"\n📊 Analysis saved to: chat_routing_analysis.json")
        print("🎯 Ready to design hybrid solution...")
    else:
        print("❌ Could not analyze - will proceed with best-guess implementation")


