# Enhanced AI Client with Life Orchestrator Integration
# This file contains the enhanced functions to add to ai_client.py

# Add this import at the top of ai_client.py
from life_orchestrator import life_orchestrator

# Add this function after the existing fetch_user_data_context function
async def fetch_life_orchestrator_insights(message: str, context: Dict):
    """Fetch comprehensive life insights and suggestions"""
    try:
        user_id = context.get("user_id", "default")
        message_lower = message.lower()
        
        # Check if this is a planning or suggestion request
        if any(word in message_lower for word in [
            'plan', 'suggest', 'recommend', 'free time', 'afternoon', 'morning', 
            'evening', 'what should', 'what can', 'help me', 'options'
        ]):
            
            # Get comprehensive life analysis
            life_analysis = await life_orchestrator.analyze_everything(user_id, context)
            
            # Add to context
            context["life_insights"] = life_analysis
            
            # Generate intelligent response suggestions
            if life_analysis.get("urgent_actions"):
                context["urgent_suggestions"] = life_analysis["urgent_actions"]
            
            if life_analysis.get("opportunity_actions"):
                context["opportunity_suggestions"] = life_analysis["opportunity_actions"]
                
            if life_analysis.get("smart_suggestions"):
                context["smart_suggestions"] = life_analysis["smart_suggestions"]
                
            if life_analysis.get("free_time_suggestions"):
                context["free_time_suggestions"] = life_analysis["free_time_suggestions"]
                
            logger.info(f"Generated {len(life_analysis.get('urgent_actions', []))} urgent actions and {len(life_analysis.get('opportunity_actions', []))} opportunities")
            
    except Exception as e:
        logger.warning(f"Failed to fetch life orchestrator insights: {e}")

# Enhanced version of get_ai_response_streaming with life orchestrator
async def get_ai_response_streaming_with_life_orchestrator(message: str, context: Dict = None) -> AsyncGenerator[Dict, None]:
    """Enhanced streaming response with life orchestrator intelligence"""
    context = context or {}
    
    try:
        # Emit thinking event
        yield {
            'type': 'agent_thinking',
            'message': 'Analyzing your request and your entire life context...',
            'timestamp': time.time()
        }
        
        # Check for calendar event creation requests (existing functionality)
        if await handle_calendar_request(message, context):
            yield {
                'type': 'tool_call_start',
                'tool': 'calendar',
                'message': 'Creating calendar event...',
                'timestamp': time.time()
            }
            
            yield {
                'type': 'tool_result',
                'tool': 'calendar',
                'success': True,
                'message': 'Event created successfully!',
                'timestamp': time.time()
            }
            
            yield {
                'type': 'content_delta',
                'content': "Perfect! I've created your birthday event for March 24th. It's now saved in your calendar as an all-day celebration! 🎉📅",
                'timestamp': time.time()
            }
            return
        
        # Fetch life orchestrator insights (NEW)
        yield {
            'type': 'agent_thinking',
            'message': 'Gathering comprehensive life insights...',
            'timestamp': time.time()
        }
        
        await fetch_life_orchestrator_insights(message, context)
        
        # Emit context gathering events (existing functionality)
        yield {
            'type': 'agent_thinking',
            'message': 'Gathering context and user data...',
            'timestamp': time.time()
        }
        
        # Update self-awareness consciousness
        await update_self_awareness_context(message, context)
        
        # Fetch relevant user data with tool indicators (existing functionality)
        async for event in fetch_user_data_context_streaming(message, context):
            yield event
        
        # Emit routing decision
        yield {
            'type': 'agent_thinking',
            'message': 'Determining best response approach...',
            'timestamp': time.time()
        }
        
        # Decide route using RouteLLM-backed router
        routing_decision = route_llm_router.classify_query(message, context)
        use_proxy = routing_decision.get("provider") == "litellm"
        
        # Stream the actual AI response
        if use_proxy:
            async for event in call_litellm_proxy_streaming(message, routing_decision, context):
                yield event
        else:
            async for event in call_ollama_direct_streaming(message, routing_decision.get("model", "llama3.2:3b"), context):
                yield event
        
        # Reflect on the interaction
        await reflect_on_interaction(message, "Response completed", context, routing_decision)
        
    except Exception as e:
        logger.error(f"Streaming AI response failed: {e}")
        yield {
            'type': 'error',
            'message': f"I apologize, but I'm having trouble processing your request right now: {str(e)}",
            'timestamp': time.time()
        }