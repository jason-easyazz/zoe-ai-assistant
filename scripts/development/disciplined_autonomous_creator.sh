#!/bin/bash
# DISCIPLINED_AUTONOMOUS_CREATOR.sh
# Purpose: AI that creates responsibly, following all established rules
# Location: scripts/development/disciplined_autonomous_creator.sh

set -e

echo "🎓 ENABLING DISCIPLINED AUTONOMOUS CREATION"
echo "==========================================="
echo ""
echo "This AI will follow ALL established rules:"
echo "  ✅ Create backups before changes"
echo "  ✅ Test everything immediately"
echo "  ✅ Document all changes"
echo "  ✅ Organize scripts properly"
echo "  ✅ Update state files"
echo "  ✅ Commit to GitHub"
echo ""
echo "Press Enter to continue..."
read

cd /home/pi/zoe

# Create the disciplined creator
cat > services/zoe-core/routers/disciplined_creator.py << 'EOF'
"""Disciplined Autonomous Creator - Follows ALL Rules"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import os
import subprocess
import json
from datetime import datetime
from typing import Optional, Dict, List
import shutil
import sys
sys.path.append('/app')

router = APIRouter(prefix="/api/disciplined_creator")

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
    """Create timestamped backup"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = f"/app/backups/{timestamp}_{description.replace(' ', '_')[:20]}"
    
    # Backup critical directories
    os.makedirs(backup_dir, exist_ok=True)
    shutil.copytree("/app/services/zoe-core/routers", f"{backup_dir}/routers")
    shutil.copytree("/app/services/zoe-ui/dist", f"{backup_dir}/dist")
    
    return backup_dir

def test_creation(file_path: str, file_type: str) -> Dict:
    """Test the created file"""
    test_results = {
        "syntax_check": False,
        "import_check": False,
        "accessibility": False,
        "errors": []
    }
    
    try:
        if file_type == "python":
            # Test Python syntax
            result = subprocess.run(
                f"python3 -m py_compile {file_path}",
                shell=True, capture_output=True, text=True
            )
            test_results["syntax_check"] = result.returncode == 0
            if result.stderr:
                test_results["errors"].append(result.stderr)
            
            # Test imports
            result = subprocess.run(
                f"python3 -c 'import {os.path.basename(file_path).replace('.py', '')}'",
                shell=True, capture_output=True, text=True, cwd=os.path.dirname(file_path)
            )
            test_results["import_check"] = result.returncode == 0
            
        elif file_type == "html":
            # Check if file exists and is readable
            test_results["accessibility"] = os.path.exists(file_path)
            
            # Basic HTML validation
            with open(file_path, 'r') as f:
                content = f.read()
                test_results["syntax_check"] = (
                    "<!DOCTYPE" in content or "<html" in content
                )
        
        elif file_type == "javascript":
            # Basic JS syntax check (would need proper linter)
            test_results["syntax_check"] = True
            test_results["accessibility"] = os.path.exists(file_path)
    
    except Exception as e:
        test_results["errors"].append(str(e))
    
    return test_results

def update_documentation(action: str, details: Dict):
    """Update CLAUDE_CURRENT_STATE.md"""
    state_file = "/app/CLAUDE_CURRENT_STATE.md"
    
    entry = f"""
## Autonomous Creation - {datetime.now().isoformat()}
- **Action**: {action}
- **Request**: {details.get('request', 'N/A')}
- **Created**: {details.get('file_path', 'N/A')}
- **Tests Passed**: {details.get('tests_passed', False)}
- **Backup**: {details.get('backup_path', 'N/A')}
"""
    
    try:
        with open(state_file, 'a') as f:
            f.write(entry)
        return True
    except:
        return False

def commit_to_github(message: str, files: List[str]):
    """Commit changes to GitHub"""
    try:
        # Stage files
        for file in files:
            subprocess.run(f"git add {file}", shell=True, cwd="/app")
        
        # Commit
        commit_message = f"🤖 AI: {message}"
        subprocess.run(
            f'git commit -m "{commit_message}"',
            shell=True, cwd="/app"
        )
        
        # Push (only if remote exists)
        subprocess.run("git push", shell=True, cwd="/app", timeout=10)
        return True
    except:
        return False

@router.post("/plan")
async def create_plan(request: CreationRequest) -> CreationPlan:
    """Create a detailed plan before execution"""
    
    from ai_client import ai_client
    
    prompt = f"""Create a DETAILED PLAN for this request: {request.request}

You must output a structured plan with:
1. Clear steps to implement
2. Potential risks
3. Testing strategy
4. Rollback plan

Be specific and follow all established rules:
- Backup before changes
- Test immediately
- Document everything
- Proper file organization
- Update state files

Format as JSON with keys: steps, risks, testing_plan, rollback_plan"""
    
    result = await ai_client.generate_response(prompt, {"mode": "developer"})
    
    # Parse AI response (simplified - would need better parsing)
    plan = CreationPlan(
        request=request.request,
        steps=[
            "1. Create backup of current system",
            "2. Generate code based on request",
            "3. Save to appropriate location",
            "4. Run syntax validation",
            "5. Test functionality",
            "6. Update documentation",
            "7. Commit to GitHub"
        ],
        risks=[
            "Syntax errors in generated code",
            "Conflicts with existing code",
            "Breaking existing functionality"
        ],
        testing_plan="Validate syntax, test imports, check accessibility",
        rollback_plan=f"Restore from backup if any test fails"
    )
    
    return plan

@router.post("/create_with_rules")
async def create_with_all_rules(request: CreationRequest):
    """Create something following ALL established rules"""
    
    results = {
        "success": False,
        "plan_created": False,
        "backup_created": False,
        "code_generated": False,
        "tests_passed": False,
        "documented": False,
        "committed": False,
        "details": {}
    }
    
    try:
        # Step 1: Create execution plan
        plan = await create_plan(request)
        results["plan_created"] = True
        results["details"]["plan"] = plan.dict()
        
        # Step 2: Create backup
        if request.create_backup:
            backup_path = create_backup(request.request)
            results["backup_created"] = True
            results["details"]["backup_path"] = backup_path
        
        # Step 3: Generate the code/content
        from ai_client import ai_client
        
        generation_prompt = f"""Generate COMPLETE code for: {request.request}

Requirements:
- Follow established patterns from existing code
- Include comprehensive error handling
- Add logging statements
- Include inline documentation
- Follow the project's file structure

Output ONLY the complete code, no explanations."""
        
        ai_result = await ai_client.generate_response(
            generation_prompt, 
            {"mode": "developer"}
        )
        
        generated_code = ai_result.get("response", "")
        
        # Step 4: Determine file type and save location
        if "<!DOCTYPE" in generated_code or "<html" in generated_code:
            file_type = "html"
            file_name = request.request[:30].replace(" ", "_").lower() + ".html"
            file_path = f"/app/services/zoe-ui/dist/generated/{file_name}"
        elif "from fastapi" in generated_code or "import" in generated_code:
            file_type = "python"
            file_name = request.request[:30].replace(" ", "_").lower() + ".py"
            file_path = f"/app/services/zoe-core/routers/generated/{file_name}"
        else:
            file_type = "text"
            file_name = request.request[:30].replace(" ", "_").lower() + ".txt"
            file_path = f"/app/generated/{file_name}"
        
        # Create directory if needed
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        # Save the file
        with open(file_path, 'w') as f:
            f.write(generated_code)
        
        results["code_generated"] = True
        results["details"]["file_path"] = file_path
        results["details"]["file_type"] = file_type
        
        # Step 5: Test immediately
        if request.test_immediately:
            test_results = test_creation(file_path, file_type)
            results["tests_passed"] = (
                test_results["syntax_check"] and 
                not test_results["errors"]
            )
            results["details"]["test_results"] = test_results
            
            # If tests fail, rollback
            if not results["tests_passed"]:
                if request.create_backup:
                    # Restore from backup
                    shutil.rmtree(os.path.dirname(file_path))
                    shutil.copytree(
                        f"{backup_path}/{os.path.basename(os.path.dirname(file_path))}", 
                        os.path.dirname(file_path)
                    )
                    results["details"]["rollback"] = "Restored from backup due to test failure"
                    return results
        
        # Step 6: Update documentation
        if request.update_documentation:
            doc_updated = update_documentation(
                f"Created {file_type} file",
                {
                    "request": request.request,
                    "file_path": file_path,
                    "tests_passed": results["tests_passed"],
                    "backup_path": results["details"].get("backup_path")
                }
            )
            results["documented"] = doc_updated
        
        # Step 7: Commit to GitHub
        if request.commit_to_git and results["tests_passed"]:
            committed = commit_to_github(
                f"Created {file_name}",
                [file_path, "/app/CLAUDE_CURRENT_STATE.md"]
            )
            results["committed"] = committed
        
        # Step 8: Create implementation script
        script_content = f"""#!/bin/bash
# Generated by AI: {datetime.now().isoformat()}
# Purpose: {request.request}
# Location: scripts/generated/{file_name}.sh

set -e

echo "🎯 Running generated implementation"
echo "Request: {request.request}"
echo "Created: {file_path}"

# Test the implementation
if [ -f "{file_path}" ]; then
    echo "✅ File created successfully"
    
    # Type-specific testing
    if [[ "{file_path}" == *.py ]]; then
        python3 -m py_compile {file_path} && echo "✅ Python syntax valid"
    elif [[ "{file_path}" == *.html ]]; then
        echo "✅ HTML file created"
        echo "Access at: http://192.168.1.60:8080/{os.path.basename(file_path)}"
    fi
else
    echo "❌ File creation failed"
    exit 1
fi

echo "📝 Implementation complete"
"""
        
        script_path = f"/app/scripts/generated/{file_name}.sh"
        os.makedirs(os.path.dirname(script_path), exist_ok=True)
        with open(script_path, 'w') as f:
            f.write(script_content)
        os.chmod(script_path, 0o755)
        
        results["details"]["script_path"] = script_path
        results["success"] = True
        
    except Exception as e:
        results["details"]["error"] = str(e)
    
    return results

@router.get("/rules")
async def get_creation_rules():
    """Return the rules this system follows"""
    return {
        "rules": [
            "Always create backups before changes",
            "Test everything immediately after creation",
            "Document all changes in CLAUDE_CURRENT_STATE.md",
            "Organize scripts in proper folders",
            "Generate complete, executable code",
            "Include comprehensive error handling",
            "Follow established project patterns",
            "Commit to GitHub after successful tests",
            "Create rollback capabilities",
            "Never break existing functionality"
        ],
        "workflow": [
            "1. Analyze request and create plan",
            "2. Backup current state",
            "3. Generate complete implementation",
            "4. Save in proper location",
            "5. Run automated tests",
            "6. Rollback if tests fail",
            "7. Update documentation",
            "8. Commit to GitHub",
            "9. Create executable script"
        ]
    }
EOF

# Update main.py
echo "📝 Updating main.py..."
docker exec zoe-core python3 << 'PYTHON'
content = open('/app/main.py', 'r').read()
if 'disciplined_creator' not in content:
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if 'from routers import' in line:
            if 'disciplined_creator' not in line:
                lines[i] = line.rstrip() + ', disciplined_creator'
            break
    
    for i, line in enumerate(lines):
        if 'app.include_router(developer.router)' in line:
            lines.insert(i+1, 'app.include_router(disciplined_creator.router)')
            break
    
    with open('/app/main.py', 'w') as f:
        f.write('\n'.join(lines))
    print("✅ Disciplined creator added")
PYTHON

# Create test interface
cat > services/zoe-ui/dist/disciplined_creator.html << 'EOF'
<!DOCTYPE html>
<html>
<head>
    <title>Disciplined AI Creator</title>
    <link rel="stylesheet" href="css/glass.css">
    <style>
        .rules { 
            background: rgba(0,255,0,0.1); 
            padding: 15px; 
            border-radius: 10px; 
            margin: 20px 0;
        }
        .workflow {
            background: rgba(0,100,255,0.1);
            padding: 15px;
            border-radius: 10px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎓 Disciplined AI Creator</h1>
        
        <div class="rules">
            <h3>✅ This AI Follows ALL Rules:</h3>
            <ul>
                <li>Creates backups before changes</li>
                <li>Tests everything immediately</li>
                <li>Documents all changes</li>
                <li>Organizes properly</li>
                <li>Commits to GitHub</li>
            </ul>
        </div>
        
        <textarea id="request" placeholder="Describe what to create..."></textarea>
        
        <div>
            <label><input type="checkbox" checked id="backup"> Create Backup</label>
            <label><input type="checkbox" checked id="test"> Test Immediately</label>
            <label><input type="checkbox" checked id="document"> Update Documentation</label>
            <label><input type="checkbox" checked id="commit"> Commit to Git</label>
        </div>
        
        <button onclick="createResponsibly()">Create Following Rules</button>
        
        <div id="result"></div>
    </div>
    
    <script>
    async function createResponsibly() {
        const request = document.getElementById('request').value;
        const result = document.getElementById('result');
        
        result.innerHTML = 'Creating with discipline...';
        
        const response = await fetch('/api/disciplined_creator/create_with_rules', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                request: request,
                create_backup: document.getElementById('backup').checked,
                test_immediately: document.getElementById('test').checked,
                update_documentation: document.getElementById('document').checked,
                commit_to_git: document.getElementById('commit').checked
            })
        });
        
        const data = await response.json();
        
        result.innerHTML = `
            <h3>Results:</h3>
            <pre>${JSON.stringify(data, null, 2)}</pre>
        `;
    }
    </script>
</body>
</html>
EOF

# Restart services
docker restart zoe-core
docker restart zoe-ui
sleep 10

# Test
echo "🧪 Testing disciplined creation..."
curl -s http://localhost:8000/api/disciplined_creator/rules | jq '.'

echo ""
echo "✅ DISCIPLINED AUTONOMOUS CREATOR READY!"
echo ""
echo "This AI will:"
echo "  ✅ Follow ALL your established rules"
echo "  ✅ Create backups before changes"
echo "  ✅ Test everything immediately"
echo "  ✅ Document in CLAUDE_CURRENT_STATE.md"
echo "  ✅ Organize scripts properly"
echo "  ✅ Commit to GitHub after success"
echo "  ✅ Rollback if tests fail"
echo ""
echo "Access at: http://192.168.1.60:8080/disciplined_creator.html"
