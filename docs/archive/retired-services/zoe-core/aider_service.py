"""
Aider Service Wrapper - Integrates Aider with Zoe's task system
Provides web API for Aider interactions
"""
import os
import sys
import json
import sqlite3
import subprocess
import asyncio
import tempfile
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

# Auto-detect project root
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
import threading
import queue

# Add Aider to path
sys.path.append(str(PROJECT_ROOT / "tools/aider-env/lib/python3.11/site-packages"))

class AiderWebService:
    def __init__(self):
        self.sessions = {}
        self.task_db = "/app/data/zoe.db"
        self.conversation_db = "/app/data/zoe.db"
        self.setup_database()
        
    def setup_database(self):
        """Create tables for Aider conversations and task links"""
        conn = sqlite3.connect(self.conversation_db)
        c = conn.cursor()
        
        c.execute('''CREATE TABLE IF NOT EXISTS aider_sessions (
            session_id TEXT PRIMARY KEY,
            task_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP,
            status TEXT DEFAULT 'active',
            files_context TEXT,
            conversation_history TEXT
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS aider_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,
            content TEXT,
            code_changes TEXT,
            files_modified TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES aider_sessions(session_id)
        )''')
        
        conn.commit()
        conn.close()
    
    async def create_session(self, task_id: Optional[str] = None, files: List[str] = None) -> str:
        """Create new Aider session, optionally linked to a task"""
        session_id = f"aider_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Determine which model to use
        model = self._select_model()
        
        # Create session record
        conn = sqlite3.connect(self.conversation_db)
        c = conn.cursor()
        c.execute("""
            INSERT INTO aider_sessions (session_id, task_id, files_context, status)
            VALUES (?, ?, ?, 'active')
        """, (session_id, task_id, json.dumps(files or [])))
        conn.commit()
        conn.close()
        
        # Initialize Aider process
        self.sessions[session_id] = {
            "task_id": task_id,
            "files": files or [],
            "model": model,
            "process": None,
            "queue": queue.Queue()
        }
        
        return session_id
    
    def _select_model(self) -> str:
        """Select best available model"""
        # Check for API keys
        if os.getenv("ANTHROPIC_API_KEY"):
            return "claude-3-opus-20240229"
        elif os.getenv("OPENAI_API_KEY"):
            return "gpt-4-turbo-preview"
        else:
            # Use local Ollama
            return "ollama/llama3.2:3b"
    
    async def send_message(self, session_id: str, message: str, auto_execute: bool = False) -> Dict:
        """Send message to Aider and get response"""
        if session_id not in self.sessions:
            return {"error": "Session not found"}
        
        session = self.sessions[session_id]
        
        # Prepare Aider command
        cmd = self._build_aider_command(session, message, auto_execute)
        
        # Execute Aider
        result = await self._run_aider_command(cmd, session)
        
        # Parse response
        response = self._parse_aider_response(result)
        
        # Save to database
        self._save_message(session_id, "user", message)
        self._save_message(session_id, "assistant", response.get("content", ""), 
                          response.get("code_changes"))
        
        # If linked to task, update task progress
        if session["task_id"]:
            self._update_task_progress(session["task_id"], response)
        
        return response
    
    def _build_aider_command(self, session: Dict, message: str, auto_execute: bool) -> List[str]:
        """Build Aider command with appropriate flags"""
        cmd = [
            str(PROJECT_ROOT / "tools/aider-env/bin/aider"),
            "--model", session["model"],
            "--yes-always" if auto_execute else "--no-auto-commits",
        ]
        
        # Add files to context
        for file in session["files"]:
            cmd.extend(["--file", file])
        
        # Add message as input
        cmd.extend(["--message", message])
        
        return cmd
    
    async def _run_aider_command(self, cmd: List[str], session: Dict) -> str:
        """Execute Aider command and capture output"""
        try:
            # Run in Zoe directory for git context
            result = subprocess.run(
                cmd,
                cwd="/home/zoe/assistant",
                capture_output=True,
                text=True,
                timeout=60
            )
            
            return result.stdout + result.stderr
            
        except subprocess.TimeoutExpired:
            return "Aider command timed out after 60 seconds"
        except Exception as e:
            return f"Error running Aider: {str(e)}"
    
    def _parse_aider_response(self, output: str) -> Dict:
        """Parse Aider output to extract code changes and explanations"""
        response = {
            "content": output,
            "code_changes": [],
            "files_modified": [],
            "git_commit": None
        }
        
        # Extract code blocks
        import re
        code_blocks = re.findall(r'```[\w]*\n(.*?)\n```', output, re.DOTALL)
        if code_blocks:
            response["code_changes"] = code_blocks
        
        # Extract modified files
        file_pattern = r'Modified: (.*?\.[\w]+)'
        files = re.findall(file_pattern, output)
        response["files_modified"] = files
        
        # Check for git commit
        commit_pattern = r'Commit: ([a-f0-9]{7,})'
        commit = re.search(commit_pattern, output)
        if commit:
            response["git_commit"] = commit.group(1)
        
        return response
    
    def _save_message(self, session_id: str, role: str, content: str, 
                     code_changes: Optional[List] = None):
        """Save message to conversation history"""
        conn = sqlite3.connect(self.conversation_db)
        c = conn.cursor()
        
        c.execute("""
            INSERT INTO aider_messages (session_id, role, content, code_changes)
            VALUES (?, ?, ?, ?)
        """, (session_id, role, content, 
              json.dumps(code_changes) if code_changes else None))
        
        # Update session last active
        c.execute("""
            UPDATE aider_sessions 
            SET last_active = CURRENT_TIMESTAMP 
            WHERE session_id = ?
        """, (session_id,))
        
        conn.commit()
        conn.close()
    
    def _update_task_progress(self, task_id: str, response: Dict):
        """Update linked task with Aider progress"""
        conn = sqlite3.connect(self.task_db)
        c = conn.cursor()
        
        # Add to task conversation
        c.execute("""
            INSERT INTO task_conversations (task_id, role, message)
            VALUES (?, 'aider', ?)
        """, (task_id, json.dumps(response)))
        
        # Update task status if code was generated
        if response.get("code_changes"):
            c.execute("""
                UPDATE tasks 
                SET code_generated = ?, 
                    implementation_path = ?
                WHERE task_id = ?
            """, (json.dumps(response["code_changes"]), 
                  json.dumps(response["files_modified"]),
                  task_id))
        
        conn.commit()
        conn.close()
    
    async def get_session_history(self, session_id: str) -> List[Dict]:
        """Get conversation history for a session"""
        conn = sqlite3.connect(self.conversation_db)
        c = conn.cursor()
        
        c.execute("""
            SELECT role, content, code_changes, timestamp
            FROM aider_messages
            WHERE session_id = ?
            ORDER BY timestamp
        """, (session_id,))
        
        messages = []
        for row in c.fetchall():
            messages.append({
                "role": row[0],
                "content": row[1],
                "code_changes": json.loads(row[2]) if row[2] else None,
                "timestamp": row[3]
            })
        
        conn.close()
        return messages
    
    async def link_to_task(self, session_id: str, task_id: str):
        """Link an Aider session to a task"""
        conn = sqlite3.connect(self.conversation_db)
        c = conn.cursor()
        c.execute("""
            UPDATE aider_sessions 
            SET task_id = ? 
            WHERE session_id = ?
        """, (task_id, session_id))
        conn.commit()
        conn.close()
        
        if session_id in self.sessions:
            self.sessions[session_id]["task_id"] = task_id

# Initialize service
aider_service = AiderWebService()
