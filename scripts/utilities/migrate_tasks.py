#!/usr/bin/env python3
"""Migrate existing tasks to dynamic format"""

import sqlite3
import json
from datetime import datetime

def migrate_tasks():
    conn = sqlite3.connect('data/zoe.db')
    cursor = conn.cursor()
    
    # Get existing tasks
    try:
        cursor.execute('SELECT * FROM developer_tasks')
        old_tasks = cursor.fetchall()
        
        print(f"Found {len(old_tasks)} tasks to migrate")
        
        for task in old_tasks:
            task_id, title, description, task_type, priority, assigned_to, status = task[:7]
            
            # Convert to requirements format
            requirements = [
                description,
                f"Type: {task_type}"
            ]
            
            constraints = [
                "Do not break existing functionality",
                "Maintain backward compatibility"
            ]
            
            acceptance_criteria = [
                "Feature works as described",
                "Tests pass",
                "No regression issues"
            ]
            
            # Insert into new table
            cursor.execute('''
                INSERT OR IGNORE INTO dynamic_tasks 
                (id, title, objective, requirements, constraints, acceptance_criteria, 
                 priority, assigned_to, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                task_id,
                title,
                description,  # Use description as objective
                json.dumps(requirements),
                json.dumps(constraints),
                json.dumps(acceptance_criteria),
                priority,
                assigned_to,
                status
            ))
        
        conn.commit()
        print(f"Migration complete!")
        
    except Exception as e:
        print(f"Migration error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    migrate_tasks()
