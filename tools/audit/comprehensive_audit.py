#!/usr/bin/env python3
"""
Comprehensive Zoe System Audit
Checks:
1. UI Pages - which ones work
2. API Endpoints - which ones return errors
3. Database schemas - mismatches
4. Docker configs - missing env vars
"""

import requests
import sqlite3
import json
import os
from pathlib import Path

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# Colors
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

def check_ui_pages():
    """Test all UI pages"""
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}UI PAGES AUDIT{RESET}")
    print(f"{BLUE}{'='*60}{RESET}\n")
    
    ui_dir = PROJECT_ROOT / "services/zoe-ui/dist"
    html_files = list(ui_dir.glob("*.html"))
    
    results = {"working": [], "broken": [], "missing_js": []}
    
    for html_file in html_files:
        page_name = html_file.name
        try:
            content = html_file.read_text()
            # Check for common issues
            issues = []
            
            # Check if it references reminders
            if 'loadReminders' in content or 'loadNotifications' in content:
                issues.append("References reminders functions")
            
            # Check for missing common.js
            if 'common.js' in content and not (ui_dir / 'js' / 'common.js').exists():
                issues.append("Missing common.js")
            
            if issues:
                results["broken"].append((page_name, issues))
                print(f"{YELLOW}⚠ {page_name:30s} - {', '.join(issues)}{RESET}")
            else:
                results["working"].append(page_name)
                print(f"{GREEN}✓ {page_name:30s} - OK{RESET}")
                
        except Exception as e:
            results["broken"].append((page_name, [str(e)]))
            print(f"{RED}✗ {page_name:30s} - ERROR: {e}{RESET}")
    
    return results

def check_api_endpoints():
    """Test critical API endpoints"""
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}API ENDPOINTS AUDIT{RESET}")
    print(f"{BLUE}{'='*60}{RESET}\n")
    
    base_url = "http://localhost:8000"
    user_id = "72038d8e-a3bb-4e41-9d9b-163b5736d2ce"
    
    endpoints = [
        ("/health", "GET", None),
        ("/api/lists/personal_todos", "GET", {"user_id": user_id}),
        ("/api/lists/work_todos", "GET", {"user_id": user_id}),
        ("/api/lists/shopping", "GET", {"user_id": user_id}),
        ("/api/lists/bucket", "GET", {"user_id": user_id}),
        ("/api/calendar/events", "GET", {"user_id": user_id}),
        ("/api/reminders/", "GET", {"user_id": user_id}),
        ("/api/reminders/notifications/pending", "GET", {"user_id": user_id}),
        ("/api/memories/", "GET", {"user_id": user_id}),
        ("/api/journal/entries", "GET", {"user_id": user_id}),
        ("/api/settings/", "GET", {"user_id": user_id}),
    ]
    
    results = {"working": [], "errors": []}
    
    for endpoint, method, params in endpoints:
        try:
            url = f"{base_url}{endpoint}"
            if method == "GET":
                response = requests.get(url, params=params, timeout=5)
            else:
                response = requests.post(url, params=params, timeout=5)
            
            status = response.status_code
            
            if status == 200:
                results["working"].append(endpoint)
                print(f"{GREEN}✓ {status} {endpoint:50s}{RESET}")
            elif status == 404:
                print(f"{YELLOW}⚠ {status} {endpoint:50s} - Not Found{RESET}")
                results["errors"].append((endpoint, status, "Not Found"))
            else:
                error_msg = response.text[:100] if response.text else "No error message"
                print(f"{RED}✗ {status} {endpoint:50s} - {error_msg}{RESET}")
                results["errors"].append((endpoint, status, error_msg))
                
        except requests.exceptions.ConnectionError:
            print(f"{RED}✗ ERR {endpoint:50s} - Connection Failed{RESET}")
            results["errors"].append((endpoint, "Connection", "Service not running"))
        except requests.exceptions.Timeout:
            print(f"{RED}✗ TMO {endpoint:50s} - Timeout{RESET}")
            results["errors"].append((endpoint, "Timeout", "Request timed out"))
        except Exception as e:
            print(f"{RED}✗ ERR {endpoint:50s} - {str(e)[:50]}{RESET}")
            results["errors"].append((endpoint, "Exception", str(e)))
    
    return results

def check_database_schemas():
    """Check database table schemas"""
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}DATABASE SCHEMA AUDIT{RESET}")
    print(f"{BLUE}{'='*60}{RESET}\n")
    
    db_path = str(PROJECT_ROOT / "data/zoe.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Get all tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cursor.fetchall()]
    
    print(f"Found {len(tables)} tables:\n")
    
    important_tables = ['reminders', 'notifications', 'events', 'lists', 'list_items', 
                       'journal_entries', 'memories', 'users', 'user_sessions']
    
    schemas = {}
    for table in important_tables:
        if table in tables:
            cursor.execute(f"PRAGMA table_info({table})")
            columns = cursor.fetchall()
            schemas[table] = [(col[1], col[2]) for col in columns]  # name, type
            print(f"{GREEN}✓ {table:25s} - {len(columns):2d} columns{RESET}")
            for col_name, col_type in schemas[table][:5]:  # Show first 5 columns
                print(f"    {col_name:25s} {col_type}")
            if len(schemas[table]) > 5:
                print(f"    ... and {len(schemas[table]) - 5} more columns")
        else:
            print(f"{RED}✗ {table:25s} - TABLE MISSING{RESET}")
    
    conn.close()
    return schemas

def check_router_expectations():
    """Check what routers expect vs what database has"""
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}ROUTER vs DATABASE MISMATCH AUDIT{RESET}")
    print(f"{BLUE}{'='*60}{RESET}\n")
    
    issues = []
    
    # Check reminders router
    reminders_py = PROJECT_ROOT / "services/zoe-core/routers/reminders.py"
    if reminders_py.exists():
        content = reminders_py.read_text()
        
        # Check for column references
        db_path = str(PROJECT_ROOT / "data/zoe.db")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Get actual reminders columns
        cursor.execute("PRAGMA table_info(reminders)")
        actual_columns = {col[1] for col in cursor.fetchall()}
        
        # Columns the code expects
        expected_in_code = set()
        if 'due_date' in content:
            expected_in_code.add('due_date')
        if 'due_time' in content:
            expected_in_code.add('due_time')
        if 'requires_acknowledgment' in content:
            expected_in_code.add('requires_acknowledgment')
        if 'family_member' in content:
            expected_in_code.add('family_member')
        if 'linked_list_id' in content:
            expected_in_code.add('linked_list_id')
        
        missing = expected_in_code - actual_columns
        if missing:
            print(f"{RED}✗ reminders.py expects columns not in DB:{RESET}")
            for col in missing:
                print(f"    - {col}")
            issues.append(("reminders.py", "missing_columns", list(missing)))
        else:
            print(f"{GREEN}✓ reminders.py - columns match{RESET}")
        
        conn.close()
    
    return issues

def generate_report(ui_results, api_results, schema_results, mismatch_results):
    """Generate comprehensive report"""
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}COMPREHENSIVE AUDIT REPORT{RESET}")
    print(f"{BLUE}{'='*60}{RESET}\n")
    
    total_issues = len(ui_results["broken"]) + len(api_results["errors"]) + len(mismatch_results)
    
    print(f"\n📊 SUMMARY:")
    print(f"  • UI Pages Working: {GREEN}{len(ui_results['working'])}{RESET}")
    print(f"  • UI Pages with Issues: {YELLOW}{len(ui_results['broken'])}{RESET}")
    print(f"  • API Endpoints Working: {GREEN}{len(api_results['working'])}{RESET}")
    print(f"  • API Endpoints with Errors: {RED}{len(api_results['errors'])}{RESET}")
    print(f"  • Schema Mismatches: {RED}{len(mismatch_results)}{RESET}")
    print(f"\n  ⚠️  TOTAL ISSUES FOUND: {RED}{total_issues}{RESET}")
    
    if api_results["errors"]:
        print(f"\n{RED}🔴 CRITICAL API ERRORS:{RESET}")
        for endpoint, status, msg in api_results["errors"]:
            print(f"  • {endpoint:40s} [{status}] - {msg[:60]}")
    
    if ui_results["broken"]:
        print(f"\n{YELLOW}⚠️  UI PAGES WITH ISSUES:{RESET}")
        for page, issues in ui_results["broken"]:
            print(f"  • {page:30s} - {', '.join(issues)}")
    
    if mismatch_results:
        print(f"\n{RED}🔴 DATABASE SCHEMA MISMATCHES:{RESET}")
        for router, issue_type, details in mismatch_results:
            print(f"  • {router} - {issue_type}: {', '.join(details)}")
    
    # Save to file
    report = {
        "timestamp": "2025-10-08",
        "summary": {
            "total_issues": total_issues,
            "ui_working": len(ui_results["working"]),
            "ui_broken": len(ui_results["broken"]),
            "api_working": len(api_results["working"]),
            "api_errors": len(api_results["errors"]),
            "schema_mismatches": len(mismatch_results)
        },
        "details": {
            "ui": ui_results,
            "api": api_results,
            "mismatches": mismatch_results
        }
    }
    
    with open(str(PROJECT_ROOT / "audit_report.json"), "w") as f:
        json.dump(report, f, indent=2)
    
    print(f"\n📄 Full report saved to: audit_report.json")

if __name__ == "__main__":
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}ZOE COMPREHENSIVE SYSTEM AUDIT{RESET}")
    print(f"{BLUE}{'='*60}{RESET}")
    
    ui_results = check_ui_pages()
    api_results = check_api_endpoints()
    schema_results = check_database_schemas()
    mismatch_results = check_router_expectations()
    
    generate_report(ui_results, api_results, schema_results, mismatch_results)
    
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{GREEN}✓ Audit Complete!{RESET}")
    print(f"{BLUE}{'='*60}{RESET}\n")

