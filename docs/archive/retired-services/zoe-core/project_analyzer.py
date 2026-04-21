"""Intelligent Project Analyzer for Zack"""
import os
import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Any
import subprocess

class ProjectAnalyzer:
    def __init__(self):
        self.project_root = Path("/app")
        self.vision_completed = [
            "Docker containerization",
            "Basic chat with Ollama",
            "Glass-morphic design", 
            "7 main UI pages",
            "Dual AI personalities",
            "Developer dashboard",
            "System monitoring"
        ]
        self.vision_in_progress = [
            "Memory system (people/projects)",
            "Voice integration (STT/TTS)",
            "N8N workflow automation",
            "Full settings backend"
        ]
        self.vision_planned = [
            "Multi-user support",
            "Wake word detection",
            "Mobile companion app",
            "Document management",
            "Health tracking"
        ]
        
    def analyze_codebase(self) -> Dict[str, Any]:
        """Analyze the actual codebase"""
        analysis = {
            "routers": [],
            "ui_pages": [],
            "database_tables": [],
            "scripts": {"categories": {}, "total": 0},
            "dependencies": [],
            "api_endpoints": []
        }
        
        # Analyze routers
        routers_path = self.project_root / "routers"
        if routers_path.exists():
            for router in routers_path.glob("*.py"):
                if router.name != "__init__.py":
                    analysis["routers"].append(router.stem)
                    
                    # Extract endpoints
                    try:
                        with open(router) as f:
                            content = f.read()
                            import re
                            endpoints = re.findall(r'@router\.(get|post|put|delete)\("([^"]+)"\)', content)
                            for method, path in endpoints:
                                analysis["api_endpoints"].append(f"{method.upper()} {path}")
                    except:
                        pass
        
        # Analyze UI pages
        ui_path = self.project_root.parent / "services/zoe-ui/dist"
        if ui_path.exists():
            for page in ui_path.glob("*.html"):
                analysis["ui_pages"].append(page.stem)
        
        # Analyze database
        try:
            conn = sqlite3.connect("/app/data/zoe.db")
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            analysis["database_tables"] = [t[0] for t in cursor.fetchall()]
            conn.close()
        except:
            pass
        
        # Analyze scripts
        scripts_path = Path(__file__).parent.parent.parent.resolve() / "scripts"
        if scripts_path.exists():
            for category in scripts_path.iterdir():
                if category.is_dir():
                    script_count = len(list(category.glob("*.sh")))
                    analysis["scripts"]["categories"][category.name] = script_count
                    analysis["scripts"]["total"] += script_count
        
        # Analyze dependencies
        req_file = self.project_root / "requirements.txt"
        if req_file.exists():
            with open(req_file) as f:
                analysis["dependencies"] = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        
        return analysis
    
    def identify_gaps(self, codebase: Dict[str, Any]) -> List[Dict[str, str]]:
        """Identify gaps between vision and implementation"""
        gaps = []
        
        # Check for missing routers
        expected_routers = ["chat", "developer", "calendar", "lists", "memories", "workflows", "settings"]
        for router in expected_routers:
            if router not in codebase["routers"]:
                gaps.append({
                    "type": "missing_router",
                    "item": f"{router}.py",
                    "impact": "high",
                    "suggestion": f"Create router for {router} functionality"
                })
        
        # Check for missing UI pages
        expected_pages = ["index", "dashboard", "calendar", "lists", "memories", "workflows", "settings"]
        for page in expected_pages:
            if page not in codebase["ui_pages"]:
                gaps.append({
                    "type": "missing_ui",
                    "item": f"{page}.html",
                    "impact": "high",
                    "suggestion": f"Create UI page for {page}"
                })
        
        # Check for missing tables
        expected_tables = ["events", "tasks", "lists", "memories", "users", "settings"]
        for table in expected_tables:
            if table not in codebase.get("database_tables", []):
                gaps.append({
                    "type": "missing_table",
                    "item": table,
                    "impact": "medium",
                    "suggestion": f"Create database table for {table}"
                })
        
        # Check for incomplete features
        if "memories" in codebase["routers"] but len([e for e in codebase["api_endpoints"] if "memor" in e.lower()]) < 3:
            gaps.append({
                "type": "incomplete_feature",
                "item": "Memory system",
                "impact": "high",
                "suggestion": "Memory system exists but needs CRUD operations for people/projects"
            })
        
        return gaps
    
    def suggest_improvements(self, codebase: Dict[str, Any]) -> List[Dict[str, str]]:
        """Suggest improvements based on analysis"""
        improvements = []
        
        # Performance improvements
        if len(codebase["api_endpoints"]) > 50:
            improvements.append({
                "category": "performance",
                "title": "Add Redis caching",
                "description": "With 50+ endpoints, implement Redis caching for frequently accessed data",
                "priority": "high",
                "implementation": "Add @cache decorator to GET endpoints"
            })
        
        # Architecture improvements
        if "tasks" in codebase["database_tables"] and "task_queue" not in codebase["database_tables"]:
            improvements.append({
                "category": "architecture",
                "title": "Implement task queue",
                "description": "Add proper task queue for async operations",
                "priority": "medium",
                "implementation": "Create task_queue table and background worker"
            })
        
        # Security improvements
        if "users" not in codebase["database_tables"]:
            improvements.append({
                "category": "security",
                "title": "Add user authentication",
                "description": "Implement user system for multi-user support",
                "priority": "high",
                "implementation": "Create users table, JWT auth, session management"
            })
        
        # Feature completeness
        if "voice" not in " ".join(codebase["routers"]):
            improvements.append({
                "category": "features",
                "title": "Complete voice integration",
                "description": "STT/TTS containers exist but no API integration",
                "priority": "medium",
                "implementation": "Create voice router with WebSocket for streaming"
            })
        
        # Code quality
        if codebase["scripts"]["total"] > 30 and "testing" not in codebase["scripts"]["categories"]:
            improvements.append({
                "category": "quality",
                "title": "Add automated testing",
                "description": "30+ scripts but no test suite",
                "priority": "medium",
                "implementation": "Create pytest suite for API endpoints"
            })
        
        return improvements
    
    def generate_report(self) -> Dict[str, Any]:
        """Generate complete project analysis report"""
        codebase = self.analyze_codebase()
        gaps = self.identify_gaps(codebase)
        improvements = self.suggest_improvements(codebase)
        
        # Calculate project health score
        completeness_score = len(self.vision_completed) / (len(self.vision_completed) + len(self.vision_in_progress) + len(self.vision_planned)) * 100
        
        return {
            "project_health": {
                "completeness": f"{completeness_score:.1f}%",
                "routers": len(codebase["routers"]),
                "ui_pages": len(codebase["ui_pages"]),
                "database_tables": len(codebase["database_tables"]),
                "api_endpoints": len(codebase["api_endpoints"]),
                "scripts": codebase["scripts"]["total"]
            },
            "vision_status": {
                "completed": self.vision_completed,
                "in_progress": self.vision_in_progress,
                "planned": self.vision_planned
            },
            "gaps": gaps,
            "improvements": improvements,
            "top_priorities": self._get_top_priorities(gaps, improvements)
        }
    
    def _get_top_priorities(self, gaps: List, improvements: List) -> List[str]:
        """Determine top priorities"""
        priorities = []
        
        # High impact gaps first
        for gap in gaps:
            if gap["impact"] == "high":
                priorities.append(f"Fix: {gap['suggestion']}")
        
        # High priority improvements
        for imp in improvements:
            if imp["priority"] == "high":
                priorities.append(f"Implement: {imp['title']}")
        
        return priorities[:5]  # Top 5 priorities

# Global analyzer instance
project_analyzer = ProjectAnalyzer()
