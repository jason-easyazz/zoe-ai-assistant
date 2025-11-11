#!/usr/bin/env python3
"""
Database Validation Tool
Ensures only allowed databases exist and enforces single source of truth
Part of database consolidation protection system
"""

import os
import sys
from pathlib import Path

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
from typing import List, Tuple

# ALLOWED DATABASES - The ONLY databases that should exist
ALLOWED_DATABASES = {
    'zoe.db': 'Primary operational database - SINGLE SOURCE OF TRUTH',
    'memory.db': 'Light RAG embeddings - Specialized semantic search only',
    'training.db': 'ML training data collection - Specialized AI learning system (logs interactions, feedback, corrections for nightly fine-tuning)'
}

# FORBIDDEN DATABASES - These should NEVER exist (they were consolidated)
FORBIDDEN_DATABASES = {
    'auth.db': 'Consolidated into zoe.db',
    'developer_tasks.db': 'Consolidated into zoe.db',
    'sessions.db': 'Consolidated into zoe.db',
    'satisfaction.db': 'Consolidated into zoe.db',
    'self_awareness.db': 'Consolidated into zoe.db',
    'learning.db': 'Consolidated into zoe.db',
    'agent_planning.db': 'Consolidated into zoe.db',
    'tool_registry.db': 'Consolidated into zoe.db',
    'snapshots.db': 'Consolidated into zoe.db',
    'model_performance.db': 'Consolidated into zoe.db',
    'context_cache.db': 'Consolidated into zoe.db',
    'knowledge.db': 'Consolidated into zoe.db',
    'aider_conversations.db': 'Consolidated into zoe.db',
    'tasks.db': 'Consolidated into zoe.db',
    'performance.db': 'Consolidated into zoe.db',
    'vector_search.db': 'Consolidated into zoe.db',
    'zoe_auth.db': 'Never should exist',
    'zoe_unified.db': 'Temporary migration file only',
    'zoe_old.db': 'Backup file only'
}

# ALLOWED IN BACKUP - These are OK in backup directories
BACKUP_PATTERNS = ['backup/', 'archive/']

class DatabaseValidator:
    def __init__(self, project_root: str = None):
        # Auto-detect project root if not provided (works for both Pi and Nano)
        if project_root is None:
            project_root = str(PROJECT_ROOT)
        self.project_root = Path(project_root)
        self.data_dir = self.project_root / "data"
        self.violations = []
        
    def scan_for_databases(self) -> List[Path]:
        """Find all .db files in data directory"""
        databases = []
        if self.data_dir.exists():
            for db_file in self.data_dir.glob("*.db"):
                databases.append(db_file)
        return databases
    
    def is_backup_location(self, file_path: Path) -> bool:
        """Check if file is in a backup/archive location"""
        path_str = str(file_path)
        return any(pattern in path_str for pattern in BACKUP_PATTERNS)
    
    def validate(self) -> Tuple[bool, List[dict]]:
        """Validate database structure"""
        violations = []
        
        # Find all databases
        databases = self.scan_for_databases()
        
        for db_path in databases:
            db_name = db_path.name
            
            # Skip if in backup location
            if self.is_backup_location(db_path):
                continue
            
            # Check if forbidden
            if db_name in FORBIDDEN_DATABASES:
                violations.append({
                    'type': 'FORBIDDEN_DATABASE',
                    'severity': 'CRITICAL',
                    'file': str(db_path),
                    'database': db_name,
                    'reason': FORBIDDEN_DATABASES[db_name],
                    'fix': f'This database was consolidated. Delete it and use zoe.db instead.'
                })
            
            # Check if unknown (not in allowed list)
            elif db_name not in ALLOWED_DATABASES:
                violations.append({
                    'type': 'UNKNOWN_DATABASE',
                    'severity': 'ERROR',
                    'file': str(db_path),
                    'database': db_name,
                    'reason': 'Database not in allowed list',
                    'fix': 'Either add to allowed list with architectural justification, or consolidate into zoe.db'
                })
        
        # Check that required databases exist
        for required_db in ['zoe.db']:  # memory.db is optional
            db_path = self.data_dir / required_db
            if not db_path.exists():
                violations.append({
                    'type': 'MISSING_REQUIRED_DATABASE',
                    'severity': 'CRITICAL',
                    'database': required_db,
                    'reason': 'Required database is missing',
                    'fix': f'Create {required_db} or restore from backup'
                })
        
        return len(violations) == 0, violations
    
    def print_report(self, success: bool, violations: List[dict]):
        """Print validation report"""
        print("=" * 80)
        print("DATABASE VALIDATION REPORT")
        print("=" * 80)
        
        if success:
            print("\n✅ VALIDATION PASSED")
            print(f"   All databases comply with single source of truth policy")
            print(f"\n📋 Allowed databases:")
            for db_name, description in ALLOWED_DATABASES.items():
                db_path = self.data_dir / db_name
                exists = "✅" if db_path.exists() else "⚠️ (not found)"
                print(f"      - {db_name}: {description} {exists}")
        else:
            print(f"\n❌ VALIDATION FAILED")
            print(f"   Found {len(violations)} violation(s)")
            
            # Group by severity
            critical = [v for v in violations if v['severity'] == 'CRITICAL']
            errors = [v for v in violations if v['severity'] == 'ERROR']
            
            if critical:
                print(f"\n🚨 CRITICAL VIOLATIONS ({len(critical)}):")
                for v in critical:
                    print(f"\n   Database: {v.get('database', 'N/A')}")
                    print(f"   Type: {v['type']}")
                    print(f"   Reason: {v['reason']}")
                    print(f"   Fix: {v['fix']}")
                    if 'file' in v:
                        print(f"   File: {v['file']}")
            
            if errors:
                print(f"\n⚠️  ERRORS ({len(errors)}):")
                for v in errors:
                    print(f"\n   Database: {v.get('database', 'N/A')}")
                    print(f"   Reason: {v['reason']}")
                    print(f"   Fix: {v['fix']}")
        
        print("\n" + "=" * 80)
        
        return 0 if success else 1

def main():
    """Main validation function"""
    validator = DatabaseValidator()
    success, violations = validator.validate()
    exit_code = validator.print_report(success, violations)
    
    # Save violations to file for CI/CD
    if violations:
        import json
        violations_file = PROJECT_ROOT / "database_violations.json"
        with open(violations_file, 'w') as f:
            json.dump(violations, f, indent=2)
        print(f"\n💾 Violations saved to: database_violations.json")
    
    return exit_code

if __name__ == "__main__":
    sys.exit(main())

