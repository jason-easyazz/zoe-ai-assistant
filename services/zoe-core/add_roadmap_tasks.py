"""Add Zoe Evolution v3.0 roadmap tasks to developer task system"""
import os
import sqlite3
import json
from datetime import datetime

conn = sqlite3.connect(os.getenv('DATABASE_PATH', '/app/data/zoe.db'))
cursor = conn.cursor()

# Ensure table exists
cursor.execute('''
    CREATE TABLE IF NOT EXISTS dynamic_tasks (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        objective TEXT NOT NULL,
        requirements TEXT NOT NULL,
        constraints TEXT,
        acceptance_criteria TEXT,
        priority TEXT DEFAULT 'medium',
        assigned_to TEXT DEFAULT 'zack',
        status TEXT DEFAULT 'pending',
        context_snapshot TEXT,
        last_analysis TEXT,
        execution_count INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_executed_at TIMESTAMP,
        completed_at TIMESTAMP
    )
''')

# Define 8 critical tasks for Zoe Evolution v3.0
tasks = [
    {
        "id": "zoe-evolution-001",
        "title": "Create Unified Database Schema",
        "objective": "Consolidate all scattered SQLite databases into single zoe.db",
        "requirements": ["Analyze current databases", "Design unified schema", "Create migration scripts"],
        "constraints": ["Preserve all data", "No functionality loss"],
        "acceptance_criteria": ["Single zoe.db", "All data migrated", "Performance maintained"],
        "priority": "critical",
        "phase": "foundation_cleanup"
    },
    {
        "id": "zoe-evolution-002",
        "title": "Create zoe-mcp-server Service",
        "objective": "Create MCP server exposing Zoe capabilities as standardized tools",
        "requirements": ["Create service", "Add Docker config", "Implement health checks"],
        "constraints": ["Follow MCP standards", "Must be performant"],
        "acceptance_criteria": ["Service created", "Docker working", "Health checks passing"],
        "priority": "critical",
        "phase": "mcp_implementation"
    },
    {
        "id": "zoe-evolution-003",
        "title": "Implement Core MCP Tools",
        "objective": "Implement MCP tools for memory, calendar, and list operations",
        "requirements": ["search_memories tool", "create_person tool", "calendar tools", "list tools"],
        "constraints": ["Preserve functionality", "Follow MCP standards"],
        "acceptance_criteria": ["All tools working", "Error handling complete"],
        "priority": "critical",
        "phase": "mcp_implementation"
    },
    {
        "id": "zoe-evolution-004",
        "title": "Extract People Service",
        "objective": "Extract people functionality into dedicated service",
        "requirements": ["Create people service", "Extract from memories router", "Add to MCP"],
        "constraints": ["Preserve functionality", "No API breaks"],
        "acceptance_criteria": ["Service extracted", "APIs working"],
        "priority": "high",
        "phase": "service_separation"
    },
    {
        "id": "zoe-evolution-005",
        "title": "Extract Collections Service",
        "objective": "Extract collections functionality into dedicated service",
        "requirements": ["Create collections service", "Visual layout management", "Add to MCP"],
        "constraints": ["Preserve functionality", "No API breaks"],
        "acceptance_criteria": ["Service extracted", "APIs working"],
        "priority": "high",
        "phase": "service_separation"
    },
    {
        "id": "zoe-evolution-006",
        "title": "Create N8N Bridge Service",
        "objective": "Create N8N bridge for workflow automation",
        "requirements": ["Create bridge service", "Workflow generation", "Templates"],
        "constraints": ["Integrate with N8N", "Support complex workflows"],
        "acceptance_criteria": ["Service created", "Workflows generating"],
        "priority": "high",
        "phase": "n8n_integration"
    },
    {
        "id": "zoe-evolution-007",
        "title": "Create Comprehensive Test Suite",
        "objective": "Create test suite for all new functionality",
        "requirements": ["Test MCP tools", "Test services", "Performance tests"],
        "constraints": [">90% coverage", "Fast execution"],
        "acceptance_criteria": ["Suite created", "All passing"],
        "priority": "high",
        "phase": "testing_validation"
    },
    {
        "id": "zoe-evolution-008",
        "title": "Update Documentation",
        "objective": "Update all documentation for new architecture",
        "requirements": ["Update ZOE_CURRENT_STATE", "MCP docs", "N8N docs", "API docs"],
        "constraints": ["Comprehensive", "Accurate"],
        "acceptance_criteria": ["All docs updated", "Deployment guide complete"],
        "priority": "medium",
        "phase": "testing_validation"
    }
]

# Insert tasks
for task in tasks:
    cursor.execute('''
        INSERT OR REPLACE INTO dynamic_tasks 
        (id, title, objective, requirements, constraints, acceptance_criteria, 
         priority, assigned_to, status, context_snapshot)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        task["id"],
        task["title"],
        task["objective"],
        json.dumps(task["requirements"]),
        json.dumps(task["constraints"]),
        json.dumps(task["acceptance_criteria"]),
        task["priority"],
        "zack",
        "pending",
        json.dumps({"phase": task["phase"], "roadmap": "zoe-evolution-v3"})
    ))

conn.commit()
conn.close()

print("✅ Added 8 critical Zoe Evolution v3.0 tasks")
print("📋 Tasks available at /api/developer/tasks/list")
print("🚀 Roadmap committed - work can continue even if chat is lost")
