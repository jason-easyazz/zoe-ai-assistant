#!/usr/bin/env python3
"""
Database Audit Tool - Find ALL database references
Ensures we don't miss anything during consolidation
"""

import os
import re
from pathlib import Path
from collections import defaultdict
import json

class DatabaseAuditor:
    def __init__(self):
        # Auto-detect project root (works for both Pi and Nano)
        self.project_root = Path(__file__).parent.parent.parent.resolve()
        self.database_refs = defaultdict(list)
        self.db_patterns = [
            r'sqlite3\.connect\(["\']([^"\']+)["\']',
            r'DB_PATH\s*=\s*["\']([^"\']+)["\']',
            r'DATABASE_PATH\s*=\s*["\']([^"\']+)["\']',
            r'MEMORY_DB_PATH\s*=\s*["\']([^"\']+)["\']',
            r'db_path\s*=\s*["\']([^"\']+)["\']',
            r'DATABASE_URL\s*=\s*["\']([^"\']+)["\']',
            r'/app/data/([a-zA-Z_]+\.db)',
            r'data/([a-zA-Z_]+\.db)',
        ]
        
    def scan_file(self, file_path: Path) -> dict:
        """Scan a single file for database references"""
        results = []
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                
            for line_num, line in enumerate(content.split('\n'), 1):
                for pattern in self.db_patterns:
                    matches = re.findall(pattern, line)
                    if matches:
                        for match in matches:
                            # Normalize the path
                            db_name = match.split('/')[-1] if '/' in match else match
                            if db_name.endswith('.db'):
                                results.append({
                                    'line': line_num,
                                    'db_name': db_name,
                                    'full_path': match,
                                    'code': line.strip()
                                })
                                self.database_refs[db_name].append({
                                    'file': str(file_path),
                                    'line': line_num,
                                    'code': line.strip()
                                })
        except Exception as e:
            pass
        
        return results
    
    def scan_directory(self, directory: Path, extensions: list = ['.py', '.yml', '.yaml', '.json']):
        """Recursively scan directory for database references"""
        for file_path in directory.rglob('*'):
            if file_path.is_file() and file_path.suffix in extensions:
                # Skip node_modules, .git, etc
                if any(skip in str(file_path) for skip in ['node_modules', '.git', '__pycache__', 'dist']):
                    continue
                self.scan_file(file_path)
    
    def generate_report(self) -> dict:
        """Generate comprehensive audit report"""
        report = {
            'total_databases': len(self.database_refs),
            'databases': {},
            'summary': {}
        }
        
        for db_name, refs in sorted(self.database_refs.items()):
            report['databases'][db_name] = {
                'reference_count': len(refs),
                'files': list(set([r['file'] for r in refs])),
                'references': refs
            }
        
        # Summary statistics
        report['summary'] = {
            'most_referenced': max(self.database_refs.items(), key=lambda x: len(x[1]))[0] if self.database_refs else None,
            'total_references': sum(len(refs) for refs in self.database_refs.values()),
            'files_with_db_refs': len(set([r['file'] for refs in self.database_refs.values() for r in refs]))
        }
        
        return report
    
    def print_report(self, report: dict):
        """Print formatted audit report"""
        print("=" * 80)
        print("DATABASE AUDIT REPORT")
        print("=" * 80)
        print(f"\n📊 SUMMARY:")
        print(f"   Total databases referenced: {report['total_databases']}")
        print(f"   Total references: {report['summary']['total_references']}")
        print(f"   Files with DB references: {report['summary']['files_with_db_refs']}")
        print(f"   Most referenced: {report['summary']['most_referenced']}")
        
        print(f"\n🗄️  DATABASE BREAKDOWN:")
        for db_name, info in sorted(report['databases'].items(), key=lambda x: -x[1]['reference_count']):
            print(f"\n   {db_name}: {info['reference_count']} references in {len(info['files'])} files")
            print(f"   Files:")
            for file_path in sorted(set(info['files']))[:10]:  # Show first 10
                short_path = str(file_path).replace(str(self.project_root) + '/', '')
                print(f"      - {short_path}")
            if len(info['files']) > 10:
                print(f"      ... and {len(info['files']) - 10} more")
        
        print("\n" + "=" * 80)
    
    def save_report(self, report: dict, output_file: Path):
        """Save detailed report to JSON"""
        with open(output_file, 'w') as f:
            json.dump(report, f, indent=2)
        print(f"\n💾 Detailed report saved to: {output_file}")

def main():
    auditor = DatabaseAuditor()
    project_root = auditor.project_root
    
    print("🔍 Scanning project for database references...")
    auditor.scan_directory(project_root / "services")
    auditor.scan_directory(project_root / "scripts")
    auditor.scan_directory(project_root / "tools")
    
    # Scan root level files
    for file_path in project_root.glob("*.py"):
        auditor.scan_file(file_path)
    for file_path in project_root.glob("*.yml"):
        auditor.scan_file(file_path)
    
    report = auditor.generate_report()
    auditor.print_report(report)
    auditor.save_report(report, project_root / "database_audit_report.json")
    
    # Return exit code based on duplicates
    if report['total_databases'] > 1:
        print(f"\n⚠️  WARNING: Multiple databases detected!")
        return 1
    else:
        print(f"\n✅ SUCCESS: Single database source of truth!")
        return 0

if __name__ == "__main__":
    exit(main())

