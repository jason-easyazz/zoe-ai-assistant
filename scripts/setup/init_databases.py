#!/usr/bin/env python3
"""
Database Initialization Script for Zoe AI Assistant
Creates all required databases from schema files

Usage:
    python3 scripts/setup/init_databases.py [--with-seed-data] [--force]
    
Options:
    --with-seed-data    Apply demo data after creating databases
    --force            Overwrite existing databases without backup
"""

import sqlite3
import argparse
import sys
from pathlib import Path
from datetime import datetime
import shutil

# ANSI color codes
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color

PROJECT_ROOT = Path("/home/zoe/assistant")
DATA_DIR = PROJECT_ROOT / "data"
SCHEMA_DIR = DATA_DIR / "schema"

def print_header():
    """Print script header"""
    print(f"{Colors.BLUE}{'='*40}{Colors.NC}")
    print(f"{Colors.BLUE}Zoe Database Initialization{Colors.NC}")
    print(f"{Colors.BLUE}{'='*40}{Colors.NC}\n")

def check_requirements():
    """Check if required directories and files exist"""
    if not SCHEMA_DIR.exists():
        print(f"{Colors.RED}✗ Error: Schema directory not found: {SCHEMA_DIR}{Colors.NC}")
        sys.exit(1)
    
    required_schemas = ["zoe_schema.sql", "memory_schema.sql", "training_schema.sql"]
    for schema in required_schemas:
        if not (SCHEMA_DIR / schema).exists():
            print(f"{Colors.RED}✗ Error: Required schema file not found: {schema}{Colors.NC}")
            sys.exit(1)
    
    print(f"{Colors.GREEN}✓ All required schema files found{Colors.NC}\n")

def backup_database(db_path: Path) -> Path:
    """Backup existing database file"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_suffix(f".backup.{timestamp}.db")
    shutil.copy2(db_path, backup_path)
    return backup_path

def init_database(db_name: str, schema_file: Path, force: bool = False) -> bool:
    """Initialize a database from schema file"""
    db_path = DATA_DIR / db_name
    
    print(f"{Colors.YELLOW}Initializing {db_name}...{Colors.NC}")
    
    # Check if schema file exists
    if not schema_file.exists():
        print(f"{Colors.RED}✗ Schema file not found: {schema_file}{Colors.NC}")
        return False
    
    # Handle existing database
    if db_path.exists():
        if force:
            print(f"  {Colors.YELLOW}⚠{Colors.NC}  Removing existing database (--force mode)")
            db_path.unlink()
        else:
            backup_path = backup_database(db_path)
            print(f"  {Colors.YELLOW}⚠{Colors.NC}  Existing database backed up to: {backup_path.name}")
            db_path.unlink()
    
    try:
        # Create database and apply schema
        print(f"  Creating database: {db_name}")
        conn = sqlite3.connect(str(db_path))
        
        # Read and execute schema
        with open(schema_file, 'r') as f:
            schema_sql = f.read()
        
        conn.executescript(schema_sql)
        conn.commit()
        
        # Get table count
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        table_count = cursor.fetchone()[0]
        
        conn.close()
        
        # Set permissions
        db_path.chmod(0o644)
        
        print(f"  {Colors.GREEN}✓{Colors.NC} {db_name} created successfully")
        print(f"  Tables created: {table_count}")
        return True
        
    except Exception as e:
        print(f"  {Colors.RED}✗{Colors.NC} Failed to create {db_name}: {str(e)}")
        return False

def apply_seed_data() -> bool:
    """Apply seed data to zoe.db"""
    seed_file = SCHEMA_DIR / "seed_data.sql"
    
    print(f"\n{Colors.YELLOW}Applying seed data...{Colors.NC}")
    
    if not seed_file.exists():
        print(f"{Colors.YELLOW}⚠{Colors.NC}  Seed data file not found, skipping")
        return False
    
    try:
        db_path = DATA_DIR / "zoe.db"
        conn = sqlite3.connect(str(db_path))
        
        with open(seed_file, 'r') as f:
            seed_sql = f.read()
        
        conn.executescript(seed_sql)
        conn.commit()
        conn.close()
        
        print(f"{Colors.GREEN}✓{Colors.NC} Seed data applied successfully")
        print(f"  Demo user created: demo / demo123")
        return True
        
    except Exception as e:
        print(f"{Colors.RED}✗{Colors.NC} Failed to apply seed data: {str(e)}")
        return False

def print_summary(seed_data_applied: bool):
    """Print completion summary"""
    print(f"\n{Colors.BLUE}{'='*40}{Colors.NC}")
    print(f"{Colors.GREEN}✓ Database initialization complete!{Colors.NC}")
    print(f"{Colors.BLUE}{'='*40}{Colors.NC}\n")
    
    print("Databases created:")
    print("  • zoe.db      - Main application database")
    print("  • memory.db   - LightRAG memory database")
    print("  • training.db - Training data database")
    print()
    
    if seed_data_applied:
        print("Demo user credentials:")
        print("  Username: demo")
        print("  Password: demo123")
        print()
    
    print("Next steps:")
    print("  1. Start Zoe services: docker-compose up -d")
    print("  2. Access UI: http://localhost:3080")
    if not seed_data_applied:
        print("  3. Create your user via onboarding")
    print()
    print(f"{Colors.GREEN}Ready to use Zoe!{Colors.NC}")

def main():
    parser = argparse.ArgumentParser(description="Initialize Zoe databases from schema files")
    parser.add_argument("--with-seed-data", action="store_true", 
                       help="Apply demo data after creating databases")
    parser.add_argument("--force", action="store_true",
                       help="Overwrite existing databases without backup")
    args = parser.parse_args()
    
    print_header()
    check_requirements()
    
    # Create data directory if needed
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # Initialize databases
    success = True
    success &= init_database("zoe.db", SCHEMA_DIR / "zoe_schema.sql", args.force)
    print()
    success &= init_database("memory.db", SCHEMA_DIR / "memory_schema.sql", args.force)
    print()
    success &= init_database("training.db", SCHEMA_DIR / "training_schema.sql", args.force)
    
    if not success:
        print(f"\n{Colors.RED}✗ Some databases failed to initialize{Colors.NC}")
        sys.exit(1)
    
    # Apply seed data if requested
    seed_applied = False
    if args.with_seed_data:
        seed_applied = apply_seed_data()
    
    print_summary(seed_applied)

if __name__ == "__main__":
    main()



