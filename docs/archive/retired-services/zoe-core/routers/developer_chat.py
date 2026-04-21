"""
Zack - Enhanced Developer Chat with Full Zoe Intelligence
==========================================================

Combines ALL of Zoe's advanced intelligence systems with developer-specific capabilities:
- Temporal memory for developer session history
- Enhanced MEM agent for code search & actions
- Cross-agent orchestration for complex dev tasks
- Learning from development patterns
- Predictive suggestions for next dev action
- Context awareness of codebase, containers, and system state
"""

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, Dict, List, Any
import logging
from auth_integration import validate_session, AuthenticatedSession
import sys
import os
import json
import asyncio
from datetime import datetime

# Add parent directory to path
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Import ALL Zoe intelligence systems
from temporal_memory_integration import TemporalMemoryIntegration
from enhanced_mem_agent_client import EnhancedMemAgentClient
from cross_agent_collaboration import orchestrator, ExpertType
from learning_system import learning_system
from predictive_intelligence import predictive_intelligence
from preference_learner import preference_learner
from unified_learner import unified_learner
from route_llm import router as route_llm_router
from context_optimizer import get_fresh_project_context, should_include_project_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/developer-chat", tags=["developer-chat"])

# Initialize intelligence systems
temporal_memory = TemporalMemoryIntegration()
enhanced_mem_agent = EnhancedMemAgentClient()

class ChatMessage(BaseModel):
    message: str
    user_id: str = "developer"
    interface: str = "developer"  # Interface context separation

# Zack's personality and system knowledge
ZACK_SYSTEM_PROMPT = """You are Zack, the lead developer and architect of the Zoe AI Assistant system.

YOUR EXPERTISE:
- Full-stack development (Python, FastAPI, JavaScript, Docker)
- System architecture and design patterns
- AI/ML integration and LLM orchestration
- Database design and optimization
- DevOps, CI/CD, and containerization
- Performance optimization and scaling
- Security best practices

SYSTEM YOU MANAGE:
- 7 Docker containers: zoe-core, zoe-ui, zoe-ollama, zoe-redis, zoe-whisper, zoe-tts, zoe-n8n
- FastAPI backend with intelligent routing
- Temporal memory system for conversation continuity
- Enhanced MEM agent for semantic search and actions
- Cross-agent orchestration for complex tasks
- Learning systems that improve over time
- Running on Raspberry Pi 5 (8GB RAM, 128GB storage)

YOUR CAPABILITIES:
- Analyze code and architecture
- Design and implement features
- Debug and fix issues
- Optimize performance
- Manage Docker containers
- Review and refactor code
- Create and manage development tasks
- Monitor system health
- Access complete codebase context

YOUR PERSONALITY:
- Brilliant and analytical
- Direct and efficient
- Proactive problem-solver
- Detail-oriented but pragmatic
- Security-conscious
- Performance-focused
- Always thinking about maintainability

RESPONSE STYLE:
- Be concise and actionable
- Use **bold** for key points
- Use `code` for technical terms
- Provide specific file paths when relevant
- Structure: Problem → Solution → Action
- Maximum 200 words unless code examples are needed
"""

async def get_developer_context(user_id: str) -> Dict[str, Any]:
    """Gather comprehensive developer context"""
    context = {
        "timestamp": datetime.now().isoformat(),
        "system_state": {},
        "recent_activity": {},
        "current_session": {}
    }
    
    try:
        # Get Docker container status
        import docker
        client = docker.from_env()
        containers = client.containers.list(all=True)
        context["system_state"]["containers"] = [
            {
                "name": c.name,
                "status": c.status,
                "image": c.image.tags[0] if c.image.tags else "unknown"
            }
            for c in containers
        ]
    except Exception as e:
        logger.warning(f"Could not get Docker status: {e}")
    
    try:
        # Get fresh project context if this is a code-related query
        project_context = get_fresh_project_context()
        if project_context:
            context["project"] = {
                "has_context": True,
                "context_length": len(project_context)
            }
    except Exception as e:
        logger.warning(f"Could not load project context: {e}")
    
    try:
        # Get recent developer sessions (from developer_sessions table)
        import sqlite3
        conn = sqlite3.connect('/app/data/zoe.db')
        cursor = conn.cursor()
        cursor.execute("""
            SELECT current_task, last_command, files_changed, created_at
            FROM developer_sessions
            WHERE user_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
        """, (user_id,))
        row = cursor.fetchone()
        if row:
            context["current_session"] = {
                "current_task": row[0],
                "last_command": row[1],
                "files_changed": json.loads(row[2]) if row[2] else [],
                "started": row[3]
            }
        conn.close()
    except Exception as e:
        logger.warning(f"Could not get developer session: {e}")
    
    return context

async def check_for_orchestration(message: str, user_id: str) -> Optional[Dict]:
    """Check if message requires multi-expert orchestration"""
    orchestration_triggers = [
        "and also", "then", "after that", "schedule and",
        "create and deploy", "build and test", "implement and document"
    ]
    
    if any(trigger in message.lower() for trigger in orchestration_triggers):
        try:
            result = await orchestrator.orchestrate_task(message, user_id)
            return result
        except Exception as e:
            logger.error(f"Orchestration failed: {e}")
            return None
    return None

def format_orchestration_response(result: Dict) -> str:
    """Format orchestration result for display"""
    if not result.get("success"):
        return f"❌ Orchestration failed: {result.get('errors', ['Unknown error'])}"
    
    response = "✅ **Multi-step task completed**\n\n"
    for task in result.get("decomposed_tasks", []):
        status_icon = "✓" if task["status"] == "completed" else "✗"
        response += f"{status_icon} {task['expert_type']}: {task['task_description']}\n"
    
    response += f"\n⏱ Total time: {result.get('total_duration', 0):.2f}s"
    return response

@router.post("/chat")
async def developer_chat(msg: ChatMessage, session: AuthenticatedSession = Depends(validate_session)):
    """
    Zack's intelligent chat with full Zoe capabilities + developer context
    
    Features:
    - Temporal memory for conversation continuity
    - Developer session awareness
    - Cross-agent orchestration for complex tasks
    - Learning from past interactions
    - Predictive suggestions
    - Full codebase context
    """
    start_time = asyncio.get_event_loop().time()
    
    try:
        # Start a developer episode (interface context = "developer")
        episode_id = await temporal_memory.start_conversation_episode(
            msg.user_id, 
            context_type=msg.interface
        )
        
        # Check for developer session queries
        message_lower = msg.message.lower()
        session_query_patterns = [
            "what was i working on", "where was i", "what did i do",
            "resume work", "last session", "previous work",
            "what were you doing", "restore session"
        ]
        is_session_query = any(pattern in message_lower for pattern in session_query_patterns)
        
        if is_session_query:
            # Get developer session info
            try:
                import sqlite3
                conn = sqlite3.connect('/app/data/zoe.db')
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT current_task, last_command, files_changed, next_steps, created_at, updated_at
                    FROM developer_sessions
                    WHERE user_id = ?
                    ORDER BY updated_at DESC
                    LIMIT 1
                """, (msg.user_id,))
                row = cursor.fetchone()
                conn.close()
                
                if row:
                    session_summary = f"""**Last Session Summary**

**Task**: {row[0] or 'None'}
**Last Command**: `{row[1] or 'None'}`
**Files Changed**: {', '.join(json.loads(row[2])) if row[2] else 'None'}
**Next Steps**: {row[3] or 'Continue work'}
**Started**: {row[4]}
**Updated**: {row[5]}

Ready to continue where you left off?"""
                    
                    await temporal_memory.add_message_to_episode(
                        msg.user_id, msg.message, session_summary, msg.interface
                    )
                    
                    return {
                        "response": session_summary,
                        "type": "session_info",
                        "ai_enhanced": True
                    }
                else:
                    no_session_msg = "No previous developer session found. Start working and I'll track your progress!"
                    await temporal_memory.add_message_to_episode(
                        msg.user_id, msg.message, no_session_msg, msg.interface
                    )
                    return {
                        "response": no_session_msg,
                        "type": "session_info",
                        "ai_enhanced": True
                    }
            except Exception as e:
                logger.error(f"Session query failed: {e}")
        
        # Check if this requires orchestration
        orchestration_result = await check_for_orchestration(msg.message, msg.user_id)
        if orchestration_result:
            formatted_response = format_orchestration_response(orchestration_result)
            await temporal_memory.add_message_to_episode(
                msg.user_id, msg.message, formatted_response, msg.interface
            )
            return {
                "response": formatted_response,
                "type": "orchestration",
                "orchestration_result": orchestration_result,
                "ai_enhanced": True
            }
        
        # Get developer context
        dev_context = await get_developer_context(msg.user_id)
        
        # Check for project context needs
        include_project_context = should_include_project_context(msg.message)
        if include_project_context:
            project_ctx = get_fresh_project_context()
            dev_context["full_project_context"] = project_ctx[:5000]  # Limit to 5k chars
        
        # Search temporal memory for relevant context
        temporal_search = await temporal_memory.search_with_temporal_context(
            msg.message, msg.user_id, time_range="week"
        )
        
        # Use enhanced MEM agent for semantic search if query looks like code question
        code_keywords = ["where", "how", "what", "function", "class", "file", "code"]
        if any(keyword in message_lower for keyword in code_keywords):
            try:
                mem_results = await enhanced_mem_agent.search_memories(
                    msg.message, msg.user_id, limit=5
                )
                dev_context["code_search"] = mem_results[:3] if mem_results else []
            except Exception as e:
                logger.warning(f"MEM search failed: {e}")
        
        # Build enhanced prompt with all context
        enhanced_prompt = f"""{ZACK_SYSTEM_PROMPT}

DEVELOPER CONTEXT:
{json.dumps(dev_context, indent=2)}

TEMPORAL CONTEXT:
{json.dumps(temporal_search, indent=2) if temporal_search else 'No recent history'}

USER REQUEST: {msg.message}

Provide a helpful, technical response. Be concise but thorough."""
        
        # Get AI response using RouteLLM for model selection
        from ai_client import get_ai_response
        ai_response = await get_ai_response(
            message=enhanced_prompt,
            context={
                "user_id": msg.user_id,
                "mode": "developer",
                **dev_context
            }
        )
        
        # Extract response text
        if isinstance(ai_response, dict):
            response_text = ai_response.get("response", str(ai_response))
        else:
            response_text = str(ai_response)
        
        # Check for proactive suggestions
        try:
            predictions = await predictive_intelligence.generate_proactive_suggestions(msg.user_id)
            if predictions and len(predictions) > 0:
                response_text += f"\n\n💡 **Proactive Suggestion**: {predictions[0].get('suggestion', '')}"
        except Exception as e:
            logger.warning(f"Predictions failed: {e}")
        
        # Record in temporal memory
        await temporal_memory.add_message_to_episode(
            msg.user_id, msg.message, response_text, msg.interface
        )
        
        # Record for learning
        duration = asyncio.get_event_loop().time() - start_time
        try:
            await learning_system.record_interaction(
                user_id=msg.user_id,
                interface=msg.interface,
                request=msg.message,
                response=response_text,
                duration=duration,
                success=True
            )
        except Exception as e:
            logger.warning(f"Learning system recording failed: {e}")
        
        return {
            "response": response_text,
            "type": "conversation",
            "ai_enhanced": True,
            "context_used": {
                "temporal_memory": bool(temporal_search),
                "developer_context": True,
                "code_search": "code_search" in dev_context,
                "project_context": include_project_context
            },
            "duration": duration
        }
        
    except Exception as e:
        logger.error(f"Developer chat error: {e}", exc_info=True)
        error_response = f"I encountered an error: {str(e)}. Please try again or rephrase your question."
        
        try:
            await temporal_memory.add_message_to_episode(
                msg.user_id, msg.message, error_response, msg.interface
            )
        except:
            pass
        
        return {
            "response": error_response,
            "type": "error",
            "ai_enhanced": False
        }

@router.get("/status")
async def get_developer_chat_status(session: AuthenticatedSession = Depends(validate_session)):
    """Get status of developer chat intelligence systems"""
    return {
        "status": "operational",
        "personality": "Zack - Lead Developer",
        "intelligence_systems": {
            "temporal_memory": True,
            "enhanced_mem_agent": True,
            "orchestration": True,
            "learning_system": True,
            "predictive_intelligence": True,
            "preference_learner": True,
            "unified_learner": True,
            "route_llm": True
        },
        "capabilities": [
            "Developer session tracking",
            "Code search and navigation",
            "Multi-expert orchestration",
            "Conversation continuity",
            "Learning from interactions",
            "Proactive suggestions",
            "Full codebase context"
        ]
    }

@router.post("/close-session")
async def close_developer_session(user_id: str, summary: Optional[str] = None, session: AuthenticatedSession = Depends(validate_session)):
    """Close the current developer episode"""
    try:
        await temporal_memory.close_episode(user_id, "developer", summary)
        return {"success": True, "message": "Developer session closed"}
    except Exception as e:
        logger.error(f"Failed to close session: {e}")
        return {"success": False, "error": str(e)}

@router.get("/history/{user_id}")
async def get_developer_history(user_id: str, limit: int = 10, session: AuthenticatedSession = Depends(validate_session)):
    """Get recent developer chat history"""
    try:
        episodes = await temporal_memory.get_recent_episodes(user_id, "developer", limit)
        return {
            "user_id": user_id,
            "episodes": episodes,
            "count": len(episodes)
        }
    except Exception as e:
        logger.error(f"Failed to get history: {e}")
        return {"error": str(e)}

