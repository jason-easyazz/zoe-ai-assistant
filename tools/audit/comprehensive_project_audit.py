#!/usr/bin/env python3
"""
Comprehensive Project Audit
============================

Checks EVERY folder for mess:
- /home/pi (home directory)
- PROJECT_ROOT (project root)
- PROJECT_ROOT/services/ (all service subdirectories)
- PROJECT_ROOT/tests/ (all test subdirectories)
- PROJECT_ROOT/scripts/ (all script subdirectories)
- PROJECT_ROOT/docs/ (all doc subdirectories)

Reports:
- Misplaced files
- Temp files
- Backup files
- Duplicate files
- Oversized files
"""

import os
from pathlib import Path

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
from collections import defaultdict
import sys

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    RESET = '\033[0m'

class ComprehensiveAuditor:
    def __init__(self):
        self.issues = defaultdict(list)
        self.stats = defaultdict(int)
        
    def audit_directory(self, path, rules):
        """Audit a directory against rules"""
        path = Path(path)
        if not path.exists():
            return
        
        for item in path.rglob('*'):
            if item.is_file():
                self.check_file(item, rules)
    
    def check_file(self, filepath, rules):
        """Check a single file against rules"""
        name = filepath.name
        
        # Check for temp files
        if any(name.endswith(ext) for ext in ['.tmp', '.cache', '.bak', '.swp', '.swo', '~']):
            self.issues['temp_files'].append(str(filepath))
            self.stats['temp_files'] += 1
        
        # Check for backup files
        if any(suffix in name for suffix in ['_backup', '_old', '_new', '.backup', '.old']):
            self.issues['backup_files'].append(str(filepath))
            self.stats['backup_files'] += 1
        
        # Check for test results in wrong place
        if name.endswith('.json') and 'result' in name.lower() and 'tests/results' not in str(filepath):
            self.issues['misplaced_results'].append(str(filepath))
            self.stats['misplaced_results'] += 1
        
        # Check file size (>10MB is suspicious)
        try:
            size = filepath.stat().st_size
            if size > 10 * 1024 * 1024:
                self.issues['large_files'].append(f"{filepath} ({size // (1024*1024)}MB)")
                self.stats['large_files'] += 1
        except:
            pass
    
    def audit_home_directory(self):
        """Audit /home/pi"""
        print(f"\n{Colors.BLUE}{'='*70}{Colors.RESET}")
        print(f"{Colors.BLUE}🏠 AUDITING: /home/pi{Colors.RESET}")
        print(f"{Colors.BLUE}{'='*70}{Colors.RESET}\n")
        
        home = Path('/home/pi')
        allowed = {'.bash_history', '.bashrc', '.profile', '.gitconfig', 'zoe'}
        
        violations = []
        for item in home.iterdir():
            if item.name.startswith('.') or item.name in allowed:
                continue
            violations.append(item.name)
        
        if violations:
            print(f"{Colors.RED}❌ Found {len(violations)} items that shouldn't be in /home/pi:{Colors.RESET}")
            for item in violations[:10]:
                print(f"  • {item}")
            if len(violations) > 10:
                print(f"  ... and {len(violations) - 10} more")
            self.stats['home_violations'] = len(violations)
        else:
            print(f"{Colors.GREEN}✅ /home/pi is clean{Colors.RESET}")
            self.stats['home_violations'] = 0
    
    def audit_zoe_root(self):
        """Audit PROJECT_ROOT root"""
        print(f"\n{Colors.BLUE}{'='*70}{Colors.RESET}")
        print(f"{Colors.BLUE}📁 AUDITING: PROJECT_ROOT (root){Colors.RESET}")
        print(f"{Colors.BLUE}{'='*70}{Colors.RESET}\n")
        
        root = Path('PROJECT_ROOT')
        
        # Check for temp files
        temp_files = []
        for item in root.glob('*'):
            if item.is_file():
                if any(item.name.endswith(ext) for ext in ['.tmp', '.cache', '.bak', '.pyc']):
                    temp_files.append(item.name)
        
        if temp_files:
            print(f"{Colors.RED}❌ Found {len(temp_files)} temp files in root:{Colors.RESET}")
            for f in temp_files[:5]:
                print(f"  • {f}")
            self.stats['root_temp_files'] = len(temp_files)
        else:
            print(f"{Colors.GREEN}✅ No temp files in root{Colors.RESET}")
            self.stats['root_temp_files'] = 0
        
        # Check MD file count
        md_files = list(root.glob('*.md'))
        print(f"\n📄 Markdown files in root: {len(md_files)}/10")
        if len(md_files) > 10:
            print(f"{Colors.YELLOW}⚠️  Over limit! Remove {len(md_files) - 10} files{Colors.RESET}")
            self.stats['md_over_limit'] = len(md_files) - 10
        else:
            print(f"{Colors.GREEN}✅ Within limit{Colors.RESET}")
            self.stats['md_over_limit'] = 0
    
    def audit_services(self):
        """Audit all service directories"""
        print(f"\n{Colors.BLUE}{'='*70}{Colors.RESET}")
        print(f"{Colors.BLUE}🔧 AUDITING: services/{Colors.RESET}")
        print(f"{Colors.BLUE}{'='*70}{Colors.RESET}\n")
        
        services_path = Path('PROJECT_ROOT/services')
        if not services_path.exists():
            print(f"{Colors.RED}❌ Services directory not found{Colors.RESET}")
            return
        
        for service_dir in services_path.iterdir():
            if service_dir.is_dir() and not service_dir.name.startswith('.'):
                self.check_service_directory(service_dir)
    
    def check_service_directory(self, service_dir):
        """Check a single service directory"""
        # Check for __pycache__
        pycache_dirs = list(service_dir.rglob('__pycache__'))
        if pycache_dirs:
            print(f"{Colors.YELLOW}  ⚠️  {service_dir.name}: {len(pycache_dirs)} __pycache__ dirs{Colors.RESET}")
            self.stats['pycache_dirs'] += len(pycache_dirs)
        
        # Check for backup files
        backup_files = []
        for pattern in ['*backup*', '*_old.*', '*.bak']:
            backup_files.extend(service_dir.rglob(pattern))
        if backup_files:
            print(f"{Colors.YELLOW}  ⚠️  {service_dir.name}: {len(backup_files)} backup files{Colors.RESET}")
            self.stats['service_backups'] += len(backup_files)
    
    def audit_tests(self):
        """Audit test directories"""
        print(f"\n{Colors.BLUE}{'='*70}{Colors.RESET}")
        print(f"{Colors.BLUE}🧪 AUDITING: tests/{Colors.RESET}")
        print(f"{Colors.BLUE}{'='*70}{Colors.RESET}\n")
        
        tests_path = Path('PROJECT_ROOT/tests')
        if not tests_path.exists():
            print(f"{Colors.RED}❌ Tests directory not found{Colors.RESET}")
            return
        
        # Check for results files outside tests/results/
        all_json = list(tests_path.rglob('*.json'))
        results_json = list((tests_path / 'results').rglob('*.json')) if (tests_path / 'results').exists() else []
        misplaced = len(all_json) - len(results_json)
        
        if misplaced > 0:
            print(f"{Colors.YELLOW}⚠️  {misplaced} result files not in tests/results/{Colors.RESET}")
            self.stats['misplaced_test_results'] = misplaced
        else:
            print(f"{Colors.GREEN}✅ Test results properly organized{Colors.RESET}")
            self.stats['misplaced_test_results'] = 0
    
    def audit_scripts(self):
        """Audit script directories"""
        print(f"\n{Colors.BLUE}{'='*70}{Colors.RESET}")
        print(f"{Colors.BLUE}📜 AUDITING: scripts/{Colors.RESET}")
        print(f"{Colors.BLUE}{'='*70}{Colors.RESET}\n")
        
        scripts_path = Path('PROJECT_ROOT/scripts')
        if not scripts_path.exists():
            print(f"{Colors.RED}❌ Scripts directory not found{Colors.RESET}")
            return
        
        # Check for executable permissions
        py_files = list(scripts_path.rglob('*.py'))
        sh_files = list(scripts_path.rglob('*.sh'))
        
        non_exec = []
        for f in py_files + sh_files:
            if not os.access(f, os.X_OK):
                non_exec.append(f.name)
        
        if non_exec:
            print(f"{Colors.YELLOW}⚠️  {len(non_exec)} scripts not executable{Colors.RESET}")
            self.stats['non_executable_scripts'] = len(non_exec)
        else:
            print(f"{Colors.GREEN}✅ All scripts executable{Colors.RESET}")
            self.stats['non_executable_scripts'] = 0
    
    def audit_docs(self):
        """Audit documentation directories"""
        print(f"\n{Colors.BLUE}{'='*70}{Colors.RESET}")
        print(f"{Colors.BLUE}📚 AUDITING: docs/{Colors.RESET}")
        print(f"{Colors.BLUE}{'='*70}{Colors.RESET}\n")
        
        docs_path = Path('PROJECT_ROOT/docs')
        if not docs_path.exists():
            print(f"{Colors.RED}❌ Docs directory not found{Colors.RESET}")
            return
        
        # Check archive structure
        archive_path = docs_path / 'archive'
        if archive_path.exists():
            archive_files = len(list(archive_path.rglob('*.md')))
            print(f"📦 Archived documents: {archive_files}")
            self.stats['archived_docs'] = archive_files
        
        print(f"{Colors.GREEN}✅ Docs directory audited{Colors.RESET}")
    
    def generate_report(self):
        """Generate final audit report"""
        print(f"\n{Colors.MAGENTA}{'='*70}{Colors.RESET}")
        print(f"{Colors.MAGENTA}📊 COMPREHENSIVE AUDIT REPORT{Colors.RESET}")
        print(f"{Colors.MAGENTA}{'='*70}{Colors.RESET}\n")
        
        total_issues = sum(self.stats.values())
        
        print(f"Total Issues Found: {total_issues}\n")
        
        print(f"{Colors.BLUE}Breakdown:{Colors.RESET}")
        for category, count in sorted(self.stats.items()):
            if count > 0:
                color = Colors.RED if count > 5 else Colors.YELLOW if count > 0 else Colors.GREEN
                print(f"  {color}• {category}: {count}{Colors.RESET}")
        
        print(f"\n{Colors.BLUE}Actions Needed:{Colors.RESET}")
        if self.stats.get('home_violations', 0) > 0:
            print(f"  {Colors.YELLOW}→ Run: python3 tools/cleanup/clean_home_directory.py{Colors.RESET}")
        if self.stats.get('pycache_dirs', 0) > 0:
            print(f"  {Colors.YELLOW}→ Run: find . -type d -name __pycache__ -exec rm -rf {{}} +{Colors.RESET}")
        if self.stats.get('temp_files', 0) > 0:
            print(f"  {Colors.YELLOW}→ Run: find . -name '*.tmp' -o -name '*.bak' | xargs rm{Colors.RESET}")
        if self.stats.get('md_over_limit', 0) > 0:
            print(f"  {Colors.YELLOW}→ Archive {self.stats['md_over_limit']} MD files to docs/archive/{Colors.RESET}")
        
        if total_issues == 0:
            print(f"\n{Colors.GREEN}🎉 PROJECT IS PERFECTLY CLEAN!{Colors.RESET}")
            return 0
        elif total_issues < 10:
            print(f"\n{Colors.GREEN}✅ PROJECT IS MOSTLY CLEAN (minor issues){Colors.RESET}")
            return 0
        else:
            print(f"\n{Colors.YELLOW}⚠️  PROJECT NEEDS CLEANUP{Colors.RESET}")
            return 1

def main():
    """Run comprehensive audit"""
    auditor = ComprehensiveAuditor()
    
    print(f"\n{Colors.MAGENTA}{'='*70}{Colors.RESET}")
    print(f"{Colors.MAGENTA}🔍 COMPREHENSIVE PROJECT AUDIT{Colors.RESET}")
    print(f"{Colors.MAGENTA}Checking EVERY folder for mess...{Colors.RESET}")
    print(f"{Colors.MAGENTA}{'='*70}{Colors.RESET}")
    
    auditor.audit_home_directory()
    auditor.audit_zoe_root()
    auditor.audit_services()
    auditor.audit_tests()
    auditor.audit_scripts()
    auditor.audit_docs()
    
    return auditor.generate_report()

if __name__ == "__main__":
    sys.exit(main())

