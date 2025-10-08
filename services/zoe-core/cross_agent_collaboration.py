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
            ExpertType.HOMEASSISTANT: "/api/homeassistant"
        }
        self.active_orchestrations = {}
        self.task_timeout = 30  # Default timeout in seconds
    
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
                    return True
                else:
                    task.error_message = f"HTTP {response.status_code}: {response.text}"
                    return False
                    
        except asyncio.TimeoutError:
            task.status = TaskStatus.TIMEOUT
            task.error_message = f"Task timed out after {task.timeout_seconds} seconds"
            return False
        
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error_message = str(e)
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

