"""
Intent Executor
===============

Central execution hub for all intents.

Responsibilities:
1. Validates user permissions
2. Routes intent → handler function
3. Executes handler
4. Formats response
5. Broadcasts updates (WebSocket)
6. Logs metrics
"""

import logging
import time
from typing import Dict, Any, Optional, Callable, Awaitable
from dataclasses import dataclass

from intent_system.classifiers import ZoeIntent, get_context_manager
from intent_system.analytics import get_metrics_collector

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """
    Result of intent execution.
    
    Attributes:
        success: Whether execution succeeded
        message: Natural language response
        data: Additional data (optional)
        latency_ms: Execution latency in milliseconds
    """
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
    latency_ms: float = 0.0


class IntentExecutor:
    """
    Central intent execution engine.
    
    Maps intents to handler functions and executes them.
    """
    
    def __init__(self):
        """Initialize intent executor."""
        self.handlers: Dict[str, Callable] = {}
        self.context_manager = get_context_manager()
        self.metrics_collector = get_metrics_collector()
        
        # Register built-in handlers
        self._register_builtin_handlers()
        
        logger.info("Initialized IntentExecutor")
    
    def _register_builtin_handlers(self):
        """Register built-in intent handlers."""
        # Import handlers lazily to avoid circular imports
        from intent_system.handlers import lists_handlers
        
        # Register list handlers
        self.register_handler("ListAdd", lists_handlers.handle_list_add)
        self.register_handler("ListRemove", lists_handlers.handle_list_remove)
        self.register_handler("ListShow", lists_handlers.handle_list_show)
        self.register_handler("ListClear", lists_handlers.handle_list_clear)
        self.register_handler("ListComplete", lists_handlers.handle_list_complete)
        
        logger.info(f"Registered {len(self.handlers)} intent handlers")
    
    def register_handler(
        self,
        intent_name: str,
        handler: Callable[[ZoeIntent, str, Dict], Awaitable[Dict[str, Any]]]
    ):
        """
        Register an intent handler.
        
        Args:
            intent_name: Name of the intent (e.g., "ListAdd")
            handler: Async function that handles the intent
        """
        self.handlers[intent_name] = handler
        logger.debug(f"Registered handler for intent: {intent_name}")
    
    async def execute(
        self,
        intent: ZoeIntent,
        user_id: str,
        session_id: str = "default"
    ) -> ExecutionResult:
        """
        Execute an intent.
        
        Args:
            intent: The classified intent
            user_id: User identifier
            session_id: Session identifier
            
        Returns:
            ExecutionResult with success status and message
        """
        start_time = time.time()
        
        # Validate intent
        if not intent or not intent.name:
            return ExecutionResult(
                success=False,
                message="Invalid intent",
                latency_ms=0.0
            )
        
        # Check if handler exists
        handler = self.handlers.get(intent.name)
        if not handler:
            logger.warning(f"No handler registered for intent: {intent.name}")
            return ExecutionResult(
                success=False,
                message=f"I don't know how to handle that action yet.",
                latency_ms=(time.time() - start_time) * 1000
            )
        
        # Get conversation context
        context = self.context_manager.get_context(user_id, session_id)
        context_dict = {
            "last_items": context.last_items,
            "last_device": context.last_device,
            "last_list": context.last_list,
            "last_area": context.last_area,
            "last_time": context.last_time,
            "last_intent": context.last_intent,
        }
        
        try:
            # Execute handler
            result = await handler(intent, user_id, context_dict)
            
            # Update context with executed intent
            self.context_manager.update_from_intent(
                user_id=user_id,
                intent_name=intent.name,
                slots=intent.slots,
                session_id=session_id
            )
            
            # Calculate latency
            latency_ms = (time.time() - start_time) * 1000
            
            # Format result
            execution_result = ExecutionResult(
                success=result.get("success", True),
                message=result.get("message", "Done!"),
                data=result.get("data"),
                latency_ms=latency_ms
            )
            
            # Log execution
            logger.info(
                f"Executed intent: {intent.name}, "
                f"user: {user_id}, "
                f"success: {execution_result.success}, "
                f"latency: {latency_ms:.2f}ms"
            )
            
            # Record metrics
            try:
                self.metrics_collector.record_execution(
                    user_id=user_id,
                    intent_name=intent.name,
                    tier=intent.tier,
                    confidence=intent.confidence,
                    latency_ms=latency_ms,
                    success=execution_result.success,
                    input_text=intent.original_text,
                    source="chat"  # TODO: Pass source from caller
                )
            except Exception as e:
                logger.warning(f"Failed to record metrics: {e}")
            
            # TODO: Broadcast WebSocket updates
            
            return execution_result
        
        except Exception as e:
            logger.error(f"Intent execution failed: {intent.name}, error: {e}", exc_info=True)
            
            return ExecutionResult(
                success=False,
                message="Sorry, I encountered an error while processing that.",
                latency_ms=(time.time() - start_time) * 1000
            )
    
    def get_registered_intents(self) -> list[str]:
        """Get list of all registered intent names."""
        return list(self.handlers.keys())

