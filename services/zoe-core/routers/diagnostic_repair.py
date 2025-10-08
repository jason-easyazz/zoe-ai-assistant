"""Autonomous Diagnostic and Repair System"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
import subprocess
import os
import json
import asyncio
from datetime import datetime
import re
import sys
sys.path.append('/app')

router = APIRouter(prefix="/api/diagnostics")

class DiagnosticRequest(BaseModel):
    symptoms: Optional[str] = "Check everything"
    auto_fix: bool = False  # Require approval by default

class RepairApproval(BaseModel):
    issue_id: str
    approved: bool
    
class Issue:
    def __init__(self, severity: str, component: str, description: str, fix_command: str, safe: bool = False):
        self.id = f"{component}_{datetime.now().strftime('%H%M%S')}"
        self.severity = severity  # critical, warning, info
        self.component = component
        self.description = description
        self.fix_command = fix_command
        self.safe = safe  # Can be auto-executed without approval
        self.status = "detected"

# Store detected issues
detected_issues = {}

def run_diagnostic_command(cmd: str) -> str:
    """Safely run diagnostic command"""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, 
            text=True, timeout=10, cwd="/app"
        )
        return result.stdout + result.stderr
    except Exception as e:
        return f"Error running command: {e}"

def diagnose_docker_health() -> List[Issue]:
    """Diagnose Docker container health"""
    issues = []
    
    # Check container status
    output = run_diagnostic_command("docker ps -a --format '{{.Names}}:{{.Status}}'")
    
    for line in output.split('\n'):
        if not line or ':' not in line:
            continue
            
        name, status = line.split(':', 1)
        
        # Check for issues
        if 'Exited' in status:
            issues.append(Issue(
                "critical", 
                name,
                f"Container {name} has exited",
                f"docker restart {name}",
                safe=True
            ))
        elif 'Restarting' in status:
            issues.append(Issue(
                "warning",
                name,
                f"Container {name} is restarting frequently",
                f"docker stop {name} && docker start {name}",
                safe=True
            ))
        elif 'unhealthy' in status.lower():
            issues.append(Issue(
                "warning",
                name,
                f"Container {name} is unhealthy",
                f"docker restart {name}",
                safe=True
            ))
    
    return issues

def diagnose_disk_usage() -> List[Issue]:
    """Check disk space issues"""
    issues = []
    
    output = run_diagnostic_command("df -h /")
    lines = output.strip().split('\n')
    
    if len(lines) > 1:
        # Parse disk usage percentage
        parts = lines[1].split()
        if len(parts) >= 5:
            usage_str = parts[4].replace('%', '')
            try:
                usage = int(usage_str)
                if usage > 90:
                    issues.append(Issue(
                        "critical",
                        "disk",
                        f"Disk usage critical: {usage}%",
                        "docker system prune -af --volumes",
                        safe=False  # Requires approval
                    ))
                elif usage > 70:
                    issues.append(Issue(
                        "warning",
                        "disk",
                        f"Disk usage high: {usage}%",
                        "docker image prune -f",
                        safe=True
                    ))
            except:
                pass
    
    return issues

def diagnose_memory() -> List[Issue]:
    """Check memory issues"""
    issues = []
    
    output = run_diagnostic_command("free -m | grep Mem")
    if output:
        parts = output.split()
        if len(parts) >= 3:
            try:
                total = int(parts[1])
                used = int(parts[2])
                percent = (used / total) * 100
                
                if percent > 90:
                    issues.append(Issue(
                        "critical",
                        "memory",
                        f"Memory usage critical: {percent:.1f}%",
                        "docker restart $(docker ps -q)",
                        safe=False  # Requires approval
                    ))
                elif percent > 75:
                    issues.append(Issue(
                        "warning",
                        "memory",
                        f"Memory usage high: {percent:.1f}%",
                        "sync && echo 3 > /proc/sys/vm/drop_caches",
                        safe=True
                    ))
            except:
                pass
    
    return issues

def diagnose_logs() -> List[Issue]:
    """Check for errors in logs"""
    issues = []
    
    # Check each container's logs for errors
    containers = ["zoe-core", "zoe-ui", "zoe-ollama"]
    
    for container in containers:
        output = run_diagnostic_command(f"docker logs {container} --tail 50 2>&1 | grep -i error | wc -l")
        try:
            error_count = int(output.strip())
            if error_count > 10:
                issues.append(Issue(
                    "warning",
                    container,
                    f"Many errors in {container} logs ({error_count} in last 50 lines)",
                    f"docker logs {container} --tail 100 > /app/logs/{container}_errors.log && docker restart {container}",
                    safe=True
                ))
        except:
            pass
    
    return issues

def diagnose_api_health() -> List[Issue]:
    """Check API responsiveness"""
    issues = []
    
    # Test API endpoint
    output = run_diagnostic_command("curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/health")
    
    if output.strip() != "200":
        issues.append(Issue(
            "critical",
            "api",
            "API not responding correctly",
            "docker restart zoe-core",
            safe=True
        ))
    
    return issues

def diagnose_cpu_temperature() -> List[Issue]:
    """Check CPU temperature (Raspberry Pi)"""
    issues = []
    
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            temp = float(f.read().strip()) / 1000
            
            if temp > 80:
                issues.append(Issue(
                    "critical",
                    "cpu",
                    f"CPU temperature critical: {temp:.1f}°C",
                    "for i in $(docker ps -q); do docker pause $i; done; sleep 30; for i in $(docker ps -q); do docker unpause $i; done",
                    safe=False  # Requires approval
                ))
            elif temp > 70:
                issues.append(Issue(
                    "warning",
                    "cpu",
                    f"CPU temperature high: {temp:.1f}°C",
                    "echo 'Consider improving cooling'",
                    safe=True
                ))
    except:
        pass
    
    return issues

@router.post("/diagnose")
async def run_diagnostics(request: DiagnosticRequest):
    """Run complete system diagnostics"""
    
    global detected_issues
    detected_issues.clear()
    
    all_issues = []
    
    # Run all diagnostic checks
    diagnostic_functions = [
        ("Docker Health", diagnose_docker_health),
        ("Disk Usage", diagnose_disk_usage),
        ("Memory", diagnose_memory),
        ("Logs", diagnose_logs),
        ("API Health", diagnose_api_health),
        ("CPU Temperature", diagnose_cpu_temperature)
    ]
    
    diagnostics_report = {
        "timestamp": datetime.now().isoformat(),
        "symptoms": request.symptoms,
        "checks_performed": [],
        "issues_found": [],
        "auto_fixed": [],
        "requires_approval": []
    }
    
    # Run each diagnostic
    for check_name, check_func in diagnostic_functions:
        diagnostics_report["checks_performed"].append(check_name)
        try:
            issues = check_func()
            all_issues.extend(issues)
        except Exception as e:
            all_issues.append(Issue(
                "warning",
                check_name.lower().replace(" ", "_"),
                f"Could not complete {check_name} check: {e}",
                "",
                safe=True
            ))
    
    # Process issues
    for issue in all_issues:
        # Store for later approval
        detected_issues[issue.id] = issue
        
        issue_dict = {
            "id": issue.id,
            "severity": issue.severity,
            "component": issue.component,
            "description": issue.description,
            "fix_available": bool(issue.fix_command),
            "safe_to_auto_fix": issue.safe
        }
        
        diagnostics_report["issues_found"].append(issue_dict)
        
        # Auto-fix safe issues if requested
        if request.auto_fix and issue.safe and issue.fix_command:
            try:
                run_diagnostic_command(issue.fix_command)
                issue.status = "fixed"
                diagnostics_report["auto_fixed"].append(issue.id)
            except:
                issue.status = "fix_failed"
        
        # Mark dangerous fixes for approval
        elif not issue.safe and issue.fix_command:
            diagnostics_report["requires_approval"].append({
                "id": issue.id,
                "description": issue.description,
                "fix_command": issue.fix_command,
                "warning": "This operation requires approval"
            })
    
    # Generate summary
    critical_count = sum(1 for i in all_issues if i.severity == "critical")
    warning_count = sum(1 for i in all_issues if i.severity == "warning")
    
    diagnostics_report["summary"] = {
        "healthy": len(all_issues) == 0,
        "critical_issues": critical_count,
        "warnings": warning_count,
        "total_issues": len(all_issues),
        "auto_fixed_count": len(diagnostics_report["auto_fixed"]),
        "pending_approval_count": len(diagnostics_report["requires_approval"])
    }
    
    # Generate recommendations
    if critical_count > 0:
        diagnostics_report["recommendation"] = "🚨 CRITICAL issues detected. Immediate action required!"
    elif warning_count > 0:
        diagnostics_report["recommendation"] = "⚠️ Some warnings detected. Review and fix when possible."
    else:
        diagnostics_report["recommendation"] = "✅ System is healthy. No issues detected."
    
    return diagnostics_report

@router.post("/approve_fix")
async def approve_and_fix(approval: RepairApproval):
    """Approve and execute a specific fix"""
    
    global detected_issues
    
    if approval.issue_id not in detected_issues:
        raise HTTPException(status_code=404, detail="Issue not found")
    
    issue = detected_issues[approval.issue_id]
    
    if not approval.approved:
        issue.status = "declined"
        return {"message": "Fix declined", "issue_id": approval.issue_id}
    
    # Execute the fix
    try:
        output = run_diagnostic_command(issue.fix_command)
        issue.status = "fixed"
        
        # Verify fix worked
        await asyncio.sleep(2)  # Wait for fix to take effect
        
        # Re-run specific diagnostic
        verify_issues = []
        if "docker" in issue.component:
            verify_issues = diagnose_docker_health()
        elif "disk" in issue.component:
            verify_issues = diagnose_disk_usage()
        elif "memory" in issue.component:
            verify_issues = diagnose_memory()
        
        # Check if issue still exists
        still_exists = any(i.description == issue.description for i in verify_issues)
        
        return {
            "success": not still_exists,
            "issue_id": approval.issue_id,
            "output": output[:500],  # Truncate output
            "status": "fixed" if not still_exists else "fix_failed",
            "message": "Fix applied successfully!" if not still_exists else "Fix applied but issue persists"
        }
        
    except Exception as e:
        issue.status = "fix_failed"
        return {
            "success": False,
            "issue_id": approval.issue_id,
            "error": str(e),
            "status": "error"
        }

@router.post("/fix_all_safe")
async def fix_all_safe_issues():
    """Automatically fix all safe issues"""
    
    global detected_issues
    
    fixed = []
    failed = []
    
    for issue_id, issue in detected_issues.items():
        if issue.safe and issue.fix_command and issue.status == "detected":
            try:
                run_diagnostic_command(issue.fix_command)
                issue.status = "fixed"
                fixed.append(issue_id)
            except Exception as e:
                issue.status = "fix_failed"
                failed.append({"id": issue_id, "error": str(e)})
    
    return {
        "fixed_count": len(fixed),
        "failed_count": len(failed),
        "fixed_issues": fixed,
        "failed_issues": failed
    }

@router.get("/issues")
async def get_current_issues():
    """Get all currently detected issues"""
    
    global detected_issues
    
    return {
        "total": len(detected_issues),
        "issues": [
            {
                "id": issue.id,
                "severity": issue.severity,
                "component": issue.component,
                "description": issue.description,
                "status": issue.status,
                "safe": issue.safe
            }
            for issue in detected_issues.values()
        ]
    }

@router.post("/smart_fix")
async def smart_autonomous_fix(request: DiagnosticRequest):
    """The ultimate autonomous diagnostic and repair"""
    
    # Step 1: Diagnose
    diagnosis = await run_diagnostics(request)
    
    # Step 2: If healthy, we're done
    if diagnosis["summary"]["healthy"]:
        return {
            "status": "healthy",
            "message": "System is operating normally. No fixes needed.",
            "diagnosis": diagnosis
        }
    
    # Step 3: Auto-fix safe issues
    safe_fixes = await fix_all_safe_issues()
    
    # Step 4: Prepare response with remaining issues
    response = {
        "status": "issues_found",
        "auto_fixed": safe_fixes["fixed_count"],
        "requires_approval": [],
        "diagnosis": diagnosis
    }
    
    # Step 5: List dangerous fixes that need approval
    for issue_id, issue in detected_issues.items():
        if not issue.safe and issue.status == "detected":
            response["requires_approval"].append({
                "id": issue.id,
                "description": issue.description,
                "command": issue.fix_command,
                "approve_endpoint": f"/api/diagnostics/approve_fix",
                "approve_payload": {"issue_id": issue.id, "approved": true}
            })
    
    return response
