"""Dynamic developer chat - LLM analyzes real data"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, Dict, Any
import subprocess
import json
import sys
sys.path.append('/app')
from ai_client import ai_client

router = APIRouter(prefix="/api/developer")

class ChatMessage(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = {}

def gather_system_data(query: str) -> Dict[str, Any]:
    """Gather real system data based on query"""
    data = {}
    query_lower = query.lower()
    
    # Determine what data to gather
    commands = []
    
    if 'health' in query_lower or 'status' in query_lower or 'check' in query_lower:
        commands.extend([
            ("containers", "docker ps --format 'table {{.Names}}\t{{.Status}}'"),
            ("health", "curl -s http://localhost:8000/health"),
            ("memory", "free -h"),
            ("disk", "df -h /"),
            ("errors", "docker logs zoe-core --tail 5 2>&1 | grep -i error || echo 'No recent errors'")
        ])
    elif 'performance' in query_lower or 'optimize' in query_lower:
        commands.extend([
            ("stats", "docker stats --no-stream --format 'table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}'"),
            ("processes", "ps aux --sort=-%cpu | head -5"),
            ("io", "iostat -x 1 2 | tail -n 20 || echo 'iostat not available'")
        ])
    else:
        # Default minimal data
        commands.append(("basic", "docker ps --format '{{.Names}}: {{.Status}}' | head -5"))
    
    # Execute commands and gather real data
    for label, cmd in commands:
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=3)
            if result.stdout:
                data[label] = result.stdout.strip()
        except Exception as e:
            data[label] = f"Error: {str(e)}"
    
    return data

def create_plan_from_request(query: str) -> Dict[str, Any]:
    """Create a plan structure based on the request"""
    query_lower = query.lower()
    
    # Determine request type
    if any(word in query_lower for word in ['create', 'build', 'implement', 'add', 'fix']):
        plan_type = "development"
    elif any(word in query_lower for word in ['check', 'status', 'health', 'diagnose']):
        plan_type = "diagnostic"
    elif any(word in query_lower for word in ['optimize', 'improve', 'speed up']):
        plan_type = "optimization"
    else:
        plan_type = "analysis"
    
    # Basic plan structure
    plan = {
        "title": query[:60],
        "type": plan_type,
        "phases": [],
        "auto_generated": True
    }
    
    return plan

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    """Let LLM analyze real data and respond"""
    
    # Step 1: Gather real system data
    system_data = gather_system_data(msg.message)
    
    # Step 2: Create prompt for LLM with real data
    analysis_prompt = f"""You are an executive technical assistant. Analyze this request and system data.

USER REQUEST: {msg.message}

REAL SYSTEM DATA:
{json.dumps(system_data, indent=2)}

Provide an EXECUTIVE SUMMARY in exactly this format:
- Start with "**Request:** [brief description]"
- Include 2-3 key findings from the data
- Maximum 5 lines total
- Use ✅ for good, ⚠️ for warning, ❌ for problems
- End with one clear action recommendation

Be specific about the actual data you see. Don't make assumptions."""

    # Step 3: Get LLM analysis
    try:
        llm_response = await ai_client.generate_response(
            analysis_prompt, 
            {"mode": "assistant", "temperature": 0.3}
        )
        response_text = llm_response.get("response", "Analysis failed")
    except Exception as e:
        response_text = f"**Error:** Could not analyze. {str(e)}"
    
    # Step 4: Generate a plan if appropriate
    plan = None
    if any(word in msg.message.lower() for word in ['create', 'build', 'plan', 'implement']):
        plan = create_plan_from_request(msg.message)
        
        # Let LLM enhance the plan based on data
        plan_prompt = f"""Based on this request: {msg.message}
And system state: {list(system_data.keys())}

Create 4-5 implementation steps. Output as JSON array of steps.
Example: [{{"step": 1, "action": "Analyze requirements", "status": "pending"}}]"""
        
        try:
            plan_response = await ai_client.generate_response(plan_prompt, {"mode": "assistant", "temperature": 0.2})
            # Try to parse steps from response
            import re
            steps = re.findall(r'"action":\s*"([^"]+)"', plan_response.get("response", ""))
            plan["phases"] = [{"step": i+1, "action": step, "status": "pending"} for i, step in enumerate(steps[:5])]
        except:
            # Fallback phases
            plan["phases"] = [
                {"step": 1, "action": "Gather requirements", "status": "pending"},
                {"step": 2, "action": "Design solution", "status": "pending"},
                {"step": 3, "action": "Implement", "status": "pending"},
                {"step": 4, "action": "Test", "status": "pending"}
            ]
    
    return {
        "response": response_text,
        "plan": plan,
        "data_points": len(system_data)  # For debugging
    }

@router.get("/status")
async def status():
    return {"status": "operational", "mode": "dynamic-llm"}
