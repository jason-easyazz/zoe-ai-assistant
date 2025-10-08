#!/usr/bin/env python3
"""
Comprehensive System Audit and Optimization for Zoe
Tests all components, models, and configurations
"""

import asyncio
import httpx
import time
import json
import subprocess
import sys
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime

@dataclass
class TestResult:
    component: str
    test_name: str
    success: bool
    response_time: float
    error: Optional[str] = None
    data: Optional[Dict] = None

class ZoeSystemAuditor:
    def __init__(self):
        self.results: List[TestResult] = []
        self.models_to_test = [
            "llama3.2:1b",    # Current
            "llama3.2:3b",    # Existing
            "gemma3:1b",      # New fast
            "qwen2.5:1.5b",   # New fast
            "qwen2.5:3b",     # Existing
            "gemma:2b",        # Existing
            "phi3:mini",       # Existing
            "mistral:latest",  # Existing
            "codellama:7b"     # Existing
        ]
        
        self.test_prompts = [
            {
                "message": "Add bread to shopping list",
                "expected_tool": "add_to_list",
                "type": "direct_action",
                "priority": "high"
            },
            {
                "message": "Turn on the living room light",
                "expected_tool": "control_home_assistant_device",
                "type": "direct_action", 
                "priority": "high"
            },
            {
                "message": "What tools do you have available?",
                "expected_tool": None,
                "type": "information",
                "priority": "medium"
            },
            {
                "message": "How are you today?",
                "type": "conversation",
                "priority": "low"
            },
            {
                "message": "Send a message to Matrix room",
                "expected_tool": "send_matrix_message",
                "type": "direct_action",
                "priority": "medium"
            }
        ]
    
    async def test_component(self, component: str, test_name: str, test_func) -> TestResult:
        """Run a single component test"""
        start_time = time.time()
        try:
            result = await test_func()
            response_time = time.time() - start_time
            
            return TestResult(
                component=component,
                test_name=test_name,
                success=True,
                response_time=response_time,
                data=result
            )
        except Exception as e:
            response_time = time.time() - start_time
            return TestResult(
                component=component,
                test_name=test_name,
                success=False,
                response_time=response_time,
                error=str(e)
            )
    
    async def test_ollama_models(self):
        """Test all available Ollama models"""
        print("\n🧪 Testing Ollama Models...")
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Get available models
            try:
                response = await client.get("http://localhost:11434/api/tags")
                if response.status_code == 200:
                    models_data = response.json()
                    available_models = [model["name"] for model in models_data.get("models", [])]
                    print(f"✅ Found {len(available_models)} models: {available_models}")
                else:
                    print(f"❌ Failed to get models: {response.status_code}")
                    return
            except Exception as e:
                print(f"❌ Ollama connection failed: {e}")
                return
            
            # Test each model
            for model in available_models:
                print(f"\n  Testing {model}...")
                
                for prompt in self.test_prompts:
                    test_name = f"{model}_{prompt['type']}"
                    
                    async def test_model():
                        response = await client.post(
                            "http://localhost:11434/api/generate",
                            json={
                                "model": model,
                                "prompt": f"""You are Zoe, an AI assistant like Samantha from "Her" - warm but direct.

RULES:
- DIRECT ACTION: When user asks to add/do something → Use tools immediately
- CONVERSATION: When chatting → Be friendly
- CONCISE: Be brief but warm

AVAILABLE TOOLS:
• add_to_list: Add an item to a user's todo list
• control_home_assistant_device: Control a Home Assistant device
• send_matrix_message: Send a message to a Matrix room

TOOL USAGE INSTRUCTIONS:
When you need to use a tool, respond with: [TOOL_CALL:tool_name:{{"param1":"value1","param2":"value2"}}]
After tool execution, confirm the action to the user.

EXAMPLES:
- "Add bread to shopping list" → [TOOL_CALL:add_to_list:{{"list_name":"shopping","task_text":"bread","priority":"medium"}}] → "Added bread to your shopping list"
- "Turn on living room light" → [TOOL_CALL:control_home_assistant_device:{{"entity_id":"light.living_room","action":"turn_on"}}] → "Turned on the living room light"

User's message: {prompt['message']}
Zoe:""",
                                "stream": False,
                                "options": {
                                    "temperature": 0.5,
                                    "top_p": 0.8,
                                    "num_predict": 64,
                                    "num_ctx": 512,
                                    "repeat_penalty": 1.1,
                                    "stop": ["\n\n", "User:", "Human:"]
                                }
                            }
                        )
                        
                        if response.status_code == 200:
                            data = response.json()
                            response_text = data.get("response", "")
                            
                            # Analyze response
                            has_tool_call = "[TOOL_CALL:" in response_text
                            is_concise = len(response_text) < 150
                            is_warm = any(word in response_text.lower() for word in ["good", "great", "sure", "okay", "done", "added"])
                            
                            return {
                                "response_text": response_text[:100] + "..." if len(response_text) > 100 else response_text,
                                "has_tool_call": has_tool_call,
                                "is_concise": is_concise,
                                "is_warm": is_warm,
                                "response_length": len(response_text)
                            }
                        else:
                            raise Exception(f"HTTP {response.status_code}")
                    
                    result = await self.test_component("ollama", test_name, test_model)
                    self.results.append(result)
                    
                    if result.success:
                        print(f"    ✅ {prompt['message'][:30]}... - {result.response_time:.2f}s")
                        if result.data.get("has_tool_call"):
                            print(f"      🔧 Tool call detected")
                    else:
                        print(f"    ❌ {prompt['message'][:30]}... - {result.error}")
    
    async def test_mcp_server(self):
        """Test MCP Server functionality"""
        print("\n🧪 Testing MCP Server...")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Test health
            async def test_health():
                response = await client.get("http://localhost:8003/health")
                if response.status_code == 200:
                    return response.json()
                else:
                    raise Exception(f"Health check failed: {response.status_code}")
            
            result = await self.test_component("mcp_server", "health_check", test_health)
            self.results.append(result)
            
            if result.success:
                print(f"  ✅ Health check - {result.response_time:.2f}s")
            else:
                print(f"  ❌ Health check failed - {result.error}")
                return
            
            # Test tools list
            async def test_tools_list():
                response = await client.post(
                    "http://localhost:8003/tools/list",
                    json={"_auth_token": "default", "_session_id": "default"}
                )
                if response.status_code == 200:
                    return response.json()
                else:
                    raise Exception(f"Tools list failed: {response.status_code}")
            
            result = await self.test_component("mcp_server", "tools_list", test_tools_list)
            self.results.append(result)
            
            if result.success:
                tools_data = result.data
                print(f"  ✅ Tools list - {len(tools_data.get('tools', []))} tools available")
                print(f"      Categories: {tools_data.get('categories', {})}")
            else:
                print(f"  ❌ Tools list failed - {result.error}")
            
            # Test direct tool execution
            async def test_add_to_list():
                response = await client.post(
                    "http://localhost:8003/tools/add_to_list",
                    json={
                        "list_name": "test_audit",
                        "task_text": "audit test item",
                        "priority": "medium",
                        "_auth_token": "default",
                        "_session_id": "default"
                    }
                )
                if response.status_code == 200:
                    return response.json()
                else:
                    raise Exception(f"Add to list failed: {response.status_code}")
            
            result = await self.test_component("mcp_server", "add_to_list", test_add_to_list)
            self.results.append(result)
            
            if result.success:
                print(f"  ✅ Direct tool execution - {result.response_time:.2f}s")
            else:
                print(f"  ❌ Direct tool execution failed - {result.error}")
    
    async def test_zoe_core_chat(self):
        """Test Zoe Core chat functionality"""
        print("\n🧪 Testing Zoe Core Chat...")
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            for prompt in self.test_prompts:
                test_name = f"chat_{prompt['type']}"
                
                async def test_chat():
                    response = await client.post(
                        "http://localhost:8000/api/chat",
                        json={"message": prompt["message"], "user_id": "audit_user"}
                    )
                    
                    if response.status_code == 200:
                        return response.json()
                    else:
                        raise Exception(f"Chat failed: {response.status_code}")
                
                result = await self.test_component("zoe_core", test_name, test_chat)
                self.results.append(result)
                
                if result.success:
                    chat_data = result.data
                    print(f"  ✅ {prompt['message'][:30]}... - {result.response_time:.2f}s")
                    print(f"      Routing: {chat_data.get('routing', 'unknown')}")
                    print(f"      Response: {chat_data.get('response', '')[:50]}...")
                else:
                    print(f"  ❌ {prompt['message'][:30]}... - {result.error}")
    
    async def test_system_resources(self):
        """Test system resource usage"""
        print("\n🧪 Testing System Resources...")
        
        # Test memory usage
        async def test_memory():
            result = subprocess.run(["free", "-h"], capture_output=True, text=True)
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                memory_line = lines[1].split()
                return {
                    "total": memory_line[1],
                    "used": memory_line[2],
                    "free": memory_line[3],
                    "available": memory_line[6] if len(memory_line) > 6 else "N/A"
                }
            else:
                raise Exception("Failed to get memory info")
        
        result = await self.test_component("system", "memory_usage", test_memory)
        self.results.append(result)
        
        if result.success:
            mem_data = result.data
            print(f"  ✅ Memory: {mem_data['used']}/{mem_data['total']} used, {mem_data['available']} available")
        else:
            print(f"  ❌ Memory check failed - {result.error}")
        
        # Test disk usage
        async def test_disk():
            result = subprocess.run(["df", "-h", "/"], capture_output=True, text=True)
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                disk_line = lines[1].split()
                return {
                    "total": disk_line[1],
                    "used": disk_line[2],
                    "available": disk_line[3],
                    "usage_percent": disk_line[4]
                }
            else:
                raise Exception("Failed to get disk info")
        
        result = await self.test_component("system", "disk_usage", test_disk)
        self.results.append(result)
        
        if result.success:
            disk_data = result.data
            print(f"  ✅ Disk: {disk_data['used']}/{disk_data['total']} used ({disk_data['usage_percent']})")
        else:
            print(f"  ❌ Disk check failed - {result.error}")
    
    async def test_bridge_services(self):
        """Test bridge services"""
        print("\n🧪 Testing Bridge Services...")
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Test Home Assistant bridge
            async def test_ha_bridge():
                response = await client.get("http://localhost:8007/health")
                if response.status_code == 200:
                    return response.json()
                else:
                    raise Exception(f"HA bridge health failed: {response.status_code}")
            
            result = await self.test_component("ha_bridge", "health_check", test_ha_bridge)
            self.results.append(result)
            
            if result.success:
                print(f"  ✅ Home Assistant bridge - {result.response_time:.2f}s")
            else:
                print(f"  ❌ Home Assistant bridge failed - {result.error}")
            
            # Test N8N bridge
            async def test_n8n_bridge():
                response = await client.get("http://localhost:8009/health")
                if response.status_code == 200:
                    return response.json()
                else:
                    raise Exception(f"N8N bridge health failed: {response.status_code}")
            
            result = await self.test_component("n8n_bridge", "health_check", test_n8n_bridge)
            self.results.append(result)
            
            if result.success:
                print(f"  ✅ N8N bridge - {result.response_time:.2f}s")
            else:
                print(f"  ❌ N8N bridge failed - {result.error}")
    
    def analyze_results(self):
        """Analyze all test results and generate recommendations"""
        print("\n" + "="*60)
        print("📊 COMPREHENSIVE SYSTEM ANALYSIS")
        print("="*60)
        
        # Group results by component
        component_results = {}
        for result in self.results:
            if result.component not in component_results:
                component_results[result.component] = []
            component_results[result.component].append(result)
        
        # Analyze each component
        for component, results in component_results.items():
            print(f"\n🔍 {component.upper()}")
            print("-" * 40)
            
            successful_results = [r for r in results if r.success]
            failed_results = [r for r in results if not r.success]
            
            print(f"  Success Rate: {len(successful_results)}/{len(results)} ({len(successful_results)/len(results)*100:.1f}%)")
            
            if successful_results:
                avg_time = sum(r.response_time for r in successful_results) / len(successful_results)
                print(f"  Average Response Time: {avg_time:.2f}s")
            
            if failed_results:
                print(f"  Failed Tests: {len(failed_results)}")
                for failed in failed_results[:3]:  # Show first 3 failures
                    print(f"    ❌ {failed.test_name}: {failed.error}")
        
        # Model performance analysis
        print(f"\n🧠 MODEL PERFORMANCE ANALYSIS")
        print("-" * 40)
        
        ollama_results = [r for r in self.results if r.component == "ollama" and r.success]
        if ollama_results:
            # Group by model
            model_performance = {}
            for result in ollama_results:
                model_name = result.test_name.split('_')[0]
                if model_name not in model_performance:
                    model_performance[model_name] = []
                model_performance[model_name].append(result)
            
            # Calculate scores for each model
            model_scores = []
            for model, results in model_performance.items():
                avg_time = sum(r.response_time for r in results) / len(results)
                tool_call_rate = sum(1 for r in results if r.data and r.data.get("has_tool_call")) / len(results) * 100
                concise_rate = sum(1 for r in results if r.data and r.data.get("is_concise")) / len(results) * 100
                
                # Combined score: tool calling (40%) + speed (30%) + conciseness (30%)
                speed_score = max(0, 100 - (avg_time * 10))  # Penalty for slow responses
                combined_score = (tool_call_rate * 0.4) + (speed_score * 0.3) + (concise_rate * 0.3)
                
                model_scores.append({
                    "model": model,
                    "score": combined_score,
                    "avg_time": avg_time,
                    "tool_call_rate": tool_call_rate,
                    "concise_rate": concise_rate,
                    "test_count": len(results)
                })
            
            # Sort by score
            model_scores.sort(key=lambda x: x["score"], reverse=True)
            
            print("🏆 MODEL RANKINGS:")
            for i, model_data in enumerate(model_scores):
                print(f"  {i+1}. {model_data['model']}")
                print(f"     Score: {model_data['score']:.1f}/100")
                print(f"     Avg Time: {model_data['avg_time']:.2f}s")
                print(f"     Tool Call Rate: {model_data['tool_call_rate']:.1f}%")
                print(f"     Conciseness: {model_data['concise_rate']:.1f}%")
                print()
        
        # Generate recommendations
        print(f"\n🎯 RECOMMENDATIONS")
        print("-" * 40)
        
        # Find best model
        if model_scores:
            best_model = model_scores[0]["model"]
            print(f"🥇 BEST MODEL: {best_model}")
            print(f"   - Recommended for production use")
            print(f"   - Best balance of speed and tool calling")
        
        # System health
        system_results = [r for r in self.results if r.component == "system"]
        if system_results:
            print(f"\n💾 SYSTEM HEALTH:")
            for result in system_results:
                if result.success and result.data:
                    if "memory" in result.test_name:
                        mem_data = result.data
                        print(f"   Memory: {mem_data['used']}/{mem_data['total']} ({mem_data['available']} available)")
                    elif "disk" in result.test_name:
                        disk_data = result.data
                        print(f"   Disk: {disk_data['used']}/{disk_data['total']} ({disk_data['usage_percent']})")
        
        # Component health
        print(f"\n🔧 COMPONENT STATUS:")
        for component, results in component_results.items():
            if component == "system":
                continue
            success_rate = len([r for r in results if r.success]) / len(results) * 100
            status = "✅ Healthy" if success_rate > 80 else "⚠️ Issues" if success_rate > 50 else "❌ Critical"
            print(f"   {component}: {status} ({success_rate:.1f}%)")
    
    async def run_comprehensive_audit(self):
        """Run the complete system audit"""
        print("🚀 Starting Comprehensive Zoe System Audit")
        print("=" * 60)
        print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        try:
            await self.test_system_resources()
            await self.test_ollama_models()
            await self.test_mcp_server()
            await self.test_zoe_core_chat()
            await self.test_bridge_services()
            
            self.analyze_results()
            
            print(f"\n🎉 Comprehensive audit completed!")
            print(f"Total tests run: {len(self.results)}")
            successful_tests = len([r for r in self.results if r.success])
            print(f"Successful tests: {successful_tests}")
            print(f"Overall success rate: {successful_tests/len(self.results)*100:.1f}%")
            
        except KeyboardInterrupt:
            print(f"\n⏹️  Audit interrupted by user")
        except Exception as e:
            print(f"\n❌ Audit failed: {e}")

async def main():
    """Run the comprehensive audit"""
    auditor = ZoeSystemAuditor()
    await auditor.run_comprehensive_audit()

if __name__ == "__main__":
    asyncio.run(main())

