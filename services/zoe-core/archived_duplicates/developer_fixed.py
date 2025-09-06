'''Developer Router - USES AI Instead of Hardcoded Responses'''
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Optional
import subprocess
import psutil
import logging
import sys

sys.path.append('/app')

logger = logging.getLogger(__name__)
router = APIRouter(prefix='/api/developer', tags=['developer'])

# Import the AI we just fixed
from ai_client import get_ai_response

class DeveloperChat(BaseModel):
    message: str

class DeveloperTask(BaseModel):
    title: str
    description: Optional[str] = ''
    type: Optional[str] = 'development'
    priority: Optional[str] = 'medium'

# Keep existing helper functions
def execute_command(cmd: str, timeout: int = 10) -> dict:
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True,
            text=True, timeout=timeout, cwd='/app'
        )
        return {
            'stdout': result.stdout[:5000],
            'stderr': result.stderr[:1000],
            'success': result.returncode == 0
        }
    except Exception as e:
        return {'stdout': '', 'stderr': str(e), 'success': False}

def get_real_system_info() -> dict:
    '''Get actual system metrics'''
    info = {}
    
    # Docker containers
    docker_result = execute_command('docker ps --format "table {{.Names}}\t{{.Status}}"')
    info['containers'] = docker_result['stdout'] if docker_result['success'] else 'Unable to get containers'
    
    # Memory
    mem_result = execute_command('free -h')
    info['memory'] = mem_result['stdout'] if mem_result['success'] else 'Unable to get memory'
    
    # CPU
    info['cpu_percent'] = psutil.cpu_percent(interval=1)
    
    # Disk
    disk = psutil.disk_usage('/')
    info['disk_percent'] = disk.percent
    
    return info

@router.get('/status')
async def get_status():
    system_info = get_real_system_info()
    container_count = system_info['containers'].count('Up') if isinstance(system_info['containers'], str) else 0
    
    return {
        'status': 'operational',
        'mode': 'ai-powered-with-routellm',
        'personality': 'Zack',
        'containers_running': container_count,
        'timestamp': datetime.now().isoformat()
    }

@router.post('/chat')
async def developer_chat(msg: DeveloperChat):
    '''ACTUALLY USE THE AI INSTEAD OF HARDCODED RESPONSES'''
    
    message = msg.message
    message_lower = message.lower()
    
    # Get real system context for certain queries
    system_context = ''
    if any(word in message_lower for word in ['status', 'system', 'memory', 'cpu', 'docker', 'container', 'disk']):
        info = get_real_system_info()
        system_context = f"""Real System Data:
Containers:
{info['containers']}

Memory:
{info['memory']}

CPU: {info['cpu_percent']}%
Disk: {info['disk_percent']}% used
"""
    
    # Build message with context
    if system_context:
        full_message = f"""User Query: {message}

{system_context}

Provide a response based on the real data above.
"""
    else:
        full_message = message
    
    try:
        logger.info(f'🤖 Calling AI with RouteLLM for: {message[:50]}...')
        
        # Call the AI with developer context
        ai_response = await get_ai_response(full_message, {'mode': 'developer'})
        
        logger.info('✅ AI response received')
        
        return {
            'response': ai_response,
            'success': True,
            'ai_powered': True,
            'has_real_data': bool(system_context)
        }
        
    except Exception as e:
        logger.error(f'❌ AI call failed: {e}')
        
        # Only use fallback if AI completely fails
        if system_context:
            return {
                'response': f'AI temporarily unavailable. Here\'s the real data:\n\n{system_context}',
                'success': False,
                'error': str(e)
            }
        else:
            return {
                'response': 'AI service temporarily unavailable. Please try again.',
                'success': False,
                'error': str(e)
            }

@router.get('/metrics')
async def get_metrics():
    try:
        return {
            'cpu_percent': psutil.cpu_percent(interval=1),
            'memory_percent': psutil.virtual_memory().percent,
            'disk_percent': psutil.disk_usage('/').percent,
            'memory_gb': round(psutil.virtual_memory().used / (1024**3), 2),
            'disk_gb': round(psutil.disk_usage('/').used / (1024**3), 2)
        }
    except Exception as e:
        return {'error': str(e)}

# Import datetime for timestamps
from datetime import datetime

# Keep task management endpoints
developer_tasks = {}

@router.post('/tasks')
async def create_task(task: DeveloperTask):
    task_id = f'task_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    developer_tasks[task_id] = {
        'id': task_id,
        'title': task.title,
        'description': task.description,
        'type': task.type,
        'priority': task.priority,
        'status': 'pending',
        'created_at': datetime.now().isoformat()
    }
    return {'task_id': task_id, 'status': 'created'}

@router.get('/tasks')
async def list_tasks():
    return {
        'tasks': list(developer_tasks.values()),
        'count': len(developer_tasks)
    }
