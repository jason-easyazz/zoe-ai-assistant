"""
Feature Request Pipeline for Zoe AI Assistant
Converts natural language requests to structured tasks automatically
"""
import re
import json
import sqlite3
import uuid
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class FeatureRequestPipeline:
    """Pipeline for converting user requests to development tasks"""
    
    def __init__(self, db_path: str = "/app/data/developer_tasks.db"):
        self.db_path = db_path
        self.complexity_keywords = {
            "simple": ["add", "create", "implement", "basic", "simple", "quick"],
            "medium": ["build", "develop", "enhance", "improve", "modify", "update"],
            "complex": ["refactor", "redesign", "optimize", "integrate", "migrate", "restructure"],
            "critical": ["fix", "urgent", "critical", "security", "bug", "error"]
        }
        
        self.priority_keywords = {
            "critical": ["urgent", "critical", "security", "bug", "error", "broken", "down"],
            "high": ["important", "priority", "asap", "soon", "needed"],
            "medium": ["enhance", "improve", "feature", "add", "new"],
            "low": ["nice", "optional", "future", "later", "someday"]
        }
        
        self.task_templates = {
            "api": {
                "title_template": "Implement {feature} API",
                "objective_template": "Create API endpoint for {feature}",
                "requirements_template": [
                    "Define API endpoint structure",
                    "Implement request/response models",
                    "Add input validation",
                    "Include error handling",
                    "Add API documentation"
                ]
            },
            "ui": {
                "title_template": "Create {feature} UI Component",
                "objective_template": "Build user interface for {feature}",
                "requirements_template": [
                    "Design responsive layout",
                    "Implement user interactions",
                    "Add form validation",
                    "Include accessibility features",
                    "Test across browsers"
                ]
            },
            "database": {
                "title_template": "Add {feature} Database Support",
                "objective_template": "Implement database functionality for {feature}",
                "requirements_template": [
                    "Design database schema",
                    "Create migration scripts",
                    "Implement CRUD operations",
                    "Add data validation",
                    "Include backup/restore"
                ]
            },
            "integration": {
                "title_template": "Integrate {feature}",
                "objective_template": "Connect external service for {feature}",
                "requirements_template": [
                    "Research API documentation",
                    "Implement authentication",
                    "Handle rate limiting",
                    "Add error handling",
                    "Include monitoring"
                ]
            },
            "feature": {
                "title_template": "Implement {feature}",
                "objective_template": "Add new feature: {feature}",
                "requirements_template": [
                    "Analyze requirements",
                    "Design implementation",
                    "Write code",
                    "Add tests",
                    "Update documentation"
                ]
            }
        }
    
    def parse_natural_language_request(self, request: str) -> Dict[str, Any]:
        """Parse natural language request and extract key information"""
        request_lower = request.lower()
        
        # Extract feature name
        feature_name = self._extract_feature_name(request)
        
        # Determine task type
        task_type = self._determine_task_type(request_lower)
        
        # Estimate complexity
        complexity = self._estimate_complexity(request_lower)
        
        # Determine priority
        priority = self._determine_priority(request_lower)
        
        # Extract requirements
        requirements = self._extract_requirements(request, task_type)
        
        # Generate constraints
        constraints = self._generate_constraints(task_type, complexity)
        
        # Generate acceptance criteria
        acceptance_criteria = self._generate_acceptance_criteria(task_type, feature_name)
        
        return {
            "feature_name": feature_name,
            "task_type": task_type,
            "complexity": complexity,
            "priority": priority,
            "requirements": requirements,
            "constraints": constraints,
            "acceptance_criteria": acceptance_criteria,
            "original_request": request
        }
    
    def _extract_feature_name(self, request: str) -> str:
        """Extract the main feature name from the request"""
        # Remove common prefixes
        prefixes = ["i want", "i need", "can you", "please", "add", "create", "implement", "build"]
        cleaned = request.lower()
        
        for prefix in prefixes:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):].strip()
                break
        
        # Extract first meaningful phrase
        words = cleaned.split()
        if words:
            # Take first 2-4 words as feature name
            feature_words = words[:min(4, len(words))]
            return " ".join(feature_words).title()
        
        return "New Feature"
    
    def _determine_task_type(self, request_lower: str) -> str:
        """Determine the type of task based on keywords"""
        if any(word in request_lower for word in ["api", "endpoint", "rest", "json"]):
            return "api"
        elif any(word in request_lower for word in ["ui", "interface", "frontend", "page", "form", "button"]):
            return "ui"
        elif any(word in request_lower for word in ["database", "db", "table", "schema", "data"]):
            return "database"
        elif any(word in request_lower for word in ["integrate", "connect", "external", "service", "api"]):
            return "integration"
        else:
            return "feature"
    
    def _estimate_complexity(self, request_lower: str) -> str:
        """Estimate task complexity based on keywords"""
        for complexity, keywords in self.complexity_keywords.items():
            if any(keyword in request_lower for keyword in keywords):
                return complexity
        
        # Default based on request length and complexity indicators
        if len(request_lower.split()) > 20 or any(word in request_lower for word in ["system", "architecture", "multiple"]):
            return "complex"
        elif len(request_lower.split()) > 10:
            return "medium"
        else:
            return "simple"
    
    def _determine_priority(self, request_lower: str) -> str:
        """Determine task priority based on keywords"""
        for priority, keywords in self.priority_keywords.items():
            if any(keyword in request_lower for keyword in keywords):
                return priority
        
        return "medium"
    
    def _extract_requirements(self, request: str, task_type: str) -> List[str]:
        """Extract specific requirements from the request"""
        requirements = []
        
        # Get base requirements from template
        if task_type in self.task_templates:
            requirements.extend(self.task_templates[task_type]["requirements_template"])
        
        # Extract specific requirements from request
        request_lower = request.lower()
        
        if "authentication" in request_lower or "login" in request_lower:
            requirements.append("Implement user authentication")
        
        if "validation" in request_lower or "validate" in request_lower:
            requirements.append("Add input validation")
        
        if "test" in request_lower or "testing" in request_lower:
            requirements.append("Write comprehensive tests")
        
        if "documentation" in request_lower or "docs" in request_lower:
            requirements.append("Create documentation")
        
        if "error" in request_lower or "handling" in request_lower:
            requirements.append("Implement error handling")
        
        if "performance" in request_lower or "optimize" in request_lower:
            requirements.append("Optimize for performance")
        
        return requirements
    
    def _generate_constraints(self, task_type: str, complexity: str) -> List[str]:
        """Generate constraints based on task type and complexity"""
        constraints = [
            "Follow existing code patterns and conventions",
            "Maintain backward compatibility",
            "Include proper error handling"
        ]
        
        if task_type == "api":
            constraints.extend([
                "Use FastAPI for API endpoints",
                "Include proper HTTP status codes",
                "Add request/response validation"
            ])
        
        elif task_type == "ui":
            constraints.extend([
                "Ensure responsive design",
                "Follow accessibility guidelines",
                "Test across different browsers"
            ])
        
        elif task_type == "database":
            constraints.extend([
                "Use SQLite for development",
                "Include migration scripts",
                "Add data validation"
            ])
        
        if complexity == "complex":
            constraints.append("Break down into smaller, manageable tasks")
        
        return constraints
    
    def _generate_acceptance_criteria(self, task_type: str, feature_name: str) -> List[str]:
        """Generate acceptance criteria for the task"""
        criteria = [
            f"Feature '{feature_name}' is fully functional",
            "Code follows project standards",
            "All tests pass",
            "Documentation is updated"
        ]
        
        if task_type == "api":
            criteria.extend([
                "API endpoint responds correctly",
                "Request/response validation works",
                "Error handling is comprehensive"
            ])
        
        elif task_type == "ui":
            criteria.extend([
                "UI is responsive and accessible",
                "User interactions work as expected",
                "Visual design is consistent"
            ])
        
        elif task_type == "database":
            criteria.extend([
                "Database schema is properly designed",
                "CRUD operations work correctly",
                "Data integrity is maintained"
            ])
        
        return criteria
    
    def create_task_from_request(self, request: str, user_id: str = "system") -> Dict[str, Any]:
        """Create a task from a natural language request"""
        try:
            # Parse the request
            parsed = self.parse_natural_language_request(request)
            
            # Generate task details
            task_type = parsed["task_type"]
            feature_name = parsed["feature_name"]
            
            if task_type in self.task_templates:
                template = self.task_templates[task_type]
                title = template["title_template"].format(feature=feature_name)
                objective = template["objective_template"].format(feature=feature_name)
            else:
                title = f"Implement {feature_name}"
                objective = f"Add new feature: {feature_name}"
            
            # Create task ID
            task_id = str(uuid.uuid4())[:8]
            
            # Check for duplicates
            if self._is_duplicate_task(title, parsed["requirements"]):
                return {
                    "error": "Duplicate task detected",
                    "suggestion": "A similar task may already exist. Please check existing tasks."
                }
            
            # Create task record
            task_data = {
                "id": task_id,
                "title": title,
                "objective": objective,
                "requirements": json.dumps(parsed["requirements"]),
                "constraints": json.dumps(parsed["constraints"]),
                "acceptance_criteria": json.dumps(parsed["acceptance_criteria"]),
                "priority": parsed["priority"],
                "status": "pending",
                "assigned_to": user_id,
                "created_at": datetime.now().isoformat(),
                "execution_count": 0,
                "original_request": parsed["original_request"],
                "task_type": task_type,
                "complexity": parsed["complexity"]
            }
            
            # Save to database
            self._save_task_to_database(task_data)
            
            return {
                "success": True,
                "task_id": task_id,
                "task_data": task_data,
                "message": f"Task '{title}' created successfully"
            }
            
        except Exception as e:
            logger.error(f"Failed to create task from request: {e}")
            return {
                "error": f"Failed to create task: {str(e)}"
            }
    
    def _is_duplicate_task(self, title: str, requirements: List[str]) -> bool:
        """Check if a similar task already exists"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Check for similar titles
            cursor.execute('''
                SELECT title FROM dynamic_tasks 
                WHERE title LIKE ? AND status != 'completed'
            ''', (f"%{title.split()[0]}%",))
            
            existing_titles = cursor.fetchall()
            
            # Simple similarity check
            for existing_title, in existing_titles:
                if self._calculate_similarity(title.lower(), existing_title.lower()) > 0.7:
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to check for duplicates: {e}")
            return False
        finally:
            if 'conn' in locals():
                conn.close()
    
    def _calculate_similarity(self, str1: str, str2: str) -> float:
        """Calculate simple string similarity"""
        words1 = set(str1.split())
        words2 = set(str2.split())
        
        if not words1 and not words2:
            return 1.0
        
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        
        return len(intersection) / len(union) if union else 0.0
    
    def _save_task_to_database(self, task_data: Dict[str, Any]) -> bool:
        """Save task to database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO dynamic_tasks 
                (id, title, objective, requirements, constraints, acceptance_criteria, 
                 priority, status, assigned_to, created_at, execution_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                task_data["id"],
                task_data["title"],
                task_data["objective"],
                task_data["requirements"],
                task_data["constraints"],
                task_data["acceptance_criteria"],
                task_data["priority"],
                task_data["status"],
                task_data["assigned_to"],
                task_data["created_at"],
                task_data["execution_count"]
            ))
            
            conn.commit()
            return True
            
        except Exception as e:
            logger.error(f"Failed to save task to database: {e}")
            return False
        finally:
            if 'conn' in locals():
                conn.close()
    
    def get_pipeline_stats(self) -> Dict[str, Any]:
        """Get statistics about the feature request pipeline"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get total tasks created by pipeline
            cursor.execute('''
                SELECT COUNT(*) FROM dynamic_tasks 
                WHERE assigned_to = 'system' AND created_at > datetime('now', '-7 days')
            ''')
            recent_tasks = cursor.fetchone()[0]
            
            # Get tasks by priority
            cursor.execute('''
                SELECT priority, COUNT(*) FROM dynamic_tasks 
                WHERE assigned_to = 'system' AND created_at > datetime('now', '-7 days')
                GROUP BY priority
            ''')
            priority_stats = dict(cursor.fetchall())
            
            # Get tasks by type
            cursor.execute('''
                SELECT task_type, COUNT(*) FROM dynamic_tasks 
                WHERE assigned_to = 'system' AND created_at > datetime('now', '-7 days')
                GROUP BY task_type
            ''')
            type_stats = dict(cursor.fetchall())
            
            return {
                "recent_tasks": recent_tasks,
                "priority_distribution": priority_stats,
                "type_distribution": type_stats,
                "pipeline_active": True
            }
            
        except Exception as e:
            logger.error(f"Failed to get pipeline stats: {e}")
            return {"error": str(e)}
        finally:
            if 'conn' in locals():
                conn.close()

# Global instance
feature_request_pipeline = FeatureRequestPipeline()
