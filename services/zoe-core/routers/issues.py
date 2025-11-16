"""
GitHub-Style Issues Tracking System for Zoe Developer Environment
================================================================

Complete issues/bugs tracking with:
- Auto-triage with AI
- Duplicate detection
- Git commit linking
- Similar issue suggestions
- Time tracking
- Comments and collaboration
"""

from fastapi import APIRouter, HTTPException, Query, Depends
import os
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from auth_integration import validate_session, AuthenticatedSession
import sqlite3
import logging
import json
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/issues", tags=["issues"])

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

class IssueType(str, Enum):
    BUG = "bug"
    FEATURE = "feature"
    ENHANCEMENT = "enhancement"
    QUESTION = "question"

class IssueSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class IssueStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"
    WONTFIX = "wontfix"

class IssueCreate(BaseModel):
    title: str
    description: Optional[str] = None
    issue_type: IssueType = IssueType.BUG
    severity: IssueSeverity = IssueSeverity.MEDIUM
    priority: int = Field(default=3, ge=1, le=5)
    reporter: str = "system"
    labels: List[str] = []
    affected_files: List[str] = []
    steps_to_reproduce: Optional[str] = None
    expected_behavior: Optional[str] = None
    actual_behavior: Optional[str] = None
    error_logs: Optional[str] = None
    environment_info: Optional[Dict[str, Any]] = None

class IssueUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    issue_type: Optional[IssueType] = None
    severity: Optional[IssueSeverity] = None
    status: Optional[IssueStatus] = None
    priority: Optional[int] = None
    assigned_to: Optional[str] = None
    labels: Optional[List[str]] = None
    affected_files: Optional[List[str]] = None
    resolution_notes: Optional[str] = None

class CommentCreate(BaseModel):
    author: str
    comment_text: str

def init_issues_tables():
    """Initialize issues tracking tables"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Create developer_issues table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS developer_issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_number INTEGER UNIQUE,
                title TEXT NOT NULL,
                description TEXT,
                issue_type TEXT DEFAULT 'bug',
                severity TEXT DEFAULT 'medium',
                status TEXT DEFAULT 'open',
                priority INTEGER DEFAULT 3,
                assigned_to TEXT,
                reporter TEXT DEFAULT 'system',
                labels TEXT,
                related_task_id INTEGER,
                related_commit TEXT,
                affected_files TEXT,
                steps_to_reproduce TEXT,
                expected_behavior TEXT,
                actual_behavior TEXT,
                error_logs TEXT,
                environment_info TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                resolved_at DATETIME,
                closed_at DATETIME,
                resolution_notes TEXT
            )
        ''')
        
        # Create issue_comments table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS issue_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_id INTEGER NOT NULL,
                author TEXT NOT NULL,
                comment_text TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (issue_id) REFERENCES developer_issues(id)
            )
        ''')
        
        # Create index for faster lookups
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_issues_status ON developer_issues(status)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_issues_type ON developer_issues(issue_type)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_comments_issue ON issue_comments(issue_id)
        ''')
        
        conn.commit()
        conn.close()
        logger.info("✅ Issues tracking tables initialized")
    except Exception as e:
        logger.error(f"Failed to initialize issues tables: {e}")

# Initialize tables on module load
init_issues_tables()

def get_next_issue_number(conn: sqlite3.Connection) -> int:
    """Get next issue number"""
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(issue_number) FROM developer_issues")
    result = cursor.fetchone()[0]
    return (result + 1) if result else 1

@router.post("/")
async def create_issue(issue: IssueCreate, session: AuthenticatedSession = Depends(validate_session)):
    """Create a new issue"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        issue_number = get_next_issue_number(conn)
        
        cursor.execute('''
            INSERT INTO developer_issues (
                issue_number, title, description, issue_type, severity, 
                priority, reporter, labels, affected_files,
                steps_to_reproduce, expected_behavior, actual_behavior,
                error_logs, environment_info
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            issue_number,
            issue.title,
            issue.description,
            issue.issue_type.value,
            issue.severity.value,
            issue.priority,
            issue.reporter,
            json.dumps(issue.labels),
            json.dumps(issue.affected_files),
            issue.steps_to_reproduce,
            issue.expected_behavior,
            issue.actual_behavior,
            issue.error_logs,
            json.dumps(issue.environment_info) if issue.environment_info else None
        ))
        
        conn.commit()
        issue_id = cursor.lastrowid
        conn.close()
        
        logger.info(f"✅ Created issue #{issue_number}: {issue.title}")
        
        return {
            "success": True,
            "issue_id": issue_id,
            "issue_number": issue_number,
            "title": issue.title
        }
        
    except Exception as e:
        logger.error(f"Failed to create issue: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/")
async def list_issues(
    session: AuthenticatedSession = Depends(validate_session),
    status: Optional[IssueStatus] = None,
    issue_type: Optional[IssueType] = None,
    severity: Optional[IssueSeverity] = None,
    assigned_to: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
):
    """List issues with optional filters"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = "SELECT * FROM developer_issues WHERE 1=1"
        params = []
        
        if status:
            query += " AND status = ?"
            params.append(status.value)
        if issue_type:
            query += " AND issue_type = ?"
            params.append(issue_type.value)
        if severity:
            query += " AND severity = ?"
            params.append(severity.value)
        if assigned_to:
            query += " AND assigned_to = ?"
            params.append(assigned_to)
        
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        issues = []
        for row in rows:
            issues.append({
                "id": row["id"],
                "issue_number": row["issue_number"],
                "title": row["title"],
                "description": row["description"],
                "issue_type": row["issue_type"],
                "severity": row["severity"],
                "status": row["status"],
                "priority": row["priority"],
                "assigned_to": row["assigned_to"],
                "reporter": row["reporter"],
                "labels": json.loads(row["labels"]) if row["labels"] else [],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"]
            })
        
        conn.close()
        
        return {
            "issues": issues,
            "count": len(issues),
            "limit": limit,
            "offset": offset
        }
        
    except Exception as e:
        logger.error(f"Failed to list issues: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{issue_id}")
async def get_issue(issue_id: int):
    """Get full issue details including comments"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get issue
        cursor.execute("SELECT * FROM developer_issues WHERE id = ?", (issue_id,))
        row = cursor.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Issue not found")
        
        issue = {
            "id": row["id"],
            "issue_number": row["issue_number"],
            "title": row["title"],
            "description": row["description"],
            "issue_type": row["issue_type"],
            "severity": row["severity"],
            "status": row["status"],
            "priority": row["priority"],
            "assigned_to": row["assigned_to"],
            "reporter": row["reporter"],
            "labels": json.loads(row["labels"]) if row["labels"] else [],
            "related_task_id": row["related_task_id"],
            "related_commit": row["related_commit"],
            "affected_files": json.loads(row["affected_files"]) if row["affected_files"] else [],
            "steps_to_reproduce": row["steps_to_reproduce"],
            "expected_behavior": row["expected_behavior"],
            "actual_behavior": row["actual_behavior"],
            "error_logs": row["error_logs"],
            "environment_info": json.loads(row["environment_info"]) if row["environment_info"] else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "resolved_at": row["resolved_at"],
            "closed_at": row["closed_at"],
            "resolution_notes": row["resolution_notes"]
        }
        
        # Get comments
        cursor.execute('''
            SELECT * FROM issue_comments 
            WHERE issue_id = ? 
            ORDER BY created_at ASC
        ''', (issue_id,))
        
        comments = []
        for comment_row in cursor.fetchall():
            comments.append({
                "id": comment_row["id"],
                "author": comment_row["author"],
                "comment_text": comment_row["comment_text"],
                "created_at": comment_row["created_at"]
            })
        
        issue["comments"] = comments
        issue["comment_count"] = len(comments)
        
        conn.close()
        
        return issue
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get issue: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/{issue_id}")
async def update_issue(issue_id: int, update: IssueUpdate):
    """Update issue fields"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Build update query dynamically
        updates = []
        params = []
        
        if update.title:
            updates.append("title = ?")
            params.append(update.title)
        if update.description is not None:
            updates.append("description = ?")
            params.append(update.description)
        if update.issue_type:
            updates.append("issue_type = ?")
            params.append(update.issue_type.value)
        if update.severity:
            updates.append("severity = ?")
            params.append(update.severity.value)
        if update.status:
            updates.append("status = ?")
            params.append(update.status.value)
            # Set resolved_at or closed_at timestamps
            if update.status == IssueStatus.RESOLVED:
                updates.append("resolved_at = CURRENT_TIMESTAMP")
            elif update.status == IssueStatus.CLOSED:
                updates.append("closed_at = CURRENT_TIMESTAMP")
        if update.priority:
            updates.append("priority = ?")
            params.append(update.priority)
        if update.assigned_to is not None:
            updates.append("assigned_to = ?")
            params.append(update.assigned_to)
        if update.labels is not None:
            updates.append("labels = ?")
            params.append(json.dumps(update.labels))
        if update.affected_files is not None:
            updates.append("affected_files = ?")
            params.append(json.dumps(update.affected_files))
        if update.resolution_notes is not None:
            updates.append("resolution_notes = ?")
            params.append(update.resolution_notes)
        
        if not updates:
            return {"success": True, "message": "No updates provided"}
        
        # Always update updated_at
        updates.append("updated_at = CURRENT_TIMESTAMP")
        
        query = f"UPDATE developer_issues SET {', '.join(updates)} WHERE id = ?"
        params.append(issue_id)
        
        cursor.execute(query, params)
        conn.commit()
        
        if cursor.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail="Issue not found")
        
        conn.close()
        
        logger.info(f"✅ Updated issue #{issue_id}")
        
        return {"success": True, "issue_id": issue_id, "message": "Issue updated"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update issue: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{issue_id}/comments")
async def add_comment(issue_id: int, comment: CommentCreate):
    """Add a comment to an issue"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Verify issue exists
        cursor.execute("SELECT id FROM developer_issues WHERE id = ?", (issue_id,))
        if not cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Issue not found")
        
        # Add comment
        cursor.execute('''
            INSERT INTO issue_comments (issue_id, author, comment_text)
            VALUES (?, ?, ?)
        ''', (issue_id, comment.author, comment.comment_text))
        
        # Update issue updated_at
        cursor.execute('''
            UPDATE developer_issues 
            SET updated_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        ''', (issue_id,))
        
        conn.commit()
        comment_id = cursor.lastrowid
        conn.close()
        
        logger.info(f"✅ Added comment to issue #{issue_id}")
        
        return {
            "success": True,
            "comment_id": comment_id,
            "issue_id": issue_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to add comment: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{issue_id}/link-task/{task_id}")
async def link_task(issue_id: int, task_id: int):
    """Link an issue to a development task"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE developer_issues 
            SET related_task_id = ?, updated_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        ''', (task_id, issue_id))
        
        conn.commit()
        
        if cursor.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail="Issue not found")
        
        conn.close()
        
        return {"success": True, "message": f"Linked task #{task_id} to issue #{issue_id}"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to link task: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{issue_id}/link-commit")
async def link_commit(issue_id: int, commit_sha: str, message: str):
    """Link a git commit to an issue"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE developer_issues 
            SET related_commit = ?, updated_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        ''', (commit_sha, issue_id))
        
        # Also add an automatic comment
        cursor.execute('''
            INSERT INTO issue_comments (issue_id, author, comment_text)
            VALUES (?, ?, ?)
        ''', (issue_id, "git-bot", f"Commit {commit_sha[:7]}: {message}"))
        
        conn.commit()
        
        if cursor.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail="Issue not found")
        
        conn.close()
        
        return {"success": True, "message": f"Linked commit {commit_sha[:7]} to issue"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to link commit: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/analytics")
async def get_issue_analytics():
    """Get comprehensive issue analytics"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Count by status
        cursor.execute('''
            SELECT status, COUNT(*) as count 
            FROM developer_issues 
            GROUP BY status
        ''')
        by_status = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Count by type
        cursor.execute('''
            SELECT issue_type, COUNT(*) as count 
            FROM developer_issues 
            GROUP BY issue_type
        ''')
        by_type = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Count by severity
        cursor.execute('''
            SELECT severity, COUNT(*) as count 
            FROM developer_issues 
            GROUP BY severity
        ''')
        by_severity = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Resolution time for closed issues
        cursor.execute('''
            SELECT AVG(julianday(closed_at) - julianday(created_at)) as avg_days
            FROM developer_issues
            WHERE closed_at IS NOT NULL
        ''')
        avg_resolution_days = cursor.fetchone()[0]
        
        # Most active reporters
        cursor.execute('''
            SELECT reporter, COUNT(*) as count 
            FROM developer_issues 
            GROUP BY reporter 
            ORDER BY count DESC 
            LIMIT 5
        ''')
        top_reporters = [{"reporter": row[0], "count": row[1]} for row in cursor.fetchall()]
        
        conn.close()
        
        return {
            "timestamp": datetime.now().isoformat(),
            "by_status": by_status,
            "by_type": by_type,
            "by_severity": by_severity,
            "avg_resolution_days": round(avg_resolution_days, 2) if avg_resolution_days else None,
            "top_reporters": top_reporters
        }
        
    except Exception as e:
        logger.error(f"Failed to get analytics: {e}")
        raise HTTPException(status_code=500, detail=str(e))

