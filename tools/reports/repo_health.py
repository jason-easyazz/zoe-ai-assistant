#!/usr/bin/env python3
"""
Repository Health Dashboard
Shows key metrics about project organization and health

Usage: python3 tools/reports/repo_health.py
"""

import subprocess
from pathlib import Path
from datetime import datetime
import os

# ANSI colors
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    CYAN = '\033[0;36m'
    NC = '\033[0m'

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

def get_repo_size():
    """Get total repository size"""
    result = subprocess.run(['du', '-sh', str(PROJECT_ROOT)], capture_output=True, text=True)
    return result.stdout.split()[0] if result.stdout else "Unknown"

def get_git_size():
    """Get .git directory size"""
    git_dir = PROJECT_ROOT / ".git"
    if git_dir.exists():
        result = subprocess.run(['du', '-sh', str(git_dir)], capture_output=True, text=True)
        return result.stdout.split()[0] if result.stdout else "Unknown"
    return "0"

def count_tracked_files():
    """Count files tracked by git"""
    result = subprocess.run(
        ['git', 'ls-files'],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True
    )
    return len(result.stdout.strip().split('\n')) if result.stdout.strip() else 0

def count_markdown_files():
    """Count markdown files in root and total"""
    root_md = len(list(PROJECT_ROOT.glob("*.md")))
    total_md = len(list(PROJECT_ROOT.rglob("*.md")))
    return root_md, total_md

def get_last_commit():
    """Get last commit info"""
    result = subprocess.run(
        ['git', 'log', '-1', '--format=%h - %s (%cr)'],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True
    )
    return result.stdout.strip() if result.stdout.strip() else "No commits"

def get_current_branch():
    """Get current git branch"""
    result = subprocess.run(
        ['git', 'branch', '--show-current'],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True
    )
    return result.stdout.strip() if result.stdout.strip() else "Unknown"

def check_structure_compliance():
    """Check if structure validation passes"""
    result = subprocess.run(
        ['python3', 'tools/audit/enforce_structure.py'],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True
    )
    return result.returncode == 0

def count_databases():
    """Count database files"""
    data_dir = PROJECT_ROOT / "data"
    if data_dir.exists():
        return len(list(data_dir.glob("*.db")))
    return 0

def get_recent_tags():
    """Get recent version tags"""
    result = subprocess.run(
        ['git', 'tag', '-l', '--sort=-creatordate'],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True
    )
    tags = result.stdout.strip().split('\n')[:3] if result.stdout.strip() else []
    return [t for t in tags if t]

def count_services():
    """Count services in services/ directory"""
    services_dir = PROJECT_ROOT / "services"
    if services_dir.exists():
        return len([d for d in services_dir.iterdir() if d.is_dir() and not d.name.startswith('.')])
    return 0

def main():
    print(f"{Colors.BLUE}{'='*60}{Colors.NC}")
    print(f"{Colors.BLUE}Zoe Repository Health Dashboard{Colors.NC}")
    print(f"{Colors.BLUE}Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{Colors.NC}")
    print(f"{Colors.BLUE}{'='*60}{Colors.NC}\n")
    
    # Repository basics
    print(f"{Colors.CYAN}📦 Repository Information{Colors.NC}")
    print(f"  Total size: {Colors.YELLOW}{get_repo_size()}{Colors.NC}")
    print(f"  .git size: {Colors.YELLOW}{get_git_size()}{Colors.NC}")
    print(f"  Tracked files: {Colors.GREEN}{count_tracked_files()}{Colors.NC}")
    print(f"  Current branch: {Colors.GREEN}{get_current_branch()}{Colors.NC}")
    
    # Documentation
    print(f"\n{Colors.CYAN}📚 Documentation{Colors.NC}")
    root_md, total_md = count_markdown_files()
    status = f"{Colors.GREEN}✓" if root_md <= 10 else f"{Colors.RED}✗"
    print(f"  Root .md files: {status} {root_md}/10{Colors.NC}")
    print(f"  Total .md files: {Colors.YELLOW}{total_md}{Colors.NC}")
    
    # Structure compliance
    print(f"\n{Colors.CYAN}🏗️  Structure Compliance{Colors.NC}")
    compliant = check_structure_compliance()
    if compliant:
        print(f"  Status: {Colors.GREEN}✓ PASSING{Colors.NC}")
    else:
        print(f"  Status: {Colors.RED}✗ VIOLATIONS FOUND{Colors.NC}")
        print(f"  Run: {Colors.YELLOW}python3 tools/audit/enforce_structure.py{Colors.NC}")
    
    # Databases
    print(f"\n{Colors.CYAN}💾 Databases{Colors.NC}")
    db_count = count_databases()
    print(f"  Database files: {Colors.GREEN}{db_count}{Colors.NC}")
    schema_dir = PROJECT_ROOT / "data" / "schema"
    if schema_dir.exists():
        schema_count = len(list(schema_dir.glob("*.sql")))
        print(f"  Schema files: {Colors.GREEN}{schema_count}{Colors.NC}")
    
    # Services
    print(f"\n{Colors.CYAN}🚀 Services{Colors.NC}")
    service_count = count_services()
    print(f"  Active services: {Colors.GREEN}{service_count}{Colors.NC}")
    
    # Git activity
    print(f"\n{Colors.CYAN}📝 Recent Activity{Colors.NC}")
    print(f"  Last commit: {Colors.YELLOW}{get_last_commit()}{Colors.NC}")
    
    recent_tags = get_recent_tags()
    if recent_tags:
        print(f"  Recent tags:")
        for tag in recent_tags:
            print(f"    • {Colors.GREEN}{tag}{Colors.NC}")
    
    # Recommendations
    print(f"\n{Colors.CYAN}💡 Recommendations{Colors.NC}")
    
    if root_md > 10:
        print(f"  {Colors.YELLOW}⚠{Colors.NC}  Archive {root_md - 10} docs from root to docs/archive/")
    
    if not compliant:
        print(f"  {Colors.RED}✗{Colors.NC}  Fix structure violations before committing")
    
    if not recent_tags:
        print(f"  {Colors.YELLOW}⚠{Colors.NC}  No version tags found. Consider tagging releases.")
    
    if root_md <= 10 and compliant:
        print(f"  {Colors.GREEN}✓{Colors.NC}  Project structure looks great!")
    
    print(f"\n{Colors.BLUE}{'='*60}{Colors.NC}")
    print(f"{Colors.GREEN}Health check complete!{Colors.NC}")
    print(f"{Colors.BLUE}{'='*60}{Colors.NC}\n")

if __name__ == "__main__":
    main()



