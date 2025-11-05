"""
Cross-Agent Collaboration & Orchestration System for Zoe
========================================================

Enables coordination of multiple experts (Calendar, List, Planning, Memory) 
for complex multi-step tasks with LLM-based task decomposition.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import httpx
import uuid

# Phase 3: Persistent agent memory integration
from persistent_agent_memory import agent_memory

logger = logging.getLogger(__name__)

class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"

class ExpertType(Enum):
    CALENDAR = "calendar"
    LISTS = "lists"
    MEMORY = "memory"
    PLANNING = "planning"
    DEVELOPMENT = "development"
    WEATHER = "weather"
    HOMEASSISTANT = "homeassistant"
    TTS = "tts"
    PERSON = "person"

@dataclass
class TaskDependency:
    """Represents a dependency between tasks"""
    task_id: str
    depends_on: str
    dependency_type: str  # "sequential", "parallel", "conditional"

@dataclass
class ExpertTask:
    """Represents a task for a specific expert"""
    id: str
    expert_type: ExpertType
    task_description: str
    input_data: Dict[str, Any]
    expected_output: str
    timeout_seconds: int = 30
    retry_count: int = 0
    max_retries: int = 3
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    created_at: str = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

@dataclass
class OrchestrationResult:
    """Result of orchestrated task execution"""
    orchestration_id: str
    user_id: str
    original_request: str
    decomposed_tasks: List[ExpertTask]
    execution_plan: List[TaskDependency]
    final_result: Dict[str, Any]
    success: bool
    total_duration: float
    errors: List[str]
    created_at: str
    completed_at: str

class ExpertOrchestrator:
    """Orchestrates multiple experts for complex tasks"""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.expert_endpoints = {
            ExpertType.CALENDAR: "/api/calendar",
            ExpertType.LISTS: "/api/lists",
            ExpertType.MEMORY: "/api/memories",
            ExpertType.PLANNING: "/api/developer/tasks",
            ExpertType.DEVELOPMENT: "/api/developer",
            ExpertType.WEATHER: "/api/weather",
            ExpertType.HOMEASSISTANT: "/api/homeassistant",
            ExpertType.TTS: "/api/tts",
            ExpertType.PERSON: "/api/people"
        }
        self.active_orchestrations = {}
        self.task_timeout = 30  # Default timeout in seconds
        
        # Expert emoji mapping for display
        self.expert_emojis = {
            "calendar": "🗓️",
            "lists": "📝",
            "memory": "🧠",
            "planning": "📊",
            "reminder": "⏰",
            "weather": "🌤️",
            "homeassistant": "🏠",
            "tts": "🔊",
            "person": "👥"
        }
    
    async def orchestrate_task(self, user_id: str, request: str, 
                             context: Dict[str, Any] = None) -> OrchestrationResult:
        """Orchestrate a complex task across multiple experts"""
        orchestration_id = str(uuid.uuid4())
        start_time = datetime.now()
        
        try:
            # Step 1: Decompose task using LLM
            decomposed_tasks = await self._decompose_task_with_llm(
                request, context or {}, user_id
            )
            
            # Step 2: Create execution plan with dependencies
            execution_plan = self._create_execution_plan(decomposed_tasks)
            
            # Step 3: Execute tasks with coordination
            results = await self._execute_tasks_with_coordination(
                orchestration_id, user_id, decomposed_tasks, execution_plan
            )
            
            # Step 4: Synthesize final result
            final_result = await self._synthesize_results(results, request)
            
            # Create orchestration result
            orchestration_result = OrchestrationResult(
                orchestration_id=orchestration_id,
                user_id=user_id,
                original_request=request,
                decomposed_tasks=decomposed_tasks,
                execution_plan=execution_plan,
                final_result=final_result,
                success=all(task.status == TaskStatus.COMPLETED for task in decomposed_tasks),
                total_duration=(datetime.now() - start_time).total_seconds(),
                errors=[task.error_message for task in decomposed_tasks if task.error_message],
                created_at=start_time.isoformat(),
                completed_at=datetime.now().isoformat()
            )
            
            # Store result
            self.active_orchestrations[orchestration_id] = orchestration_result
            
            return orchestration_result
            
        except Exception as e:
            logger.error(f"Orchestration failed: {e}")
            return OrchestrationResult(
                orchestration_id=orchestration_id,
                user_id=user_id,
                original_request=request,
                decomposed_tasks=[],
                execution_plan=[],
                final_result={"error": str(e)},
                success=False,
                total_duration=(datetime.now() - start_time).total_seconds(),
                errors=[str(e)],
                created_at=start_time.isoformat(),
                completed_at=datetime.now().isoformat()
            )
    
    async def _decompose_task_with_llm(self, request: str, context: Dict[str, Any], 
                                     user_id: str) -> List[ExpertTask]:
        """Decompose task using LLM-based analysis"""
        try:
            # Phase 3 Enhancement: Recall learned patterns for better decomposition
            learned_context = ""
            for expert_type in ExpertType:
                patterns = await agent_memory.recall(
                    agent_type=expert_type.value,
                    user_id=user_id,
                    task_description=request,
                    limit=3
                )
                if patterns:
                    learned_context += f"\n{expert_type.value} expert learned patterns:\n"
                    learned_context += "\n".join(f"  - {p}" for p in patterns[:3])
            
            # Create LLM prompt for task decomposition
            prompt = f"""
            Analyze this user request and decompose it into specific tasks for different experts.
            
            Available experts:
            - calendar: Schedule events, manage calendar
            - lists: Manage to-do lists, shopping lists, reminders
            - memory: Store and retrieve information
            - planning: Create plans, roadmaps, project management
            - development: Code generation, debugging, technical tasks
            - weather: Weather information and forecasts
            - homeassistant: Smart home control and automation
            - person: Manage people, contacts, relationships, birthdays, notes, gift ideas
            
            {learned_context if learned_context else ""}
            
            User Request: {request}
            Context: {json.dumps(context, indent=2)}
            
            Return a JSON array of tasks, each with:
            - expert_type: which expert should handle this
            - task_description: clear description of what to do
            - input_data: any specific data needed
            - expected_output: what the expert should return
            - timeout_seconds: how long to wait (default 30)
            
            Example:
            [
                {{
                    "expert_type": "calendar",
                    "task_description": "Create a meeting for tomorrow at 2pm",
                    "input_data": {{"title": "Team Meeting", "start_time": "2024-01-15T14:00:00"}},
                    "expected_output": "Meeting created with ID and confirmation",
                    "timeout_seconds": 30
                }}
            ]
            """
            
            # Call LLM for decomposition (simplified - in real implementation would use actual LLM)
            decomposed_data = await self._call_llm_for_decomposition(prompt)
            
            # Convert to ExpertTask objects
            tasks = []
            for i, task_data in enumerate(decomposed_data):
                task = ExpertTask(
                    id=f"task_{orchestration_id}_{i}",
                    expert_type=ExpertType(task_data["expert_type"]),
                    task_description=task_data["task_description"],
                    input_data=task_data["input_data"],
                    expected_output=task_data["expected_output"],
                    timeout_seconds=task_data.get("timeout_seconds", 30),
                    created_at=datetime.now().isoformat()
                )
                tasks.append(task)
            
            return tasks
            
        except Exception as e:
            logger.error(f"Task decomposition failed: {e}")
            # Fallback to simple keyword-based decomposition
            return self._fallback_decomposition(request, context)
    
    async def _call_llm_for_decomposition(self, prompt: str) -> List[Dict[str, Any]]:
        """Call LLM for task decomposition"""
        try:
            # In a real implementation, this would call the actual LLM service
            # For now, return a simple decomposition based on keywords
            return self._simple_keyword_decomposition(prompt)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return []
    
    def _simple_keyword_decomposition(self, prompt: str) -> List[Dict[str, Any]]:
        """Simple keyword-based task decomposition as fallback"""
        request_lower = prompt.lower()
        tasks = []
        
        # Calendar tasks
        if any(keyword in request_lower for keyword in ["meeting", "event", "schedule", "appointment"]):
            tasks.append({
                "expert_type": "calendar",
                "task_description": "Handle calendar-related request",
                "input_data": {"request": prompt},
                "expected_output": "Calendar operation completed",
                "timeout_seconds": 30
            })
        
        # List tasks
        if any(keyword in request_lower for keyword in ["list", "todo", "reminder", "shopping"]):
            tasks.append({
                "expert_type": "lists",
                "task_description": "Handle list-related request",
                "input_data": {"request": prompt},
                "expected_output": "List operation completed",
                "timeout_seconds": 30
            })
        
        # Memory tasks
        if any(keyword in request_lower for keyword in ["remember", "recall", "memory", "note"]):
            tasks.append({
                "expert_type": "memory",
                "task_description": "Handle memory-related request",
                "input_data": {"request": prompt},
                "expected_output": "Memory operation completed",
                "timeout_seconds": 30
            })
        
        # Development tasks
        if any(keyword in request_lower for keyword in ["code", "programming", "debug", "function"]):
            tasks.append({
                "expert_type": "development",
                "task_description": "Handle development-related request",
                "input_data": {"request": prompt},
                "expected_output": "Development task completed",
                "timeout_seconds": 60
            })
        
        # Default task if no specific keywords found
        if not tasks:
            tasks.append({
                "expert_type": "memory",
                "task_description": "Handle general request",
                "input_data": {"request": prompt},
                "expected_output": "Request processed",
                "timeout_seconds": 30
            })
        
        return tasks
    
    def _fallback_decomposition(self, request: str, context: Dict[str, Any]) -> List[ExpertTask]:
        """Fallback decomposition when LLM fails"""
        return [
            ExpertTask(
                id=f"fallback_task_{uuid.uuid4()}",
                expert_type=ExpertType.MEMORY,
                task_description=f"Process request: {request}",
                input_data={"request": request, "context": context},
                expected_output="Request processed",
                timeout_seconds=30,
                created_at=datetime.now().isoformat()
            )
        ]
    
    def _create_execution_plan(self, tasks: List[ExpertTask]) -> List[TaskDependency]:
        """Create execution plan with dependencies"""
        dependencies = []
        
        # Simple sequential execution for now
        # In a more sophisticated system, this would analyze task dependencies
        for i in range(1, len(tasks)):
            dependencies.append(TaskDependency(
                task_id=tasks[i].id,
                depends_on=tasks[i-1].id,
                dependency_type="sequential"
            ))
        
        return dependencies
    
    async def _execute_tasks_with_coordination(self, orchestration_id: str, user_id: str,
                                             tasks: List[ExpertTask], 
                                             execution_plan: List[TaskDependency]) -> List[ExpertTask]:
        """Execute tasks with proper coordination and timeout handling"""
        completed_tasks = []
        
        for task in tasks:
            try:
                # Check dependencies
                if not self._check_dependencies(task, completed_tasks, execution_plan):
                    continue
                
                # Execute task with timeout
                result = await self._execute_single_task(task, user_id)
                
                if result:
                    completed_tasks.append(task)
                else:
                    # Handle task failure
                    task.status = TaskStatus.FAILED
                    task.error_message = "Task execution failed"
                
            except asyncio.TimeoutError:
                task.status = TaskStatus.TIMEOUT
                task.error_message = f"Task timed out after {task.timeout_seconds} seconds"
                logger.warning(f"Task {task.id} timed out")
            
            except Exception as e:
                task.status = TaskStatus.FAILED
                task.error_message = str(e)
                logger.error(f"Task {task.id} failed: {e}")
        
        return tasks
    
    def _check_dependencies(self, task: ExpertTask, completed_tasks: List[ExpertTask],
                          execution_plan: List[TaskDependency]) -> bool:
        """Check if task dependencies are satisfied"""
        task_dependencies = [dep for dep in execution_plan if dep.task_id == task.id]
        
        for dep in task_dependencies:
            if not any(completed.id == dep.depends_on for completed in completed_tasks):
                return False
        
        return True
    
    async def _execute_single_task(self, task: ExpertTask, user_id: str) -> bool:
        """Execute a single task with timeout handling"""
        try:
            task.status = TaskStatus.IN_PROGRESS
            task.started_at = datetime.now().isoformat()
            
            # Get expert endpoint
            endpoint = self.expert_endpoints.get(task.expert_type)
            if not endpoint:
                raise ValueError(f"No endpoint for expert type: {task.expert_type}")
            
            # Prepare request data
            request_data = {
                **task.input_data,
                "user_id": user_id
            }
            
            # Execute with timeout
            async with httpx.AsyncClient(timeout=task.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}{endpoint}",
                    json=request_data
                )
                
                if response.status_code == 200:
                    task.result = response.json()
                    task.status = TaskStatus.COMPLETED
                    task.completed_at = datetime.now().isoformat()
                    
                    # Phase 3: Remember successful pattern
                    await agent_memory.remember(
                        agent_type=task.expert_type.value,
                        user_id=user_id,
                        orchestration_id=task.id,
                        task_description=task.task_description,
                        success=True,
                        result=task.result
                    )
                    
                    return True
                else:
                    task.error_message = f"HTTP {response.status_code}: {response.text}"
                    
                    # Phase 3: Remember failure
                    await agent_memory.remember(
                        agent_type=task.expert_type.value,
                        user_id=user_id,
                        orchestration_id=task.id,
                        task_description=task.task_description,
                        success=False,
                        result={"error": task.error_message}
                    )
                    
                    return False
                    
        except asyncio.TimeoutError:
            task.status = TaskStatus.TIMEOUT
            task.error_message = f"Task timed out after {task.timeout_seconds} seconds"
            
            # Phase 3: Remember timeout
            await agent_memory.remember(
                agent_type=task.expert_type.value,
                user_id=user_id,
                orchestration_id=task.id,
                task_description=task.task_description,
                success=False,
                result={"error": task.error_message}
            )
            
            return False
        
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error_message = str(e)
            
            # Phase 3: Remember failure
            await agent_memory.remember(
                agent_type=task.expert_type.value,
                user_id=user_id,
                orchestration_id=task.id,
                task_description=task.task_description,
                success=False,
                result={"error": task.error_message}
            )
            
            return False
    
    async def _synthesize_results(self, tasks: List[ExpertTask], 
                                original_request: str) -> Dict[str, Any]:
        """Synthesize results from all tasks into coherent response"""
        successful_tasks = [task for task in tasks if task.status == TaskStatus.COMPLETED]
        failed_tasks = [task for task in tasks if task.status in [TaskStatus.FAILED, TaskStatus.TIMEOUT]]
        
        synthesis = {
            "original_request": original_request,
            "successful_tasks": len(successful_tasks),
            "failed_tasks": len(failed_tasks),
            "total_tasks": len(tasks),
            "results": {},
            "errors": []
        }
        
        # Collect results from successful tasks
        for task in successful_tasks:
            synthesis["results"][task.expert_type.value] = {
                "description": task.task_description,
                "result": task.result,
                "duration": self._calculate_task_duration(task)
            }
        
        # Collect errors from failed tasks
        for task in failed_tasks:
            synthesis["errors"].append({
                "expert_type": task.expert_type.value,
                "description": task.task_description,
                "error": task.error_message,
                "status": task.status.value
            })
        
        # Generate summary
        synthesis["summary"] = self._generate_summary(synthesis)
        
        return synthesis
    
    def _calculate_task_duration(self, task: ExpertTask) -> float:
        """Calculate task execution duration"""
        if task.started_at and task.completed_at:
            start = datetime.fromisoformat(task.started_at)
            end = datetime.fromisoformat(task.completed_at)
            return (end - start).total_seconds()
        return 0.0
    
    def _generate_summary(self, synthesis: Dict[str, Any]) -> str:
        """Generate human-readable summary of orchestration results"""
        successful = synthesis["successful_tasks"]
        failed = synthesis["failed_tasks"]
        total = synthesis["total_tasks"]
        
        summary = f"Orchestration completed: {successful}/{total} tasks successful"
        
        if failed > 0:
            summary += f", {failed} failed"
        
        if synthesis["results"]:
            summary += f". Experts involved: {', '.join(synthesis['results'].keys())}"
        
        return summary
    
    async def stream_orchestration(self, user_id: str, request: str, context: Dict[str, Any] = None):
        """Stream orchestration progress with AG-UI protocol for real-time updates"""
        orchestration_id = str(uuid.uuid4())
        start_time = datetime.now()
        
        try:
            # Session start
            yield {
                "type": "session_start",
                "session_id": orchestration_id,
                "timestamp": start_time.isoformat()
            }
            
            # Step 1: Decompose task
            yield {
                "type": "agent_state_delta",
                "state": {"status": "decomposing", "message": "🔄 Breaking down your request..."}
            }
            
            # Use Enhanced MEM Agent for intelligent task decomposition
            decomposed_tasks = await self._decompose_with_enhanced_mem_agent(request, user_id)
            
            # Show planned experts
            expert_list = "\n".join([
                f"   {i+1}. {self.expert_emojis.get(task['expert'], '🤖')} {task['expert'].title()} expert → {task['description']}"
                for i, task in enumerate(decomposed_tasks)
            ])
            
            yield {
                "type": "message_delta",
                "delta": f"\n📋 I'll coordinate {len(decomposed_tasks)} experts:\n{expert_list}\n\n"
            }
            
            # Step 2: Execute each expert task
            results = []
            for i, task in enumerate(decomposed_tasks):
                expert_name = task['expert']
                expert_emoji = self.expert_emojis.get(expert_name, '🤖')
                
                # Show expert starting
                yield {
                    "type": "action",
                    "action": {
                        "type": "expert_call",
                        "expert": expert_name,
                        "description": task['description']
                    }
                }
                
                yield {
                    "type": "message_delta",
                    "delta": f"\n{expert_emoji} {expert_name.title()} expert working...\n"
                }
                
                # Execute expert
                result = await self._execute_expert_for_orchestration(expert_name, task, user_id)
                results.append(result)
                
                # Show result
                if result.get('success'):
                    summary = self._format_expert_result(expert_name, result.get('data', {}))
                    yield {
                        "type": "message_delta",
                        "delta": f"   ✅ {summary}\n"
                    }
                else:
                    yield {
                        "type": "message_delta",
                        "delta": f"   ⚠️ Could not complete this step\n"
                    }
            
            # Step 3: Create actionable cards (if applicable)
            action_cards = await self._create_actionable_cards(results, user_id)
            
            if action_cards:
                yield {
                    "type": "message_delta",
                    "delta": "\n\n💡 **Suggested Actions:**\n"
                }
                
                yield {
                    "type": "action_cards",
                    "cards": action_cards
                }
            
            # Step 4: Synthesize final result
            final_plan = await self._synthesize_daily_plan(results, request)
            
            yield {
                "type": "message_delta",
                "delta": f"\n\n🎉 All done! Here's your plan:\n\n{final_plan}\n"
            }
            
            # Session end
            yield {
                "type": "session_end",
                "final_result": final_plan,
                "timestamp": datetime.now().isoformat(),
                "session_id": orchestration_id
            }
            
        except Exception as e:
            logger.error(f"Streaming orchestration error: {e}", exc_info=True)
            yield {
                "type": "error",
                "error": str(e),
                "message": f"❌ Orchestration failed: {e}"
            }
            yield {
                "type": "session_end",
                "timestamp": datetime.now().isoformat(),
                "session_id": orchestration_id
            }
    
    async def _decompose_with_enhanced_mem_agent(self, request: str, user_id: str) -> List[Dict]:
        """Use Enhanced MEM Agent to intelligently decompose the request"""
        # For "plan my day" type requests, always query all relevant experts
        request_lower = request.lower()
        
        if any(phrase in request_lower for phrase in ["plan my day", "plan day", "organize my day", "help me plan"]):
            # Comprehensive daily planning = query ALL experts
            return [
                {"expert": "calendar", "description": "Get today's events and identify free time slots", "action": "read"},
                {"expert": "lists", "description": "Get pending tasks from all lists", "action": "read"},
                {"expert": "reminder", "description": "Get reminders due today", "action": "read"},
                {"expert": "memory", "description": "Search for important upcoming events (birthdays, calls to make)", "action": "read"},
                {"expert": "planning", "description": "Synthesize all info into a comprehensive plan", "action": "write"}
            ]
        else:
            # For other requests, use keyword-based decomposition
            tasks = []
            
            if any(word in request_lower for word in ["calendar", "event", "schedule", "meeting"]):
                tasks.append({"expert": "calendar", "description": "Handle calendar request", "action": "write"})
            
            if any(word in request_lower for word in ["list", "task", "todo", "shopping"]):
                tasks.append({"expert": "lists", "description": "Handle list request", "action": "write"})
            
            if any(word in request_lower for word in ["remind", "reminder"]):
                tasks.append({"expert": "reminder", "description": "Handle reminder request", "action": "write"})
            
            if any(word in request_lower for word in ["remember", "memory", "who", "what"]):
                tasks.append({"expert": "memory", "description": "Search memories", "action": "read"})
            
            return tasks if tasks else [{"expert": "planning", "description": "Process general request", "action": "write"}]
    
    async def _execute_expert_for_orchestration(self, expert_name: str, task: Dict, user_id: str) -> Dict:
        """Execute a specific expert for orchestration"""
        try:
            # Call Enhanced MEM Agent with the expert query
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Map to the appropriate action based on expert
                if expert_name == "calendar" and task.get("action") == "read":
                    query = "show me my calendar and free time today"
                elif expert_name == "lists" and task.get("action") == "read":
                    query = "show me my pending tasks"
                elif expert_name == "reminder" and task.get("action") == "read":
                    query = "show me my reminders for today"
                elif expert_name == "memory" and task.get("action") == "read":
                    query = "show me upcoming important events and birthdays"
                elif expert_name == "planning":
                    query = "help me plan my day"
                else:
                    query = task.get("description", "")
                
                # Call Enhanced MEM Agent
                response = await client.post(
                    "http://mem-agent:11435/search",
                    json={
                        "query": query,
                        "user_id": user_id,
                        "execute_actions": True
                    },
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    result = response.json()
                    # Extract data from first expert result
                    experts = result.get("experts", [])
                    if experts:
                        first_expert = experts[0]
                        expert_result = first_expert.get("result", {})
                        return {
                            "success": expert_result.get("success", True),
                            "expert": expert_name,
                            "data": expert_result.get("data", {}),
                            "message": expert_result.get("message", "")
                        }
                
                return {"success": False, "expert": expert_name, "error": f"API error: {response.status_code}"}
                
        except Exception as e:
            logger.error(f"Expert {expert_name} execution error: {e}", exc_info=True)
            return {"success": False, "expert": expert_name, "error": str(e)}
    
    def _format_expert_result(self, expert_name: str, data: Dict) -> str:
        """Format expert result for display"""
        if expert_name == "calendar":
            events = len(data.get("today_events", []))
            free_hours = data.get("total_free_hours", 0)
            return f"Found {events} events, {free_hours:.1f} hours free today"
        elif expert_name == "lists":
            total = data.get("total_pending", 0)
            high = len(data.get("high_priority", []))
            return f"Found {total} pending tasks ({high} high priority)"
        elif expert_name == "reminder":
            count = len(data.get("reminders", []))
            return f"Found {count} reminders for today"
        elif expert_name == "memory":
            birthdays = len(data.get("upcoming_birthdays", []))
            calls = len(data.get("people_to_call", []))
            return f"Found {birthdays} upcoming birthdays, {calls} people to call"
        elif expert_name == "planning":
            steps = len(data.get("steps", []))
            return f"Created plan with {steps} steps"
        return "Completed"
    
    async def _create_actionable_cards(self, results: List[Dict], user_id: str) -> List[Dict]:
        """Create interactive action cards from expert results"""
        cards = []
        
        # Extract data from each expert (safely handle failed results)
        calendar_data = next((r.get('data', {}) for r in results if r.get('expert') == 'calendar' and r.get('success')), {})
        lists_data = next((r.get('data', {}) for r in results if r.get('expert') == 'lists' and r.get('success')), {})
        memory_data = next((r.get('data', {}) for r in results if r.get('expert') == 'memory' and r.get('success')), {})
        
        # Get free time slots from calendar
        free_slots = calendar_data.get('free_slots', [])
        
        # Create cards for high-priority tasks with time slot suggestions
        high_priority_tasks = lists_data.get('high_priority', [])
        for i, task in enumerate(high_priority_tasks[:3]):  # Top 3 tasks
            task_duration = task.get('estimated_duration', 60)
            
            # Find matching free slots
            matching_slots = [
                slot for slot in free_slots 
                if slot.get('duration_minutes', 0) >= task_duration
            ]
            
            actions = [{
                "type": "add_to_calendar_with_slots",
                "label": "📅 Add to Calendar",
                "data": {
                    "title": task.get('text', 'Task'),
                    "task_id": task.get('id'),
                    "duration_minutes": task_duration,
                    "available_slots": matching_slots,
                    "all_slots": free_slots
                }
            }, {
                "type": "set_reminder",
                "label": "⏰ Remind Me",
                "data": {
                    "title": task.get('text', 'Task'),
                    "task_id": task.get('id')
                }
            }]
            
            cards.append({
                "id": f"task_{task.get('id', i)}",
                "type": "task",
                "icon": "🎯",
                "priority": task.get('priority', 'medium'),
                "title": task.get('text', 'Task'),
                "description": f"{task.get('priority', 'Medium')} priority - Est. {task_duration} min",
                "actions": actions
            })
        
        # Create cards for upcoming birthdays
        upcoming_birthdays = memory_data.get('upcoming_birthdays', [])
        for birthday in upcoming_birthdays[:3]:  # Top 3
            cards.append({
                "id": f"birthday_{birthday.get('person_id')}",
                "type": "reminder",
                "icon": "🎂",
                "title": f"{birthday.get('name')}'s birthday - {birthday.get('birthday')}",
                "description": "Don't forget!",
                "actions": [{
                    "type": "add_to_calendar",
                    "label": "📅 Add to Calendar",
                    "data": {
                        "title": f"{birthday.get('name')}'s Birthday",
                        "start_date": birthday.get('birthday'),
                        "all_day": True
                    }
                }, {
                    "type": "add_to_list",
                    "label": "🎁 Add Gift to Shopping",
                    "data": {
                        "text": f"Buy birthday gift for {birthday.get('name')}",
                        "list_name": "Shopping",
                        "priority": "high"
                    }
                }]
            })
        
        # Create cards for people to call
        people_to_call = memory_data.get('people_to_call', [])
        for person in people_to_call[:2]:  # Top 2
            cards.append({
                "id": f"call_{person.get('person_id')}",
                "type": "reminder",
                "icon": "📞",
                "title": f"Call {person.get('name')}",
                "description": person.get('reason', 'Important'),
                "actions": [{
                    "type": "add_to_calendar",
                    "label": "📅 Schedule Call",
                    "data": {
                        "title": f"Call {person.get('name')}",
                        "duration": 30
                    }
                }, {
                    "type": "set_reminder",
                    "label": "⏰ Remind Me Today",
                    "data": {
                        "title": f"Call {person.get('name')}",
                        "remind_at": "today_evening"
                    }
                }]
            })
        
        return cards
    
    async def _synthesize_daily_plan(self, results: List[Dict], request: str) -> str:
        """Synthesize all expert results into a comprehensive daily plan"""
        # Extract data from experts (safely handle failed results)
        calendar_data = next((r.get('data', {}) for r in results if r.get('expert') == 'calendar' and r.get('success')), {})
        lists_data = next((r.get('data', {}) for r in results if r.get('expert') == 'lists' and r.get('success')), {})
        reminders_data = next((r.get('data', {}) for r in results if r.get('expert') == 'reminder' and r.get('success')), {})
        memory_data = next((r.get('data', {}) for r in results if r.get('expert') == 'memory' and r.get('success')), {})
        
        plan = "**Your Daily Plan**\n\n"
        
        # Today's schedule
        today_events = calendar_data.get('today_events', [])
        if today_events:
            plan += "📅 **Today's Schedule:**\n"
            for event in today_events[:10]:  # Max 10 events
                time_str = event.get('start_time', 'All day')
                if time_str and time_str != "All day":
                    # Format as 12-hour time
                    try:
                        hour = int(time_str.split(":")[0])
                        minute = time_str.split(":")[1] if ":" in time_str else "00"
                        ampm = "AM" if hour < 12 else "PM"
                        hour_12 = hour % 12 or 12
                        time_str = f"{hour_12}:{minute} {ampm}"
                    except:
                        pass
                title = event.get('title', 'Untitled')
                plan += f"• {time_str} - {title}\n"
            plan += "\n"
        
        # Free time suggestions
        free_slots = calendar_data.get('free_slots', [])
        if free_slots:
            plan += "⏰ **Available Time:**\n"
            for slot in free_slots[:5]:  # Max 5 slots
                start = slot.get('start_time', '')
                end = slot.get('end_time', '')
                duration = slot.get('duration_minutes', 0) / 60
                plan += f"• {start} - {end} ({duration:.1f}h free)\n"
            plan += "\n"
        
        # Priority tasks
        high_priority = lists_data.get('high_priority', [])
        if high_priority:
            plan += "🎯 **High Priority Tasks:**\n"
            for task in high_priority[:5]:
                plan += f"• {task.get('text', 'Task')}\n"
            plan += "\n"
        
        # Reminders
        if reminders_data and isinstance(reminders_data, dict):
            reminders = reminders_data.get('reminders', [])
            if reminders:
                plan += "⏰ **Reminders:**\n"
                for reminder in reminders[:5]:
                    plan += f"• {reminder.get('title', 'Reminder')}\n"
                plan += "\n"
        
        # Important notes from memory
        upcoming_birthdays = memory_data.get('upcoming_birthdays', [])
        if upcoming_birthdays:
            plan += "🎂 **Upcoming Events:**\n"
            for birthday in upcoming_birthdays[:3]:
                plan += f"• {birthday.get('name')}'s birthday - {birthday.get('birthday')}\n"
        
        people_to_call = memory_data.get('people_to_call', [])
        if people_to_call:
            plan += "\n📞 **Don't Forget:**\n"
            for person in people_to_call[:3]:
                plan += f"• Call {person.get('name')} - {person.get('reason', '')}\n"
        
        return plan if len(plan) > 30 else "Your schedule is clear! Great time to tackle those pending tasks or enjoy some free time. 😊"
    
    async def get_orchestration_status(self, orchestration_id: str) -> Optional[OrchestrationResult]:
        """Get status of a specific orchestration"""
        return self.active_orchestrations.get(orchestration_id)
    
    async def get_user_orchestrations(self, user_id: str, limit: int = 10) -> List[OrchestrationResult]:
        """Get orchestration history for a user"""
        user_orchestrations = [
            result for result in self.active_orchestrations.values()
            if result.user_id == user_id
        ]
        
        # Sort by creation time, most recent first
        user_orchestrations.sort(key=lambda x: x.created_at, reverse=True)
        
        return user_orchestrations[:limit]
    
    async def cancel_orchestration(self, orchestration_id: str) -> bool:
        """Cancel a running orchestration"""
        if orchestration_id in self.active_orchestrations:
            result = self.active_orchestrations[orchestration_id]
            # Mark as cancelled (simplified - in real implementation would stop running tasks)
            result.success = False
            result.errors.append("Orchestration cancelled by user")
            return True
        return False

# Global instance
orchestrator = ExpertOrchestrator()

