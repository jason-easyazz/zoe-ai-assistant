"""
Agent Zero Module Intent Handlers
==================================

Handles Agent Zero intents by calling the bridge module's HTTP endpoints.
Auto-discovered and registered by zoe-core.

Architecture:
  User says "research smart light bulbs"
    → zoe-core intent system matches AgentZeroResearch
    → Calls handle_agent_zero_research() from this file
    → Handler calls http://agent-zero-bridge:8101/tools/research
    → Bridge talks to Agent Zero via WebSocket
    → Returns formatted result to user

Intent-to-Handler Mapping:
  - AgentZeroResearch → handle_agent_zero_research()
  - AgentZeroPlan → handle_agent_zero_plan()
  - AgentZeroAnalyze → handle_agent_zero_analyze()
  - AgentZeroCompare → handle_agent_zero_compare()
"""

import logging
import httpx
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Agent Zero bridge URL (internal Docker service)
AGENT_ZERO_BRIDGE_URL = "http://agent-zero-bridge:8101"


async def handle_agent_zero_research(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle AgentZeroResearch intent.
    
    Example triggers:
      - "research smart light bulbs"
      - "look up home automation systems"
      - "tell me about Agent Zero"
      - "what do you know about Zigbee?"
    
    Args:
        intent: Intent object with slots
        user_id: User identifier
        context: Conversation context
        
    Returns:
        Result dict with success, message, and optional data
    """
    query = intent.slots.get("query")
    
    if not query:
        return {
            "success": False,
            "message": "What would you like me to research?"
        }
    
    logger.info(f"🔍 Agent Zero research: {query}")
    
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:  # Agent Zero can take time
            response = await client.post(
                f"{AGENT_ZERO_BRIDGE_URL}/tools/research",
                json={
                    "query": query,
                    "depth": "thorough",
                    "user_id": user_id
                }
            )
            result = response.json()
            
            if result.get("success"):
                summary = result.get("summary", "Research complete.")
                sources = result.get("sources", [])
                
                # Format sources for display
                sources_text = ""
                if sources:
                    sources_text = "\n\n📚 Sources:\n" + "\n".join([f"  • {s}" for s in sources[:3]])
                
                message = f"🔍 {summary}{sources_text}"
                
                return {
                    "success": True,
                    "message": message,
                    "data": result
                }
            else:
                error_msg = result.get("message", "I had trouble researching that.")
                return {
                    "success": False,
                    "message": error_msg
                }
                
    except httpx.TimeoutException:
        logger.warning(f"Agent Zero research timeout: {query}")
        return {
            "success": False,
            "message": "Research is taking longer than expected. Try a simpler query?"
        }
    except httpx.ConnectError:
        logger.error(f"Agent Zero bridge not reachable")
        return {
            "success": False,
            "message": "Agent Zero is unavailable right now. The bridge service might not be running."
        }
    except Exception as e:
        logger.error(f"Agent Zero research failed: {e}")
        return {
            "success": False,
            "message": "Something went wrong with the research task."
        }


async def handle_agent_zero_plan(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle AgentZeroPlan intent.
    
    Example triggers:
      - "plan my home automation setup"
      - "how do I set up Home Assistant?"
      - "create a plan to organize my music library"
      - "guide me through installing Docker"
    
    Args:
        intent: Intent object with slots
        user_id: User identifier
        context: Conversation context
        
    Returns:
        Result dict with success, message, and optional data
    """
    task = intent.slots.get("task")
    
    if not task:
        return {
            "success": False,
            "message": "What would you like help planning?"
        }
    
    logger.info(f"📋 Agent Zero plan: {task}")
    
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                f"{AGENT_ZERO_BRIDGE_URL}/tools/plan",
                json={
                    "task": task,
                    "user_id": user_id
                }
            )
            result = response.json()
            
            if result.get("success"):
                steps = result.get("steps", [])
                
                if not steps:
                    return {
                        "success": False,
                        "message": "I couldn't create a plan for that. Try being more specific?"
                    }
                
                # Format steps for display
                steps_text = "\n".join([f"  {i+1}. {step}" for i, step in enumerate(steps)])
                
                complexity = result.get("complexity", "")
                time_est = result.get("estimated_time", "")
                
                message = f"📋 Here's your plan:\n\n{steps_text}"
                
                if complexity:
                    message += f"\n\n💡 Complexity: {complexity}"
                if time_est:
                    message += f"\n⏱️  Estimated time: {time_est}"
                
                return {
                    "success": True,
                    "message": message,
                    "data": result
                }
            else:
                error_msg = result.get("message", "I couldn't create a plan for that.")
                return {
                    "success": False,
                    "message": error_msg
                }
                
    except httpx.TimeoutException:
        logger.warning(f"Agent Zero planning timeout: {task}")
        return {
            "success": False,
            "message": "Planning is taking longer than expected."
        }
    except httpx.ConnectError:
        logger.error(f"Agent Zero bridge not reachable")
        return {
            "success": False,
            "message": "Agent Zero is unavailable right now."
        }
    except Exception as e:
        logger.error(f"Agent Zero planning failed: {e}")
        return {
            "success": False,
            "message": "Something went wrong with the planning task."
        }


async def handle_agent_zero_analyze(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle AgentZeroAnalyze intent.
    
    Example triggers:
      - "analyze my Home Assistant setup"
      - "review my docker-compose.yml"
      - "check my network configuration"
      - "evaluate my smart home devices"
    
    Args:
        intent: Intent object with slots
        user_id: User identifier
        context: Conversation context
        
    Returns:
        Result dict with success, message, and optional data
    """
    target = intent.slots.get("target")
    
    if not target:
        return {
            "success": False,
            "message": "What would you like me to analyze?"
        }
    
    logger.info(f"🔬 Agent Zero analyze: {target}")
    
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                f"{AGENT_ZERO_BRIDGE_URL}/tools/analyze",
                json={
                    "target": target,
                    "user_id": user_id
                }
            )
            result = response.json()
            
            if result.get("success"):
                analysis = result.get("analysis", "Analysis complete.")
                findings = result.get("findings", [])
                recommendations = result.get("recommendations", [])
                
                message = f"🔬 {analysis}"
                
                if findings:
                    findings_text = "\n".join([f"  • {f}" for f in findings[:5]])
                    message += f"\n\n📊 Findings:\n{findings_text}"
                
                if recommendations:
                    recs_text = "\n".join([f"  • {r}" for r in recommendations[:5]])
                    message += f"\n\n💡 Recommendations:\n{recs_text}"
                
                return {
                    "success": True,
                    "message": message,
                    "data": result
                }
            else:
                error_msg = result.get("message", "Analysis failed.")
                return {
                    "success": False,
                    "message": error_msg
                }
                
    except httpx.TimeoutException:
        logger.warning(f"Agent Zero analysis timeout: {target}")
        return {
            "success": False,
            "message": "Analysis is taking longer than expected."
        }
    except httpx.ConnectError:
        logger.error(f"Agent Zero bridge not reachable")
        return {
            "success": False,
            "message": "Agent Zero is unavailable right now."
        }
    except Exception as e:
        logger.error(f"Agent Zero analysis failed: {e}")
        return {
            "success": False,
            "message": "Something went wrong with the analysis task."
        }


async def handle_agent_zero_compare(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle AgentZeroCompare intent.
    
    Example triggers:
      - "compare Zigbee and Z-Wave"
      - "Home Assistant versus OpenHAB"
      - "which is better, Plex or Jellyfin?"
      - "difference between Docker and Kubernetes"
    
    Args:
        intent: Intent object with slots
        user_id: User identifier
        context: Conversation context
        
    Returns:
        Result dict with success, message, and optional data
    """
    item_a = intent.slots.get("item_a")
    item_b = intent.slots.get("item_b")
    
    if not item_a or not item_b:
        return {
            "success": False,
            "message": "What two things would you like me to compare?"
        }
    
    logger.info(f"⚖️ Agent Zero compare: {item_a} vs {item_b}")
    
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{AGENT_ZERO_BRIDGE_URL}/tools/compare",
                json={
                    "item_a": item_a,
                    "item_b": item_b,
                    "user_id": user_id
                }
            )
            result = response.json()
            
            if result.get("success"):
                comparison = result.get("comparison", "Comparison complete.")
                winner = result.get("recommendation")
                
                message = f"⚖️ {comparison}"
                
                if winner:
                    message += f"\n\n💡 Recommendation: {winner}"
                
                return {
                    "success": True,
                    "message": message,
                    "data": result
                }
            else:
                error_msg = result.get("message", "Comparison failed.")
                return {
                    "success": False,
                    "message": error_msg
                }
                
    except httpx.TimeoutException:
        logger.warning(f"Agent Zero comparison timeout: {item_a} vs {item_b}")
        return {
            "success": False,
            "message": "Comparison is taking longer than expected."
        }
    except httpx.ConnectError:
        logger.error(f"Agent Zero bridge not reachable")
        return {
            "success": False,
            "message": "Agent Zero is unavailable right now."
        }
    except Exception as e:
        logger.error(f"Agent Zero comparison failed: {e}")
        return {
            "success": False,
            "message": "Something went wrong with the comparison task."
        }


# Handler mapping for auto-discovery by zoe-core
# This dict is imported by zoe-core's module intent loader
INTENT_HANDLERS = {
    "AgentZeroResearch": handle_agent_zero_research,
    "AgentZeroPlan": handle_agent_zero_plan,
    "AgentZeroAnalyze": handle_agent_zero_analyze,
    "AgentZeroCompare": handle_agent_zero_compare,
}
