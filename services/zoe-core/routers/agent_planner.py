"""
Agent-Based Task Planning System
===============================

Implements advanced agent concepts for goal-driven task planning with inter-agent communication.
Based on the analysis document priorities for intelligent task decomposition and execution.

Features:
- AgentGoal class for structured objectives
- TaskPlanner that breaks requests into executable steps
- Inter-agent communication via Redis
- Agent registry in database
- Goal decomposition and conflict resolution
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
import os
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Set
from datetime import datetime, timedelta
from enum import Enum
import sqlite3
import json
import asyncio
import redis
import hashlib
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent-planning"])

# Redis connection for inter-agent communication
try:
    redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
    redis_client.ping()
except:
    redis_client = None
    logger.warning("Redis not available - inter-agent communication disabled")

class GoalPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class GoalStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class AgentType(str, Enum):
    PLANNER = "planner"
    EXECUTOR = "executor"
    VALIDATOR = "validator"
    COORDINATOR = "coordinator"
    SPECIALIST = "specialist"

class AgentGoal(BaseModel):
    """Structured goal with constraints and success criteria"""
    id: Optional[str] = None
    title: str = Field(..., description="Human-readable goal title")
    objective: str = Field(..., description="What needs to be accomplished")
    constraints: List[str] = Field(default_factory=list, description="What must not be broken")
    success_criteria: List[str] = Field(default_factory=list, description="How to verify success")
    priority: GoalPriority = GoalPriority.MEDIUM
    deadline: Optional[datetime] = None
    estimated_duration_minutes: Optional[int] = None
    dependencies: List[str] = Field(default_factory=list, description="Other goals that must complete first")
    context: Dict[str, Any] = Field(default_factory=dict, description="Additional context")
    assigned_agent: Optional[str] = None
    status: GoalStatus = GoalStatus.PENDING
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

class TaskStep(BaseModel):
    """Individual step in a task plan"""
    step_id: str
    description: str
    agent_type: AgentType
    estimated_duration_minutes: int
    dependencies: List[str] = Field(default_factory=list)
    resources_needed: List[str] = Field(default_factory=list)
    validation_criteria: List[str] = Field(default_factory=list)
    rollback_plan: Optional[str] = None
    status: str = "pending"
    assigned_agent: Optional[str] = None
    execution_order: int

class TaskPlan(BaseModel):
    """Complete plan for achieving a goal"""
    plan_id: str
    goal_id: str
    goal: AgentGoal
    steps: List[TaskStep]
    estimated_total_duration: int
    critical_path: List[str]  # Step IDs that must complete in sequence
    parallel_steps: List[List[str]]  # Groups of steps that can run in parallel
    risk_assessment: Dict[str, Any]
    rollback_strategy: Dict[str, Any]
    created_at: datetime
    status: str = "planned"

class Agent(BaseModel):
    """Agent in the system"""
    agent_id: str
    agent_type: AgentType
    name: str
    capabilities: List[str]
    current_load: int = 0
    max_concurrent_tasks: int = 3
    specializations: List[str] = Field(default_factory=list)
    status: str = "available"  # available, busy, offline
    last_activity: Optional[datetime] = None

class AgentMessage(BaseModel):
    """Message between agents"""
    message_id: str
    from_agent: str
    to_agent: str
    message_type: str  # request, response, notification, coordination
    content: Dict[str, Any]
    timestamp: datetime
    priority: GoalPriority = GoalPriority.MEDIUM
    requires_response: bool = False
    response_deadline: Optional[datetime] = None

# Database setup
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

def init_agent_db():
    """Initialize agent planning database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Goals table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS agent_goals (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            objective TEXT NOT NULL,
            constraints TEXT,  -- JSON array
            success_criteria TEXT,  -- JSON array
            priority TEXT DEFAULT 'medium',
            deadline TEXT,
            estimated_duration_minutes INTEGER,
            dependencies TEXT,  -- JSON array
            context TEXT,  -- JSON object
            assigned_agent TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT
        )
    ''')
    
    # Task plans table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS task_plans (
            plan_id TEXT PRIMARY KEY,
            goal_id TEXT NOT NULL,
            goal_data TEXT,  -- JSON object
            steps TEXT,  -- JSON array
            estimated_total_duration INTEGER,
            critical_path TEXT,  -- JSON array
            parallel_steps TEXT,  -- JSON array
            risk_assessment TEXT,  -- JSON object
            rollback_strategy TEXT,  -- JSON object
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'planned',
            FOREIGN KEY (goal_id) REFERENCES agent_goals(id)
        )
    ''')
    
    # Agents table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS agents (
            agent_id TEXT PRIMARY KEY,
            agent_type TEXT NOT NULL,
            name TEXT NOT NULL,
            capabilities TEXT,  -- JSON array
            current_load INTEGER DEFAULT 0,
            max_concurrent_tasks INTEGER DEFAULT 3,
            specializations TEXT,  -- JSON array
            status TEXT DEFAULT 'available',
            last_activity TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Agent messages table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS agent_messages (
            message_id TEXT PRIMARY KEY,
            from_agent TEXT NOT NULL,
            to_agent TEXT NOT NULL,
            message_type TEXT NOT NULL,
            content TEXT,  -- JSON object
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            priority TEXT DEFAULT 'medium',
            requires_response BOOLEAN DEFAULT FALSE,
            response_deadline TEXT,
            responded_at TEXT
        )
    ''')
    
    # Task executions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS task_executions (
            execution_id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL,
            step_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            started_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            status TEXT DEFAULT 'running',
            result TEXT,  -- JSON object
            error_message TEXT,
            FOREIGN KEY (plan_id) REFERENCES task_plans(plan_id)
        )
    ''')
    
    conn.commit()
    conn.close()
    
    # Initialize default agents
    initialize_default_agents()

def initialize_default_agents():
    """Initialize default agents in the system"""
    default_agents = [
        Agent(
            agent_id="planner-001",
            agent_type=AgentType.PLANNER,
            name="Zoe Planner",
            capabilities=["goal_decomposition", "resource_allocation", "timeline_estimation"],
            specializations=["general_planning", "conflict_resolution"]
        ),
        Agent(
            agent_id="executor-001", 
            agent_type=AgentType.EXECUTOR,
            name="Zoe Executor",
            capabilities=["code_execution", "file_operations", "api_calls"],
            specializations=["backend_tasks", "file_management"]
        ),
        Agent(
            agent_id="validator-001",
            agent_type=AgentType.VALIDATOR,
            name="Zoe Validator", 
            capabilities=["code_review", "testing", "validation"],
            specializations=["quality_assurance", "error_detection"]
        ),
        Agent(
            agent_id="coordinator-001",
            agent_type=AgentType.COORDINATOR,
            name="Zoe Coordinator",
            capabilities=["task_coordination", "resource_management", "communication"],
            specializations=["workflow_management", "inter_agent_communication"]
        )
    ]
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    for agent in default_agents:
        cursor.execute('''
            INSERT OR REPLACE INTO agents 
            (agent_id, agent_type, name, capabilities, specializations, status)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            agent.agent_id,
            agent.agent_type.value,
            agent.name,
            json.dumps(agent.capabilities),
            json.dumps(agent.specializations),
            agent.status
        ))
    
    conn.commit()
    conn.close()

# Initialize database
init_agent_db()

class TaskPlanner:
    """Main task planning engine"""
    
    def __init__(self):
        self.redis_client = redis_client
    
    def create_goal(self, goal: AgentGoal) -> AgentGoal:
        """Create a new agent goal"""
        goal.id = hashlib.md5(f"{goal.title}{datetime.now()}".encode()).hexdigest()[:12]
        goal.created_at = datetime.now()
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO agent_goals 
            (id, title, objective, constraints, success_criteria, priority, deadline,
             estimated_duration_minutes, dependencies, context, assigned_agent, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            goal.id,
            goal.title,
            goal.objective,
            json.dumps(goal.constraints),
            json.dumps(goal.success_criteria),
            goal.priority.value,
            goal.deadline.isoformat() if goal.deadline else None,
            goal.estimated_duration_minutes,
            json.dumps(goal.dependencies),
            json.dumps(goal.context),
            goal.assigned_agent,
            goal.status.value,
            goal.created_at.isoformat()
        ))
        
        conn.commit()
        conn.close()
        
        # Notify agents about new goal
        self._broadcast_goal_created(goal)
        
        return goal
    
    def plan_goal_execution(self, goal_id: str) -> TaskPlan:
        """Generate a detailed execution plan for a goal"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM agent_goals WHERE id = ?', (goal_id,))
        row = cursor.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Goal not found")
        
        goal = self._row_to_goal(row)
        
        # Generate plan based on goal type and complexity
        plan = self._generate_plan(goal)
        
        # Store plan
        goal_dict = goal.dict()
        goal_dict['created_at'] = goal_dict['created_at'].isoformat() if goal_dict['created_at'] else None
        goal_dict['completed_at'] = goal_dict['completed_at'].isoformat() if goal_dict['completed_at'] else None
        goal_dict['deadline'] = goal_dict['deadline'].isoformat() if goal_dict['deadline'] else None
        
        cursor.execute('''
            INSERT INTO task_plans 
            (plan_id, goal_id, goal_data, steps, estimated_total_duration,
             critical_path, parallel_steps, risk_assessment, rollback_strategy, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            plan.plan_id,
            plan.goal_id,
            json.dumps(goal_dict),
            json.dumps([step.dict() for step in plan.steps]),
            plan.estimated_total_duration,
            json.dumps(plan.critical_path),
            json.dumps(plan.parallel_steps),
            json.dumps(plan.risk_assessment),
            json.dumps(plan.rollback_strategy),
            plan.status
        ))
        
        conn.commit()
        conn.close()
        
        # Update goal status
        goal.status = GoalStatus.PLANNING
        self._update_goal_status(goal_id, GoalStatus.PLANNING)
        
        return plan
    
    def _generate_plan(self, goal: AgentGoal) -> TaskPlan:
        """Generate execution plan based on goal analysis"""
        plan_id = hashlib.md5(f"plan_{goal.id}_{datetime.now()}".encode()).hexdigest()[:12]
        
        # Analyze goal complexity and decompose into steps
        steps = self._decompose_goal(goal)
        
        # Calculate critical path and parallel execution opportunities
        critical_path, parallel_steps = self._analyze_dependencies(steps)
        
        # Estimate total duration
        total_duration = sum(step.estimated_duration_minutes for step in steps)
        
        # Assess risks
        risk_assessment = self._assess_risks(goal, steps)
        
        # Create rollback strategy
        rollback_strategy = self._create_rollback_strategy(steps)
        
        return TaskPlan(
            plan_id=plan_id,
            goal_id=goal.id,
            goal=goal,
            steps=steps,
            estimated_total_duration=total_duration,
            critical_path=critical_path,
            parallel_steps=parallel_steps,
            risk_assessment=risk_assessment,
            rollback_strategy=rollback_strategy,
            created_at=datetime.now()
        )
    
    def _decompose_goal(self, goal: AgentGoal) -> List[TaskStep]:
        """Decompose goal into executable steps"""
        steps = []
        
        # Example: "Plan family movie night Friday"
        if "movie" in goal.objective.lower() and "family" in goal.objective.lower():
            steps = [
                TaskStep(
                    step_id="step_001",
                    description="Check family calendar availability",
                    agent_type=AgentType.EXECUTOR,
                    estimated_duration_minutes=5,
                    execution_order=1,
                    validation_criteria=["Calendar checked", "Conflicts identified"]
                ),
                TaskStep(
                    step_id="step_002", 
                    description="Research movie options and showtimes",
                    agent_type=AgentType.SPECIALIST,
                    estimated_duration_minutes=10,
                    execution_order=2,
                    validation_criteria=["Movies found", "Showtimes available"]
                ),
                TaskStep(
                    step_id="step_003",
                    description="Create calendar event",
                    agent_type=AgentType.EXECUTOR,
                    estimated_duration_minutes=3,
                    dependencies=["step_001", "step_002"],
                    execution_order=3,
                    validation_criteria=["Event created", "Family notified"]
                ),
                TaskStep(
                    step_id="step_004",
                    description="Add movie snacks to shopping list",
                    agent_type=AgentType.EXECUTOR,
                    estimated_duration_minutes=2,
                    execution_order=4,
                    validation_criteria=["Items added to list"]
                )
            ]
        
        # Example: "Enhance Memory System with Advanced Features"
        elif "memory" in goal.objective.lower() and "enhance" in goal.objective.lower():
            steps = [
                TaskStep(
                    step_id="step_001",
                    description="Analyze current memory system",
                    agent_type=AgentType.VALIDATOR,
                    estimated_duration_minutes=15,
                    execution_order=1,
                    validation_criteria=["System analyzed", "Gaps identified"]
                ),
                TaskStep(
                    step_id="step_002",
                    description="Design new memory features",
                    agent_type=AgentType.PLANNER,
                    estimated_duration_minutes=30,
                    dependencies=["step_001"],
                    execution_order=2,
                    validation_criteria=["Features designed", "Architecture planned"]
                ),
                TaskStep(
                    step_id="step_003",
                    description="Implement semantic search",
                    agent_type=AgentType.EXECUTOR,
                    estimated_duration_minutes=45,
                    dependencies=["step_002"],
                    execution_order=3,
                    validation_criteria=["Search implemented", "Tests passing"]
                ),
                TaskStep(
                    step_id="step_004",
                    description="Add relationship graphs",
                    agent_type=AgentType.EXECUTOR,
                    estimated_duration_minutes=30,
                    dependencies=["step_003"],
                    execution_order=4,
                    validation_criteria=["Graphs implemented", "Queries working"]
                ),
                TaskStep(
                    step_id="step_005",
                    description="Test and validate system",
                    agent_type=AgentType.VALIDATOR,
                    estimated_duration_minutes=20,
                    dependencies=["step_004"],
                    execution_order=5,
                    validation_criteria=["All tests pass", "Performance verified"]
                )
            ]
        
        # Default decomposition for other goals
        else:
            steps = [
                TaskStep(
                    step_id="step_001",
                    description=f"Analyze requirements for: {goal.objective}",
                    agent_type=AgentType.PLANNER,
                    estimated_duration_minutes=10,
                    execution_order=1,
                    validation_criteria=["Requirements understood"]
                ),
                TaskStep(
                    step_id="step_002",
                    description=f"Execute: {goal.objective}",
                    agent_type=AgentType.EXECUTOR,
                    estimated_duration_minutes=goal.estimated_duration_minutes or 30,
                    dependencies=["step_001"],
                    execution_order=2,
                    validation_criteria=["Task completed"]
                ),
                TaskStep(
                    step_id="step_003",
                    description=f"Validate completion of: {goal.objective}",
                    agent_type=AgentType.VALIDATOR,
                    estimated_duration_minutes=5,
                    dependencies=["step_002"],
                    execution_order=3,
                    validation_criteria=["Validation passed"]
                )
            ]
        
        return steps
    
    def _analyze_dependencies(self, steps: List[TaskStep]) -> tuple[List[str], List[List[str]]]:
        """Analyze step dependencies and identify parallel execution opportunities"""
        critical_path = []
        parallel_steps = []
        
        # Simple dependency analysis
        for step in sorted(steps, key=lambda x: x.execution_order):
            if not step.dependencies:
                critical_path.append(step.step_id)
            else:
                # Check if this step can run in parallel with others
                can_parallel = True
                for dep in step.dependencies:
                    if dep in critical_path:
                        can_parallel = False
                        break
                
                if can_parallel:
                    parallel_steps.append([step.step_id])
                else:
                    critical_path.append(step.step_id)
        
        return critical_path, parallel_steps
    
    def _assess_risks(self, goal: AgentGoal, steps: List[TaskStep]) -> Dict[str, Any]:
        """Assess risks for the execution plan"""
        risks = {
            "high_risk_factors": [],
            "mitigation_strategies": [],
            "contingency_plans": []
        }
        
        # Check for time constraints
        if goal.deadline:
            total_time = sum(step.estimated_duration_minutes for step in steps)
            if total_time > 60:  # More than 1 hour
                risks["high_risk_factors"].append("Long execution time may cause deadline issues")
                risks["mitigation_strategies"].append("Break into smaller chunks with checkpoints")
        
        # Check for complex dependencies
        complex_deps = [step for step in steps if len(step.dependencies) > 1]
        if complex_deps:
            risks["high_risk_factors"].append("Complex dependencies may cause delays")
            risks["mitigation_strategies"].append("Monitor dependency completion closely")
        
        return risks
    
    def _create_rollback_strategy(self, steps: List[TaskStep]) -> Dict[str, Any]:
        """Create rollback strategy for the plan"""
        return {
            "rollback_points": [step.step_id for step in steps],
            "rollback_actions": {
                step.step_id: f"Undo changes from: {step.description}"
                for step in steps
            },
            "full_rollback": "Restore system to state before plan execution",
            "partial_rollback": "Roll back to last successful step"
        }
    
    def _broadcast_goal_created(self, goal: AgentGoal):
        """Broadcast new goal to all agents via Redis"""
        if not self.redis_client:
            return
        
        message = AgentMessage(
            message_id=hashlib.md5(f"msg_{goal.id}_{datetime.now()}".encode()).hexdigest()[:12],
            from_agent="system",
            to_agent="all",
            message_type="goal_created",
            content=goal.dict(),
            timestamp=datetime.now(),
            priority=goal.priority
        )
        
        try:
            self.redis_client.publish("agent_messages", json.dumps(message.dict()))
        except Exception as e:
            logger.error(f"Failed to broadcast goal: {e}")
    
    def _update_goal_status(self, goal_id: str, status: GoalStatus):
        """Update goal status in database"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE agent_goals SET status = ? WHERE id = ?
        ''', (status.value, goal_id))
        
        conn.commit()
        conn.close()
    
    def _row_to_goal(self, row) -> AgentGoal:
        """Convert database row to AgentGoal object"""
        return AgentGoal(
            id=row[0],
            title=row[1],
            objective=row[2],
            constraints=json.loads(row[3] or '[]'),
            success_criteria=json.loads(row[4] or '[]'),
            priority=GoalPriority(row[5]),
            deadline=datetime.fromisoformat(row[6]) if row[6] else None,
            estimated_duration_minutes=row[7],
            dependencies=json.loads(row[8] or '[]'),
            context=json.loads(row[9] or '{}'),
            assigned_agent=row[10],
            status=GoalStatus(row[11]),
            created_at=datetime.fromisoformat(row[12]) if row[12] else None,
            completed_at=datetime.fromisoformat(row[13]) if row[13] else None
        )

# Initialize task planner
task_planner = TaskPlanner()

# API Endpoints

@router.post("/goals", response_model=AgentGoal)
async def create_goal(goal: AgentGoal):
    """Create a new agent goal"""
    try:
        created_goal = task_planner.create_goal(goal)
        return created_goal
    except Exception as e:
        logger.error(f"Failed to create goal: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/goals", response_model=List[AgentGoal])
async def list_goals(status: Optional[str] = None, priority: Optional[str] = None):
    """List agent goals with optional filtering"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    query = "SELECT * FROM agent_goals"
    params = []
    conditions = []
    
    if status:
        conditions.append("status = ?")
        params.append(status)
    
    if priority:
        conditions.append("priority = ?")
        params.append(priority)
    
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    
    query += " ORDER BY created_at DESC"
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    
    goals = [task_planner._row_to_goal(row) for row in rows]
    return goals

@router.get("/goals/{goal_id}", response_model=AgentGoal)
async def get_goal(goal_id: str):
    """Get a specific goal by ID"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM agent_goals WHERE id = ?", (goal_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Goal not found")
    
    return task_planner._row_to_goal(row)

@router.post("/goals/{goal_id}/plan", response_model=TaskPlan)
async def plan_goal_execution(goal_id: str):
    """Generate execution plan for a goal"""
    try:
        plan = task_planner.plan_goal_execution(goal_id)
        return plan
    except Exception as e:
        logger.error(f"Failed to plan goal execution: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/plans/{plan_id}", response_model=TaskPlan)
async def get_plan(plan_id: str):
    """Get a specific execution plan"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM task_plans WHERE plan_id = ?", (plan_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Plan not found")
    
    # Reconstruct TaskPlan object
    goal_data = json.loads(row[2])
    steps_data = json.loads(row[3])
    
    # Convert ISO strings back to datetime objects
    if goal_data.get('created_at'):
        goal_data['created_at'] = datetime.fromisoformat(goal_data['created_at'])
    if goal_data.get('completed_at'):
        goal_data['completed_at'] = datetime.fromisoformat(goal_data['completed_at'])
    if goal_data.get('deadline'):
        goal_data['deadline'] = datetime.fromisoformat(goal_data['deadline'])
    
    goal = AgentGoal(**goal_data)
    steps = [TaskStep(**step) for step in steps_data]
    
    return TaskPlan(
        plan_id=row[0],
        goal_id=row[1],
        goal=goal,
        steps=steps,
        estimated_total_duration=row[4],
        critical_path=json.loads(row[5]),
        parallel_steps=json.loads(row[6]),
        risk_assessment=json.loads(row[7]),
        rollback_strategy=json.loads(row[8]),
        created_at=datetime.fromisoformat(row[9]),
        status=row[10]
    )

@router.get("/agents", response_model=List[Agent])
async def list_agents():
    """List all agents in the system"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM agents ORDER BY agent_type, name")
    rows = cursor.fetchall()
    conn.close()
    
    agents = []
    for row in rows:
        agents.append(Agent(
            agent_id=row[0],
            agent_type=AgentType(row[1]),
            name=row[2],
            capabilities=json.loads(row[3] or '[]'),
            current_load=row[4],
            max_concurrent_tasks=row[5],
            specializations=json.loads(row[6] or '[]'),
            status=row[7],
            last_activity=datetime.fromisoformat(row[8]) if row[8] else None
        ))
    
    return agents

@router.post("/goals/{goal_id}/execute")
async def execute_goal(goal_id: str, background_tasks: BackgroundTasks):
    """Execute a goal using the agent planning system"""
    try:
        # First, generate a plan
        plan = task_planner.plan_goal_execution(goal_id)
        
        # Then execute the plan in background
        background_tasks.add_task(execute_plan_async, plan)
        
        return {
            "message": f"Goal {goal_id} execution started",
            "plan_id": plan.plan_id,
            "estimated_duration": plan.estimated_total_duration,
            "steps": len(plan.steps)
        }
    except Exception as e:
        logger.error(f"Failed to execute goal: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def execute_plan_async(plan: TaskPlan):
    """Execute a plan asynchronously"""
    logger.info(f"Starting execution of plan {plan.plan_id}")
    
    # Update goal status
    task_planner._update_goal_status(plan.goal_id, GoalStatus.EXECUTING)
    
    try:
        # Execute steps in order
        for step in sorted(plan.steps, key=lambda x: x.execution_order):
            await execute_step_async(plan.plan_id, step)
        
        # Mark goal as completed
        task_planner._update_goal_status(plan.goal_id, GoalStatus.COMPLETED)
        
        # Update completion timestamp
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE agent_goals SET completed_at = ? WHERE id = ?
        ''', (datetime.now().isoformat(), plan.goal_id))
        conn.commit()
        conn.close()
        
        logger.info(f"Plan {plan.plan_id} execution completed successfully")
        
    except Exception as e:
        logger.error(f"Plan execution failed: {e}")
        task_planner._update_goal_status(plan.goal_id, GoalStatus.FAILED)

async def execute_step_async(plan_id: str, step: TaskStep):
    """Execute a single step"""
    logger.info(f"Executing step {step.step_id}: {step.description}")
    
    # Find available agent
    agent = await find_available_agent(step.agent_type)
    
    if not agent:
        raise Exception(f"No available agent for step {step.step_id}")
    
    # Record execution start
    execution_id = hashlib.md5(f"exec_{plan_id}_{step.step_id}_{datetime.now()}".encode()).hexdigest()[:12]
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO task_executions 
        (execution_id, plan_id, step_id, agent_id, started_at, status)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        execution_id,
        plan_id,
        step.step_id,
        agent.agent_id,
        datetime.now().isoformat(),
        "running"
    ))
    
    conn.commit()
    conn.close()
    
    try:
        # Simulate step execution (in real implementation, this would call actual agent)
        await asyncio.sleep(step.estimated_duration_minutes / 60.0)  # Convert to seconds for demo
        
        # Mark as completed
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE task_executions 
            SET completed_at = ?, status = ?, result = ?
            WHERE execution_id = ?
        ''', (
            datetime.now().isoformat(),
            "completed",
            json.dumps({"message": "Step completed successfully"}),
            execution_id
        ))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Step {step.step_id} completed successfully")
        
    except Exception as e:
        # Mark as failed
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE task_executions 
            SET completed_at = ?, status = ?, error_message = ?
            WHERE execution_id = ?
        ''', (
            datetime.now().isoformat(),
            "failed",
            str(e),
            execution_id
        ))
        
        conn.commit()
        conn.close()
        
        raise e

async def find_available_agent(agent_type: AgentType) -> Optional[Agent]:
    """Find an available agent of the specified type"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM agents 
        WHERE agent_type = ? AND status = 'available' 
        AND current_load < max_concurrent_tasks
        ORDER BY current_load ASC
        LIMIT 1
    ''', (agent_type.value,))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return None
    
    return Agent(
        agent_id=row[0],
        agent_type=AgentType(row[1]),
        name=row[2],
        capabilities=json.loads(row[3] or '[]'),
        current_load=row[4],
        max_concurrent_tasks=row[5],
        specializations=json.loads(row[6] or '[]'),
        status=row[7],
        last_activity=datetime.fromisoformat(row[8]) if row[8] else None
    )

@router.get("/stats")
async def get_agent_stats():
    """Get agent planning system statistics"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Goal statistics
    cursor.execute("SELECT status, COUNT(*) FROM agent_goals GROUP BY status")
    goal_stats = dict(cursor.fetchall())
    
    # Plan statistics
    cursor.execute("SELECT status, COUNT(*) FROM task_plans GROUP BY status")
    plan_stats = dict(cursor.fetchall())
    
    # Agent statistics
    cursor.execute("SELECT status, COUNT(*) FROM agents GROUP BY status")
    agent_stats = dict(cursor.fetchall())
    
    conn.close()
    
    return {
        "goals": goal_stats,
        "plans": plan_stats,
        "agents": agent_stats,
        "redis_available": redis_client is not None,
        "total_goals": sum(goal_stats.values()),
        "total_plans": sum(plan_stats.values()),
        "total_agents": sum(agent_stats.values())
    }
