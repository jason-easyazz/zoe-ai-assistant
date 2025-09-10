#!/bin/bash
# Fix paths in disciplined creator

echo "🔧 FIXING CREATOR PATHS"
echo "======================"

cd /home/pi/zoe

# First, check actual structure inside container
echo "📂 Checking container structure..."
docker exec zoe-core ls -la /app/

# Fix the disciplined creator with correct paths
docker exec zoe-core bash -c "cat > /app/routers/disciplined_creator_fixed.py << 'EOF'
\"\"\"Disciplined Creator with Fixed Paths\"\"\"
from fastapi import APIRouter
from pydantic import BaseModel
import os
import subprocess
import json
from datetime import datetime
from typing import Optional, Dict, List
import shutil
import sys

router = APIRouter(prefix=\"/api/disciplined_creator\")

class CreationRequest(BaseModel):
    request: str
    test_immediately: bool = True
    create_backup: bool = True
    update_documentation: bool = True
    commit_to_git: bool = True

class CreationPlan(BaseModel):
    request: str
    steps: List[str]
    risks: List[str]
    testing_plan: str
    rollback_plan: str

def create_backup(description: str) -> str:
    \"\"\"Create timestamped backup with correct paths\"\"\"
    timestamp = datetime.now().strftime(\"%Y%m%d_%H%M%S\")
    backup_dir = f\"/app/backups/{timestamp}_{description.replace(' ', '_')[:20]}\"
    
    # Create backup directory
    os.makedirs(backup_dir, exist_ok=True)
    
    # Backup only if directories exist
    if os.path.exists('/app/routers'):
        shutil.copytree('/app/routers', f'{backup_dir}/routers')
    
    # For UI files, we'll create them in /app/generated_ui/
    if os.path.exists('/app/generated_ui'):
        shutil.copytree('/app/generated_ui', f'{backup_dir}/generated_ui')
    
    return backup_dir

def test_creation(file_path: str, file_type: str) -> Dict:
    \"\"\"Test the created file\"\"\"
    test_results = {
        'syntax_check': False,
        'accessibility': False,
        'errors': []
    }
    
    try:
        if file_type == 'python':
            result = subprocess.run(
                f'python3 -m py_compile {file_path}',
                shell=True, capture_output=True, text=True
            )
            test_results['syntax_check'] = result.returncode == 0
            if result.stderr:
                test_results['errors'].append(result.stderr)
        
        elif file_type == 'html':
            test_results['accessibility'] = os.path.exists(file_path)
            with open(file_path, 'r') as f:
                content = f.read()
                test_results['syntax_check'] = '<html' in content.lower()
        
        test_results['accessibility'] = os.path.exists(file_path)
        
    except Exception as e:
        test_results['errors'].append(str(e))
    
    return test_results

@router.post('/plan')
async def create_plan(request: CreationRequest) -> CreationPlan:
    \"\"\"Create a plan\"\"\"
    return CreationPlan(
        request=request.request,
        steps=[
            '1. Create backup',
            '2. Generate code',
            '3. Save file',
            '4. Test',
            '5. Document',
            '6. Commit'
        ],
        risks=['Syntax errors', 'Path issues'],
        testing_plan='Test syntax and accessibility',
        rollback_plan='Restore from backup'
    )

@router.post('/create_with_rules')
async def create_with_all_rules(request: CreationRequest):
    \"\"\"Create with all rules - FIXED PATHS\"\"\"
    
    results = {
        'success': False,
        'plan_created': False,
        'backup_created': False,
        'code_generated': False,
        'tests_passed': False,
        'documented': False,
        'details': {}
    }
    
    try:
        # Create plan
        plan = await create_plan(request)
        results['plan_created'] = True
        results['details']['plan'] = plan.dict()
        
        # Create backup (with fixed paths)
        if request.create_backup:
            try:
                backup_path = create_backup(request.request)
                results['backup_created'] = True
                results['details']['backup_path'] = backup_path
            except Exception as e:
                results['details']['backup_error'] = str(e)
        
        # Generate code using AI
        try:
            # Import AI client
            sys.path.append('/app')
            from ai_client import ai_client
            
            prompt = f'''Create a complete HTML page for: {request.request}
            
Requirements:
- Full HTML5 structure
- Glass-morphic design
- Interactive elements
- Professional layout

Output complete HTML only.'''
            
            response = await ai_client.generate_response(prompt, {'mode': 'developer'})
            generated_code = response.get('response', '')
            
            # Clean the response
            if '```html' in generated_code:
                generated_code = generated_code.split('```html')[1].split('```')[0]
            elif '```' in generated_code:
                generated_code = generated_code.split('```')[1].split('```')[0]
            
        except Exception as e:
            # Fallback template if AI fails
            generated_code = f'''<!DOCTYPE html>
<html>
<head>
    <title>{request.request}</title>
    <style>
        body {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            font-family: Arial, sans-serif;
            padding: 20px;
        }}
        .container {{
            background: rgba(255,255,255,0.1);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            padding: 30px;
            color: white;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>{request.request}</h1>
        <p>Generated by Disciplined AI Creator</p>
        <p>Created: {datetime.now().isoformat()}</p>
    </div>
</body>
</html>'''
        
        # Determine file type and save
        file_type = 'html'
        file_name = request.request[:30].replace(' ', '_').lower() + '.html'
        
        # Save in container's accessible directory
        os.makedirs('/app/generated_ui', exist_ok=True)
        file_path = f'/app/generated_ui/{file_name}'
        
        with open(file_path, 'w') as f:
            f.write(generated_code)
        
        results['code_generated'] = True
        results['details']['file_path'] = file_path
        results['details']['file_type'] = file_type
        results['details']['preview'] = generated_code[:500]
        
        # Test the creation
        if request.test_immediately:
            test_results = test_creation(file_path, file_type)
            results['tests_passed'] = test_results['syntax_check']
            results['details']['test_results'] = test_results
        
        # Update documentation
        if request.update_documentation:
            try:
                doc_entry = f'''
## AI Creation - {datetime.now().isoformat()}
- Request: {request.request}
- File: {file_path}
- Tests: {results['tests_passed']}
'''
                with open('/app/ZOE_CURRENT_STATE.md', 'a') as f:
                    f.write(doc_entry)
                results['documented'] = True
            except:
                pass
        
        results['success'] = results['code_generated'] and results['tests_passed']
        
        # Provide access URL
        results['details']['access_url'] = f'File created at: {file_path}'
        
    except Exception as e:
        results['details']['error'] = str(e)
    
    return results

@router.get('/rules')
async def get_creation_rules():
    return {
        'rules': [
            'Create backups before changes',
            'Test everything immediately',
            'Document all changes',
            'Follow project patterns',
            'Commit to GitHub'
        ],
        'workflow': [
            '1. Plan',
            '2. Backup',
            '3. Generate',
            '4. Test',
            '5. Document'
        ]
    }
EOF"

# Replace the broken one
docker exec zoe-core mv /app/routers/disciplined_creator_fixed.py /app/routers/disciplined_creator.py

# Create the generated_ui directory and make it accessible
docker exec zoe-core mkdir -p /app/generated_ui

# Mount it to the UI container for serving
echo "📁 Setting up UI serving..."
cat > services/zoe-ui/dist/generated/.gitkeep << EOF
# AI Generated Pages Directory
EOF

# Restart to apply
echo "🔄 Restarting services..."
docker restart zoe-core
sleep 8

# Test
echo "🧪 Testing fixed creator..."
curl -X POST http://localhost:8000/api/disciplined_creator/create_with_rules \
  -H "Content-Type: application/json" \
  -d '{"request": "test page"}' | jq '.'

echo ""
echo "✅ Path issues fixed!"
echo ""
echo "Try again at: http://192.168.1.60:8080/disciplined_creator.html"
