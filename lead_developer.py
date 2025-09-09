"""
Lead Developer System for Zoe
Complete project analysis, repair, deployment, and testing
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from enum import Enum
import subprocess
import sqlite3
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.append("/app")

router = APIRouter(prefix="/api/developer", tags=["developer"])

class TaskStatus(Enum):
    ANALYZING = "analyzing"
    ISSUES_FOUND = "issues_found"
    SOLUTION_PROPOSED = "solution_proposed"
    APPROVED = "approved"
    DEPLOYING = "deploying"
    TESTING = "testing"
    COMPLETED = "completed"
    FAILED = "failed"

class DevelopmentTask(BaseModel):
    task_id: Optional[str] = None
    type: str  # "analysis", "fix", "feature", "deployment"
    description: str
    auto_deploy: bool = False

class ProjectAnalysis(BaseModel):
    timestamp: datetime
    issues: List[Dict[str, Any]]
    recommendations: List[Dict[str, Any]]
    health_score: float
    
class DeploymentPlan(BaseModel):
    task_id: str
    changes: List[Dict[str, str]]  # file: content
    sql_migrations: Optional[List[str]] = None
    rollback_plan: Dict[str, Any]
    tests: List[Dict[str, str]]

# In-memory task storage (should be in database for production)
tasks = {}

class LeadDeveloper:
    """Autonomous Lead Developer with full project control"""
    
    def __init__(self):
        self.project_root = Path("/app")
        self.backup_dir = Path("/app/backups")
        self.backup_dir.mkdir(exist_ok=True)
        
    async def analyze_project(self) -> ProjectAnalysis:
        """Complete project analysis"""
        issues = []
        recommendations = []
        
        # 1. Check Docker containers
        containers = self._check_containers()
        for container in containers:
            if container['status'] != 'running':
                issues.append({
                    'type': 'container',
                    'severity': 'high',
                    'container': container['name'],
                    'issue': f"Container {container['name']} is {container['status']}"
                })
        
        # 2. Analyze code quality
        code_issues = await self._analyze_code_quality()
        issues.extend(code_issues)
        
        # 3. Check database integrity
        db_issues = self._check_database()
        issues.extend(db_issues)
        
        # 4. Security audit
        security_issues = self._security_audit()
        issues.extend(security_issues)
        
        # 5. Performance metrics
        perf_issues = self._check_performance()
        issues.extend(perf_issues)
        
        # 6. Check for outdated dependencies
        dep_issues = self._check_dependencies()
        issues.extend(dep_issues)
        
        # Generate recommendations using AI
        recommendations = await self._generate_recommendations(issues)
        
        # Calculate health score
        health_score = max(0, 100 - (len(issues) * 5))
        
        return ProjectAnalysis(
            timestamp=datetime.now(),
            issues=issues,
            recommendations=recommendations,
            health_score=health_score
        )
    
    def _check_containers(self) -> List[Dict]:
        """Check all Docker containers"""
        result = subprocess.run(
            "docker ps -a --format '{{.Names}}|{{.Status}}' | grep zoe-",
            shell=True, capture_output=True, text=True
        )
        containers = []
        for line in result.stdout.strip().split('\n'):
            if line:
                parts = line.split('|')
                containers.append({
                    'name': parts[0],
                    'status': 'running' if 'Up' in parts[1] else 'stopped'
                })
        return containers
    
    async def _analyze_code_quality(self) -> List[Dict]:
        """Analyze code for issues"""
        issues = []
        
        # Check for common Python issues
        result = subprocess.run(
            "find /app -name '*.py' -exec grep -l 'except:' {} \\;",
            shell=True, capture_output=True, text=True
        )
        if result.stdout:
            for file in result.stdout.strip().split('\n'):
                issues.append({
                    'type': 'code',
                    'severity': 'medium',
                    'file': file,
                    'issue': 'Bare except clause found - should specify exception type'
                })
        
        # Check for TODOs
        result = subprocess.run(
            "grep -r 'TODO\\|FIXME' /app --include='*.py' | head -10",
            shell=True, capture_output=True, text=True
        )
        if result.stdout:
            issues.append({
                'type': 'code',
                'severity': 'low',
                'issue': f"Found {len(result.stdout.strip().split('\\n'))} TODOs/FIXMEs in code"
            })
        
        return issues
    
    def _check_database(self) -> List[Dict]:
        """Check database integrity"""
        issues = []
        try:
            conn = sqlite3.connect("/app/data/zoe.db")
            cursor = conn.cursor()
            
            # Check for missing indexes
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = cursor.fetchall()
            
            for table in tables:
                cursor.execute(f"PRAGMA index_list({table[0]})")
                indexes = cursor.fetchall()
                if not indexes and table[0] != 'sqlite_sequence':
                    issues.append({
                        'type': 'database',
                        'severity': 'medium',
                        'table': table[0],
                        'issue': f"Table {table[0]} has no indexes"
                    })
            
            conn.close()
        except Exception as e:
            issues.append({
                'type': 'database',
                'severity': 'high',
                'issue': f"Database error: {str(e)}"
            })
        
        return issues
    
    def _security_audit(self) -> List[Dict]:
        """Security audit"""
        issues = []
        
        # Check for hardcoded secrets
        result = subprocess.run(
            "grep -r 'api_key\\|password\\|secret' /app --include='*.py' | grep -v '#' | grep '=' | head -5",
            shell=True, capture_output=True, text=True
        )
        if result.stdout:
            issues.append({
                'type': 'security',
                'severity': 'critical',
                'issue': 'Potential hardcoded secrets found in code'
            })
        
        # Check file permissions
        result = subprocess.run(
            "find /app -type f -perm 777",
            shell=True, capture_output=True, text=True
        )
        if result.stdout:
            issues.append({
                'type': 'security',
                'severity': 'high',
                'issue': 'Files with world-writable permissions found'
            })
        
        return issues
    
    def _check_performance(self) -> List[Dict]:
        """Check performance metrics"""
        issues = []
        
        # Check memory usage
        result = subprocess.run(
            "free -m | grep Mem | awk '{print $3/$2 * 100}'",
            shell=True, capture_output=True, text=True
        )
        try:
            mem_usage = float(result.stdout.strip())
            if mem_usage > 80:
                issues.append({
                    'type': 'performance',
                    'severity': 'high',
                    'issue': f'Memory usage at {mem_usage:.1f}%'
                })
        except:
            pass
        
        return issues
    
    def _check_dependencies(self) -> List[Dict]:
        """Check for outdated dependencies"""
        issues = []
        
        # This would normally use pip list --outdated but simplified for now
        issues.append({
            'type': 'dependencies',
            'severity': 'low',
            'issue': 'Dependency audit recommended'
        })
        
        return issues
    
    async def _generate_recommendations(self, issues: List[Dict]) -> List[Dict]:
        """Use AI to generate recommendations based on issues"""
        from ai_client_complete import get_ai_response
        
        context = f"""You are Zack, lead developer. Analyze these issues and provide 3-5 actionable recommendations:
        
Issues found:
{json.dumps(issues, indent=2)}

Provide recommendations in this JSON format:
[
  {{"priority": "high", "action": "specific action", "expected_outcome": "result"}},
  ...
]

Be direct and specific."""
        
        response = await get_ai_response(context, {"mode": "developer"})
        
        try:
            # Parse AI response as JSON
            import re
            json_match = re.search(r'\[.*?\]', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
        except:
            pass
        
        return [{"priority": "high", "action": "Review and fix identified issues", "expected_outcome": "Improved system stability"}]
    
    async def create_solution(self, task_id: str, issues: List[Dict]) -> DeploymentPlan:
        """Create solution for identified issues"""
        from ai_client_complete import get_ai_response
        
        # Get AI to generate fixes
        context = f"""You are Zack, lead developer. Create fixes for these issues:
        
{json.dumps(issues, indent=2)}

Generate:
1. Python code fixes (complete files)
2. SQL migrations if needed
3. Test cases
4. Rollback plan

Format as executable code with clear file paths."""
        
        response = await get_ai_response(context, {"mode": "developer"})
        
        # Parse response into deployment plan
        changes = self._parse_code_blocks(response)
        sql_migrations = self._parse_sql_blocks(response)
        tests = self._parse_test_blocks(response)
        
        return DeploymentPlan(
            task_id=task_id,
            changes=changes,
            sql_migrations=sql_migrations,
            rollback_plan=self._create_rollback_plan(changes),
            tests=tests
        )
    
    def _parse_code_blocks(self, response: str) -> List[Dict[str, str]]:
        """Extract code blocks from AI response"""
        import re
        changes = []
        
        # Find Python code blocks with filenames
        pattern = r'```python\n# (?:filename:|file:) (.*?)\n(.*?)```'
        matches = re.findall(pattern, response, re.DOTALL)
        
        for filename, code in matches:
            changes.append({'file': filename.strip(), 'content': code.strip()})
        
        return changes
    
    def _parse_sql_blocks(self, response: str) -> List[str]:
        """Extract SQL from response"""
        import re
        sql = []
        
        pattern = r'```sql\n(.*?)```'
        matches = re.findall(pattern, response, re.DOTALL)
        
        for match in matches:
            sql.append(match.strip())
        
        return sql
    
    def _parse_test_blocks(self, response: str) -> List[Dict[str, str]]:
        """Extract test cases"""
        import re
        tests = []
        
        pattern = r'```(?:bash|python)\n# test: (.*?)\n(.*?)```'
        matches = re.findall(pattern, response, re.DOTALL)
        
        for name, code in matches:
            tests.append({'name': name.strip(), 'code': code.strip()})
        
        return tests
    
    def _create_rollback_plan(self, changes: List[Dict]) -> Dict:
        """Create rollback plan"""
        rollback = {
            'backup_dir': f"/app/backups/{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            'files_to_restore': [change['file'] for change in changes],
            'commands': [
                f"cp {change['file']} {{backup_dir}}/" for change in changes
            ]
        }
        return rollback
    
    async def deploy(self, plan: DeploymentPlan) -> Dict[str, Any]:
        """Deploy changes with rollback capability"""
        results = {'success': [], 'failed': []}
        
        # Create backup
        backup_dir = Path(plan.rollback_plan['backup_dir'])
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            # Backup existing files
            for change in plan.changes:
                file_path = Path(change['file'])
                if file_path.exists():
                    subprocess.run(f"cp {file_path} {backup_dir}/", shell=True)
            
            # Apply changes
            for change in plan.changes:
                try:
                    file_path = Path(change['file'])
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text(change['content'])
                    results['success'].append(change['file'])
                except Exception as e:
                    results['failed'].append({'file': change['file'], 'error': str(e)})
            
            # Run SQL migrations
            if plan.sql_migrations:
                conn = sqlite3.connect("/app/data/zoe.db")
                cursor = conn.cursor()
                for sql in plan.sql_migrations:
                    try:
                        cursor.executescript(sql)
                        conn.commit()
                    except Exception as e:
                        results['failed'].append({'sql': sql[:50], 'error': str(e)})
                conn.close()
            
            # Restart services if needed
            if any('services/zoe-core' in f['file'] for f in plan.changes):
                subprocess.run("docker compose restart zoe-core", shell=True)
                await asyncio.sleep(5)
            
        except Exception as e:
            # Rollback on failure
            await self.rollback(plan)
            raise HTTPException(status_code=500, detail=f"Deployment failed: {str(e)}")
        
        return results
    
    async def rollback(self, plan: DeploymentPlan):
        """Rollback deployment"""
        backup_dir = plan.rollback_plan['backup_dir']
        for file in plan.rollback_plan['files_to_restore']:
            backup_file = f"{backup_dir}/{Path(file).name}"
            if Path(backup_file).exists():
                subprocess.run(f"cp {backup_file} {file}", shell=True)
    
    async def run_tests(self, plan: DeploymentPlan) -> Dict[str, Any]:
        """Run test suite"""
        results = {'passed': [], 'failed': []}
        
        for test in plan.tests:
            try:
                result = subprocess.run(
                    test['code'],
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if result.returncode == 0:
                    results['passed'].append(test['name'])
                else:
                    results['failed'].append({
                        'name': test['name'],
                        'error': result.stderr or result.stdout
                    })
            except Exception as e:
                results['failed'].append({'name': test['name'], 'error': str(e)})
        
        return results

# Initialize lead developer
lead = LeadDeveloper()

@router.post("/analyze")
async def analyze_project():
    """Analyze entire project and identify issues"""
    analysis = await lead.analyze_project()
    
    # Store analysis
    task_id = f"analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    tasks[task_id] = {
        'status': TaskStatus.ISSUES_FOUND,
        'analysis': analysis.dict(),
        'created': datetime.now()
    }
    
    return {
        'task_id': task_id,
        'health_score': analysis.health_score,
        'issues_count': len(analysis.issues),
        'recommendations': analysis.recommendations
    }

@router.post("/propose-fix/{task_id}")
async def propose_fix(task_id: str):
    """Create solution for identified issues"""
    if task_id not in tasks:
        raise HTTPException(404, "Task not found")
    
    task = tasks[task_id]
    if 'analysis' not in task:
        raise HTTPException(400, "No analysis found for task")
    
    # Create solution
    plan = await lead.create_solution(task_id, task['analysis']['issues'])
    
    task['status'] = TaskStatus.SOLUTION_PROPOSED
    task['plan'] = plan.dict()
    
    return {
        'task_id': task_id,
        'changes_count': len(plan.changes),
        'has_migrations': bool(plan.sql_migrations),
        'tests_count': len(plan.tests),
        'plan_summary': {
            'files_to_change': [c['file'] for c in plan.changes],
            'rollback_available': True
        }
    }

@router.post("/approve/{task_id}")
async def approve_deployment(task_id: str, background_tasks: BackgroundTasks):
    """Approve and deploy solution"""
    if task_id not in tasks:
        raise HTTPException(404, "Task not found")
    
    task = tasks[task_id]
    if task['status'] != TaskStatus.SOLUTION_PROPOSED:
        raise HTTPException(400, "No solution proposed for approval")
    
    task['status'] = TaskStatus.APPROVED
    
    # Deploy in background
    background_tasks.add_task(deploy_and_test, task_id)
    
    return {
        'task_id': task_id,
        'status': 'deployment_started',
        'message': 'Deployment initiated in background'
    }

async def deploy_and_test(task_id: str):
    """Background task to deploy and test"""
    task = tasks[task_id]
    
    try:
        # Deploy
        task['status'] = TaskStatus.DEPLOYING
        plan = DeploymentPlan(**task['plan'])
        deploy_results = await lead.deploy(plan)
        task['deploy_results'] = deploy_results
        
        # Test
        task['status'] = TaskStatus.TESTING
        test_results = await lead.run_tests(plan)
        task['test_results'] = test_results
        
        # Mark complete
        task['status'] = TaskStatus.COMPLETED
        task['completed'] = datetime.now()
        
    except Exception as e:
        task['status'] = TaskStatus.FAILED
        task['error'] = str(e)

@router.get("/task/{task_id}")
async def get_task_status(task_id: str):
    """Get task status and results"""
    if task_id not in tasks:
        raise HTTPException(404, "Task not found")
    
    task = tasks[task_id]
    return {
        'task_id': task_id,
        'status': task['status'].value,
        'created': task.get('created'),
        'completed': task.get('completed'),
        'analysis': task.get('analysis', {}).get('health_score'),
        'deploy_results': task.get('deploy_results'),
        'test_results': task.get('test_results'),
        'error': task.get('error')
    }

@router.post("/chat")
async def developer_chat(msg: DevelopmentTask):
    """Lead developer chat with action capability"""
    from ai_client_complete import get_ai_response
    
    context = f"""You are Zack, lead developer with full system control.
Current task: {msg.description}
Type: {msg.type}

Provide direct, actionable response with specific implementation steps."""
    
    response = await get_ai_response(context, {"mode": "developer"})
    
    # If auto_deploy is set, create and execute task
    if msg.auto_deploy and msg.type in ["fix", "feature"]:
        task_id = f"{msg.type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        tasks[task_id] = {
            'status': TaskStatus.ANALYZING,
            'description': msg.description,
            'response': response
        }
        return {
            'response': response,
            'task_id': task_id,
            'auto_deploy': True
        }
    
    return {'response': response}

@router.get("/status")
async def status():
    """Lead developer status"""
    return {
        'status': 'operational',
        'role': 'lead_developer',
        'capabilities': [
            'project_analysis',
            'issue_detection',
            'solution_generation',
            'automated_deployment',
            'testing',
            'rollback'
        ],
        'active_tasks': len([t for t in tasks.values() if t['status'] not in [TaskStatus.COMPLETED, TaskStatus.FAILED]])
    }
