"""
Intelligent Task Scheduling System for Zoe AI Assistant
Optimizes task ordering with dependency analysis, parallelization, and resource awareness
"""
import sqlite3
import json
import time
import asyncio
from typing import Dict, List, Any, Optional, Tuple, Set
from datetime import datetime, timedelta
import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"

class TaskPriority(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

@dataclass
class TaskDependency:
    """Represents a task dependency"""
    task_id: str
    depends_on: str
    dependency_type: str  # "blocks", "requires", "suggests"
    weight: float = 1.0

@dataclass
class TaskResource:
    """Represents resource requirements for a task"""
    cpu_intensive: bool = False
    memory_intensive: bool = False
    io_intensive: bool = False
    network_intensive: bool = False
    estimated_duration: int = 0  # minutes
    max_parallel: int = 1

@dataclass
class ScheduledTask:
    """Represents a task with scheduling information"""
    task_id: str
    title: str
    priority: TaskPriority
    status: TaskStatus
    dependencies: List[TaskDependency]
    resources: TaskResource
    estimated_start: Optional[datetime] = None
    estimated_end: Optional[datetime] = None
    execution_order: int = 0
    can_parallelize: bool = True

class TaskScheduler:
    """Intelligent task scheduling system"""
    
    def __init__(self, db_path: str = "/app/data/developer_tasks.db"):
        self.db_path = db_path
        self.scheduled_tasks: List[ScheduledTask] = []
        self.execution_queue: List[ScheduledTask] = []
        self.running_tasks: Set[str] = set()
        self.completed_tasks: Set[str] = set()
        self.max_parallel_tasks = 3
        self.resource_limits = {
            "cpu_intensive": 2,
            "memory_intensive": 2,
            "io_intensive": 4,
            "network_intensive": 3
        }
        
    def analyze_dependencies(self, tasks: List[Dict[str, Any]]) -> List[TaskDependency]:
        """Analyze task dependencies based on content and keywords"""
        dependencies = []
        
        for task in tasks:
            task_id = task["id"]
            title = task["title"].lower()
            objective = task["objective"].lower()
            requirements = json.loads(task.get("requirements", "[]"))
            
            # Look for dependency keywords
            dependency_keywords = {
                "blocks": ["blocked by", "depends on", "requires", "after", "following"],
                "requires": ["needs", "requires", "depends on", "prerequisite"],
                "suggests": ["should", "recommended", "better if", "prefer"]
            }
            
            for dep_type, keywords in dependency_keywords.items():
                for keyword in keywords:
                    if keyword in title or keyword in objective:
                        # Try to find referenced task
                        referenced_task = self._find_referenced_task(tasks, title, objective, keyword)
                        if referenced_task and referenced_task != task_id:
                            weight = 1.0 if dep_type == "blocks" else 0.7 if dep_type == "requires" else 0.3
                            dependencies.append(TaskDependency(
                                task_id=task_id,
                                depends_on=referenced_task,
                                dependency_type=dep_type,
                                weight=weight
                            ))
            
            # Check requirements for explicit dependencies
            for req in requirements:
                if "depends on" in req.lower() or "requires" in req.lower():
                    referenced_task = self._find_referenced_task(tasks, req, "", "depends on")
                    if referenced_task and referenced_task != task_id:
                        dependencies.append(TaskDependency(
                            task_id=task_id,
                            depends_on=referenced_task,
                            dependency_type="requires",
                            weight=0.8
                        ))
        
        return dependencies
    
    def _find_referenced_task(self, tasks: List[Dict[str, Any]], text: str, context: str, keyword: str) -> Optional[str]:
        """Find a task referenced in text"""
        # Simple keyword matching - could be enhanced with NLP
        for task in tasks:
            task_title = task["title"].lower()
            if task_title in text or task_title in context:
                return task["id"]
        return None
    
    def analyze_resource_requirements(self, task: Dict[str, Any]) -> TaskResource:
        """Analyze resource requirements for a task"""
        title = task["title"].lower()
        objective = task["objective"].lower()
        requirements = json.loads(task.get("requirements", "[]"))
        
        # Determine resource intensity
        cpu_keywords = ["compute", "calculation", "processing", "algorithm", "optimize"]
        memory_keywords = ["large", "big", "memory", "cache", "buffer", "data"]
        io_keywords = ["file", "database", "disk", "storage", "backup", "export"]
        network_keywords = ["api", "network", "http", "request", "download", "upload"]
        
        cpu_intensive = any(keyword in title or keyword in objective for keyword in cpu_keywords)
        memory_intensive = any(keyword in title or keyword in objective for keyword in memory_keywords)
        io_intensive = any(keyword in title or keyword in objective for keyword in io_keywords)
        network_intensive = any(keyword in title or keyword in objective for keyword in network_keywords)
        
        # Estimate duration based on complexity
        estimated_duration = 30  # default 30 minutes
        if "simple" in title or "quick" in title:
            estimated_duration = 15
        elif "complex" in title or "major" in title:
            estimated_duration = 120
        elif "refactor" in title or "optimize" in title:
            estimated_duration = 90
        
        # Determine max parallel execution
        max_parallel = 1
        if not cpu_intensive and not memory_intensive:
            max_parallel = 3
        elif not cpu_intensive:
            max_parallel = 2
        
        return TaskResource(
            cpu_intensive=cpu_intensive,
            memory_intensive=memory_intensive,
            io_intensive=io_intensive,
            network_intensive=network_intensive,
            estimated_duration=estimated_duration,
            max_parallel=max_parallel
        )
    
    def create_scheduled_tasks(self, tasks: List[Dict[str, Any]]) -> List[ScheduledTask]:
        """Create scheduled tasks with dependencies and resource analysis"""
        scheduled_tasks = []
        dependencies = self.analyze_dependencies(tasks)
        
        for task in tasks:
            # Create dependency list for this task
            task_dependencies = [dep for dep in dependencies if dep.task_id == task["id"]]
            
            # Analyze resource requirements
            resources = self.analyze_resource_requirements(task)
            
            # Create scheduled task
            scheduled_task = ScheduledTask(
                task_id=task["id"],
                title=task["title"],
                priority=TaskPriority(task["priority"]),
                status=TaskStatus(task["status"]),
                dependencies=task_dependencies,
                resources=resources,
                can_parallelize=not resources.cpu_intensive and not resources.memory_intensive
            )
            
            scheduled_tasks.append(scheduled_task)
        
        return scheduled_tasks
    
    def build_execution_graph(self, scheduled_tasks: List[ScheduledTask]) -> Dict[str, List[str]]:
        """Build execution graph showing task dependencies"""
        graph = {}
        
        for task in scheduled_tasks:
            graph[task.task_id] = []
            for dep in task.dependencies:
                if dep.dependency_type in ["blocks", "requires"]:
                    graph[task.task_id].append(dep.depends_on)
        
        return graph
    
    def topological_sort(self, graph: Dict[str, List[str]]) -> List[str]:
        """Perform topological sort to determine execution order"""
        in_degree = {node: 0 for node in graph}
        
        # Calculate in-degrees
        for node in graph:
            for neighbor in graph[node]:
                in_degree[neighbor] = in_degree.get(neighbor, 0) + 1
        
        # Find nodes with no incoming edges
        queue = [node for node in in_degree if in_degree[node] == 0]
        result = []
        
        while queue:
            node = queue.pop(0)
            result.append(node)
            
            # Reduce in-degree for neighbors
            for neighbor in graph.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        
        return result
    
    def optimize_schedule(self, scheduled_tasks: List[ScheduledTask]) -> List[ScheduledTask]:
        """Optimize task schedule for parallel execution"""
        # Build execution graph
        graph = self.build_execution_graph(scheduled_tasks)
        
        # Get topological order
        execution_order = self.topological_sort(graph)
        
        # Create task lookup
        task_lookup = {task.task_id: task for task in scheduled_tasks}
        
        # Assign execution order and estimate times
        current_time = datetime.now()
        resource_usage = {
            "cpu_intensive": 0,
            "memory_intensive": 0,
            "io_intensive": 0,
            "network_intensive": 0
        }
        
        for i, task_id in enumerate(execution_order):
            if task_id not in task_lookup:
                continue
                
            task = task_lookup[task_id]
            task.execution_order = i
            
            # Check if task can start (dependencies satisfied)
            can_start = True
            for dep in task.dependencies:
                if dep.dependency_type in ["blocks", "requires"]:
                    if dep.depends_on not in self.completed_tasks:
                        can_start = False
                        break
            
            if can_start:
                # Check resource availability
                resource_available = True
                for resource_type in ["cpu_intensive", "memory_intensive", "io_intensive", "network_intensive"]:
                    if getattr(task.resources, resource_type):
                        if resource_usage[resource_type] >= self.resource_limits[resource_type]:
                            resource_available = False
                            break
                
                if resource_available:
                    # Schedule task
                    task.estimated_start = current_time
                    task.estimated_end = current_time + timedelta(minutes=task.resources.estimated_duration)
                    
                    # Update resource usage
                    for resource_type in ["cpu_intensive", "memory_intensive", "io_intensive", "network_intensive"]:
                        if getattr(task.resources, resource_type):
                            resource_usage[resource_type] += 1
                    
                    # Update current time for next task
                    if task.resources.estimated_duration > 0:
                        current_time = task.estimated_end
                else:
                    # Task is blocked by resource constraints
                    task.status = TaskStatus.BLOCKED
            else:
                # Task is blocked by dependencies
                task.status = TaskStatus.BLOCKED
        
        return scheduled_tasks
    
    def get_parallel_execution_groups(self, scheduled_tasks: List[ScheduledTask]) -> List[List[ScheduledTask]]:
        """Group tasks that can be executed in parallel"""
        groups = []
        current_group = []
        current_group_resources = {
            "cpu_intensive": 0,
            "memory_intensive": 0,
            "io_intensive": 0,
            "network_intensive": 0
        }
        
        for task in sorted(scheduled_tasks, key=lambda t: t.execution_order):
            if task.status != TaskStatus.PENDING:
                continue
            
            # Check if task can be added to current group
            can_add = True
            for resource_type in ["cpu_intensive", "memory_intensive", "io_intensive", "network_intensive"]:
                if getattr(task.resources, resource_type):
                    if current_group_resources[resource_type] + 1 > self.resource_limits[resource_type]:
                        can_add = False
                        break
            
            if can_add and len(current_group) < self.max_parallel_tasks:
                current_group.append(task)
                for resource_type in ["cpu_intensive", "memory_intensive", "io_intensive", "network_intensive"]:
                    if getattr(task.resources, resource_type):
                        current_group_resources[resource_type] += 1
            else:
                # Start new group
                if current_group:
                    groups.append(current_group)
                current_group = [task]
                current_group_resources = {
                    "cpu_intensive": 1 if task.resources.cpu_intensive else 0,
                    "memory_intensive": 1 if task.resources.memory_intensive else 0,
                    "io_intensive": 1 if task.resources.io_intensive else 0,
                    "network_intensive": 1 if task.resources.network_intensive else 0
                }
        
        if current_group:
            groups.append(current_group)
        
        return groups
    
    def generate_schedule_report(self, scheduled_tasks: List[ScheduledTask]) -> Dict[str, Any]:
        """Generate a comprehensive schedule report"""
        total_tasks = len(scheduled_tasks)
        pending_tasks = len([t for t in scheduled_tasks if t.status == TaskStatus.PENDING])
        blocked_tasks = len([t for t in scheduled_tasks if t.status == TaskStatus.BLOCKED])
        
        # Calculate total estimated time
        total_time = sum(task.resources.estimated_duration for task in scheduled_tasks if task.estimated_start)
        
        # Get parallel execution groups
        parallel_groups = self.get_parallel_execution_groups(scheduled_tasks)
        
        # Calculate efficiency metrics
        parallel_efficiency = len(parallel_groups) / total_tasks if total_tasks > 0 else 0
        resource_utilization = {
            "cpu_intensive": len([t for t in scheduled_tasks if t.resources.cpu_intensive]) / total_tasks if total_tasks > 0 else 0,
            "memory_intensive": len([t for t in scheduled_tasks if t.resources.memory_intensive]) / total_tasks if total_tasks > 0 else 0,
            "io_intensive": len([t for t in scheduled_tasks if t.resources.io_intensive]) / total_tasks if total_tasks > 0 else 0,
            "network_intensive": len([t for t in scheduled_tasks if t.resources.network_intensive]) / total_tasks if total_tasks > 0 else 0
        }
        
        return {
            "total_tasks": total_tasks,
            "pending_tasks": pending_tasks,
            "blocked_tasks": blocked_tasks,
            "total_estimated_time_minutes": total_time,
            "parallel_groups": len(parallel_groups),
            "parallel_efficiency": round(parallel_efficiency, 2),
            "resource_utilization": resource_utilization,
            "execution_plan": [
                {
                    "group_id": i,
                    "tasks": [
                        {
                            "task_id": task.task_id,
                            "title": task.title,
                            "priority": task.priority.value,
                            "estimated_duration": task.resources.estimated_duration,
                            "resources": {
                                "cpu_intensive": task.resources.cpu_intensive,
                                "memory_intensive": task.resources.memory_intensive,
                                "io_intensive": task.resources.io_intensive,
                                "network_intensive": task.resources.network_intensive
                            }
                        }
                        for task in group
                    ]
                }
                for i, group in enumerate(parallel_groups)
            ],
            "blocked_tasks_details": [
                {
                    "task_id": task.task_id,
                    "title": task.title,
                    "dependencies": [
                        {
                            "depends_on": dep.depends_on,
                            "type": dep.dependency_type,
                            "weight": dep.weight
                        }
                        for dep in task.dependencies
                    ]
                }
                for task in scheduled_tasks if task.status == TaskStatus.BLOCKED
            ]
        }
    
    def get_optimized_task_list(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get optimized list of tasks to execute next"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get pending tasks
            cursor.execute('''
                SELECT id, title, objective, requirements, constraints, 
                       acceptance_criteria, priority, status, assigned_to,
                       created_at, last_executed_at, execution_count
                FROM dynamic_tasks 
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT ?
            ''', (limit * 2,))  # Get more to allow for filtering
            
            tasks = []
            for row in cursor.fetchall():
                tasks.append({
                    "id": row[0],
                    "title": row[1],
                    "objective": row[2],
                    "requirements": row[3],
                    "constraints": row[4],
                    "acceptance_criteria": row[5],
                    "priority": row[6],
                    "status": row[7],
                    "assigned_to": row[8],
                    "created_at": row[9],
                    "last_executed_at": row[10],
                    "execution_count": row[11]
                })
            
            # Create scheduled tasks
            scheduled_tasks = self.create_scheduled_tasks(tasks)
            
            # Optimize schedule
            optimized_tasks = self.optimize_schedule(scheduled_tasks)
            
            # Get parallel execution groups
            parallel_groups = self.get_parallel_execution_groups(optimized_tasks)
            
            # Return first group of tasks ready for execution
            if parallel_groups:
                ready_tasks = parallel_groups[0]
                return [
                    {
                        "task_id": task.task_id,
                        "title": task.title,
                        "priority": task.priority.value,
                        "estimated_duration": task.resources.estimated_duration,
                        "can_parallelize": task.can_parallelize,
                        "execution_order": task.execution_order
                    }
                    for task in ready_tasks[:limit]
                ]
            else:
                return []
                
        except Exception as e:
            logger.error(f"Failed to get optimized task list: {e}")
            return []
        finally:
            if 'conn' in locals():
                conn.close()

# Global instance
task_scheduler = TaskScheduler()
