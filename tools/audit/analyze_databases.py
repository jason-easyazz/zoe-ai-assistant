#!/usr/bin/env python3
"""
Database Analysis Script for Zoe Evolution v3.0
Analyzes all existing SQLite databases to design unified schema
"""

import sqlite3
import json
import os
from pathlib import Path
from collections import defaultdict

def analyze_database(db_path):
    """Analyze a single database and return schema information"""
    if not os.path.exists(db_path):
        return {"error": f"Database {db_path} does not exist"}
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Get all table names
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        
        schema_info = {
            "database": db_path,
            "tables": {},
            "total_tables": len(tables)
        }
        
        # Analyze each table
        for table in tables:
            # Get table schema
            cursor.execute(f"PRAGMA table_info({table})")
            columns = cursor.fetchall()
            
            # Get row count
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            row_count = cursor.fetchone()[0]
            
            # Get sample data (first 3 rows)
            cursor.execute(f"SELECT * FROM {table} LIMIT 3")
            sample_data = cursor.fetchall()
            
            schema_info["tables"][table] = {
                "columns": [{"name": col[1], "type": col[2], "not_null": bool(col[3]), "default": col[4], "primary_key": bool(col[5])} for col in columns],
                "row_count": row_count,
                "sample_data": sample_data
            }
        
        conn.close()
        return schema_info
        
    except Exception as e:
        return {"error": f"Error analyzing {db_path}: {str(e)}"}

def main():
    """Main analysis function"""
    print("🔍 ZOE DATABASE ANALYSIS - Evolution v3.0")
    print("=" * 50)
    
    # Find all databases
    data_dir = Path("/home/pi/zoe/data")
    databases = list(data_dir.glob("*.db"))
    
    print(f"Found {len(databases)} databases:")
    for db in databases:
        print(f"  - {db.name}")
    
    print("\n" + "=" * 50)
    
    # Analyze each database
    all_schemas = {}
    total_tables = 0
    
    for db_path in databases:
        print(f"\n📊 Analyzing {db_path.name}...")
        schema_info = analyze_database(str(db_path))
        
        if "error" in schema_info:
            print(f"❌ {schema_info['error']}")
            continue
            
        all_schemas[db_path.name] = schema_info
        total_tables += schema_info["total_tables"]
        
        print(f"   Tables: {schema_info['total_tables']}")
        for table_name, table_info in schema_info["tables"].items():
            print(f"     - {table_name}: {table_info['row_count']} rows")
    
    print(f"\n📈 SUMMARY:")
    print(f"   Total databases: {len(databases)}")
    print(f"   Total tables: {total_tables}")
    
    # Save detailed analysis
    analysis_file = "/home/pi/zoe/database_analysis.json"
    with open(analysis_file, 'w') as f:
        json.dump(all_schemas, f, indent=2, default=str)
    
    print(f"\n💾 Detailed analysis saved to: {analysis_file}")
    
    # Generate unified schema recommendations
    print(f"\n🎯 UNIFIED SCHEMA RECOMMENDATIONS:")
    print("=" * 50)
    
    # Collect all unique tables
    all_tables = defaultdict(list)
    for db_name, schema in all_schemas.items():
        for table_name in schema["tables"].keys():
            all_tables[table_name].append(db_name)
    
    # Identify tables that appear in multiple databases
    duplicates = {table: dbs for table, dbs in all_tables.items() if len(dbs) > 1}
    if duplicates:
        print("⚠️  Tables appearing in multiple databases:")
        for table, dbs in duplicates.items():
            print(f"   - {table}: {', '.join(dbs)}")
    
    # Identify core tables by frequency
    core_tables = ["users", "people", "projects", "memories", "events", "tasks", "lists"]
    print(f"\n✅ Core tables to prioritize:")
    for table in core_tables:
        if table in all_tables:
            print(f"   - {table}: {', '.join(all_tables[table])}")
    
    return all_schemas

if __name__ == "__main__":
    main()

