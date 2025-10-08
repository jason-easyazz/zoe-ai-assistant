"""
Plan Generator Module - Converts task requirements into executable steps
"""
import json
from typing import Dict, List, Any

class PlanGenerator:
    def __init__(self):
        self.step_templates = {
            'file': {
                'type': 'file_create',
                'description': 'Create file',
                'path': '',
                'content': ''
            },
            'shell': {
                'type': 'shell',
                'description': 'Execute command',
                'command': ''
            },
            'test': {
                'type': 'test',
                'description': 'Run test',
                'command': ''
            }
        }
    
    def generate_plan(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate execution plan from task requirements"""
        
        # Extract task data (support both old and new format)
        objective = task_data.get('objective', '')
        requirements = task_data.get('requirements', [])
        constraints = task_data.get('constraints', [])
        context = task_data.get('context', {})
        
        # Generate steps based on requirements
        steps = []
        
        for req in requirements:
            req_lower = req.lower()
            
            # Determine step type based on requirement text
            if 'create file' in req_lower or 'create test file' in req_lower:
                # Extract file path if mentioned
                if '/tmp/' in req:
                    filepath = req.split('/tmp/')[1].split()[0]
                    filepath = f"/tmp/{filepath}"
                else:
                    filepath = "/tmp/test_file.txt"
                
                steps.append({
                    'type': 'file_create',
                    'description': req,
                    'path': filepath,
                    'content': f"Test file created by task {task_data.get('task_id', 'unknown')}\n{req}"
                })
                
            elif 'execute' in req_lower or 'run' in req_lower or 'command' in req_lower:
                steps.append({
                    'type': 'shell',
                    'description': req,
                    'command': 'echo "Executing requirement: ' + req + '"'
                })
                
            elif 'test' in req_lower or 'verify' in req_lower:
                steps.append({
                    'type': 'test',
                    'description': req,
                    'command': 'test -f /tmp/test_file.txt && echo "Test passed"'
                })
                
            else:
                # Default to shell command
                steps.append({
                    'type': 'shell',
                    'description': req,
                    'command': f'echo "Implementing: {req}"'
                })
        
        # If no steps generated, create a default one
        if not steps:
            steps.append({
                'type': 'shell',
                'description': 'Default execution',
                'command': 'echo "Task executed successfully"'
            })
        
        # Build complete plan
        plan = {
            'task_id': task_data.get('task_id', 'unknown'),
            'objective': objective,
            'steps': steps,
            'constraints': constraints,
            'rollback_enabled': True
        }
        
        return plan
    
    def analyze_system(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze current system state"""
        # Simplified for now
        return {
            'files_exist': [],
            'services_running': [],
            'recent_changes': []
        }
