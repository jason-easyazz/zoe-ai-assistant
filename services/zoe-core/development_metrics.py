"""
Development Metrics Dashboard for Zoe AI Assistant
Track development velocity, task completion rates, and system improvements
"""
import sqlite3
import json
import time
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class DevelopmentMetrics:
    """Development metrics tracking and reporting system"""
    
    def __init__(self, db_path: str = "/app/data/zoe.db"):
        self.db_path = db_path
        self.metrics_cache = {}
        self.cache_ttl = 300  # 5 minutes
    
    def get_task_completion_metrics(self, days: int = 30) -> Dict[str, Any]:
        """Get task completion metrics for the specified period"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get date range
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            
            # Total tasks in period
            cursor.execute('''
                SELECT COUNT(*) FROM dynamic_tasks 
                WHERE created_at >= ? AND created_at <= ?
            ''', (start_date.isoformat(), end_date.isoformat()))
            total_tasks = cursor.fetchone()[0]
            
            # Completed tasks in period
            cursor.execute('''
                SELECT COUNT(*) FROM dynamic_tasks 
                WHERE created_at >= ? AND created_at <= ? AND status = 'completed'
            ''', (start_date.isoformat(), end_date.isoformat()))
            completed_tasks = cursor.fetchone()[0]
            
            # Tasks by priority
            cursor.execute('''
                SELECT priority, COUNT(*) FROM dynamic_tasks 
                WHERE created_at >= ? AND created_at <= ?
                GROUP BY priority
            ''', (start_date.isoformat(), end_date.isoformat()))
            tasks_by_priority = dict(cursor.fetchall())
            
            # Completion rate by priority
            cursor.execute('''
                SELECT priority, 
                       COUNT(*) as total,
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
                FROM dynamic_tasks 
                WHERE created_at >= ? AND created_at <= ?
                GROUP BY priority
            ''', (start_date.isoformat(), end_date.isoformat()))
            
            priority_completion = {}
            for row in cursor.fetchall():
                priority, total, completed = row
                completion_rate = (completed / total * 100) if total > 0 else 0
                priority_completion[priority] = {
                    "total": total,
                    "completed": completed,
                    "completion_rate": round(completion_rate, 2)
                }
            
            # Daily completion trend
            cursor.execute('''
                SELECT DATE(completed_at) as date, COUNT(*) as completed
                FROM dynamic_tasks 
                WHERE completed_at >= ? AND completed_at <= ? AND status = 'completed'
                GROUP BY DATE(completed_at)
                ORDER BY date
            ''', (start_date.isoformat(), end_date.isoformat()))
            
            daily_trend = [{"date": row[0], "completed": row[1]} for row in cursor.fetchall()]
            
            # Calculate completion rate
            completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
            
            return {
                "period_days": days,
                "total_tasks": total_tasks,
                "completed_tasks": completed_tasks,
                "completion_rate": round(completion_rate, 2),
                "tasks_by_priority": tasks_by_priority,
                "priority_completion": priority_completion,
                "daily_trend": daily_trend,
                "average_daily_completion": round(completed_tasks / days, 2) if days > 0 else 0
            }
            
        except Exception as e:
            logger.error(f"Failed to get task completion metrics: {e}")
            return {"error": str(e)}
        finally:
            if 'conn' in locals():
                conn.close()
    
    def get_code_quality_metrics(self) -> Dict[str, Any]:
        """Get code quality metrics"""
        try:
            # This would integrate with code analysis tools
            # For now, we'll provide basic metrics based on task data
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get tasks with code-related keywords
            code_keywords = ["code", "implement", "fix", "refactor", "optimize", "bug"]
            
            code_tasks = 0
            code_tasks_completed = 0
            
            for keyword in code_keywords:
                cursor.execute('''
                    SELECT COUNT(*), 
                           SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END)
                    FROM dynamic_tasks 
                    WHERE (title LIKE ? OR objective LIKE ?) 
                    AND created_at >= datetime('now', '-30 days')
                ''', (f"%{keyword}%", f"%{keyword}%"))
                
                total, completed = cursor.fetchone()
                code_tasks += total
                code_tasks_completed += completed or 0
            
            # Calculate code quality score (simplified)
            code_completion_rate = (code_tasks_completed / code_tasks * 100) if code_tasks > 0 else 0
            
            # Get tasks with testing keywords
            cursor.execute('''
                SELECT COUNT(*) FROM dynamic_tasks 
                WHERE (title LIKE '%test%' OR objective LIKE '%test%')
                AND created_at >= datetime('now', '-30 days')
            ''')
            test_tasks = cursor.fetchone()[0]
            
            return {
                "code_tasks_total": code_tasks,
                "code_tasks_completed": code_tasks_completed,
                "code_completion_rate": round(code_completion_rate, 2),
                "test_tasks": test_tasks,
                "quality_score": min(100, round(code_completion_rate + (test_tasks * 5), 2))
            }
            
        except Exception as e:
            logger.error(f"Failed to get code quality metrics: {e}")
            return {"error": str(e)}
        finally:
            if 'conn' in locals():
                conn.close()
    
    def get_system_improvement_metrics(self) -> Dict[str, Any]:
        """Get system improvement metrics"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get improvement-related tasks
            improvement_keywords = ["improve", "enhance", "optimize", "upgrade", "fix", "refactor"]
            
            improvement_tasks = 0
            improvement_completed = 0
            
            for keyword in improvement_keywords:
                cursor.execute('''
                    SELECT COUNT(*), 
                           SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END)
                    FROM dynamic_tasks 
                    WHERE (title LIKE ? OR objective LIKE ?)
                    AND created_at >= datetime('now', '-30 days')
                ''', (f"%{keyword}%", f"%{keyword}%"))
                
                total, completed = cursor.fetchone()
                improvement_tasks += total
                improvement_completed += completed or 0
            
            # Get new feature tasks
            cursor.execute('''
                SELECT COUNT(*), 
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END)
                FROM dynamic_tasks 
                WHERE (title LIKE '%feature%' OR title LIKE '%add%' OR title LIKE '%create%')
                AND created_at >= datetime('now', '-30 days')
            ''')
            
            feature_total, feature_completed = cursor.fetchone()
            
            # Calculate improvement velocity
            improvement_rate = (improvement_completed / improvement_tasks * 100) if improvement_tasks > 0 else 0
            feature_rate = (feature_completed / feature_total * 100) if feature_total > 0 else 0
            
            return {
                "improvement_tasks": improvement_tasks,
                "improvement_completed": improvement_completed,
                "improvement_rate": round(improvement_rate, 2),
                "feature_tasks": feature_total,
                "feature_completed": feature_completed,
                "feature_rate": round(feature_rate, 2),
                "overall_velocity": round((improvement_rate + feature_rate) / 2, 2)
            }
            
        except Exception as e:
            logger.error(f"Failed to get system improvement metrics: {e}")
            return {"error": str(e)}
        finally:
            if 'conn' in locals():
                conn.close()
    
    def get_weekly_report(self) -> Dict[str, Any]:
        """Generate weekly development report"""
        try:
            # Get metrics for the past week
            task_metrics = self.get_task_completion_metrics(7)
            code_metrics = self.get_code_quality_metrics()
            improvement_metrics = self.get_system_improvement_metrics()
            
            # Calculate trends (simplified)
            current_week = task_metrics.get("completed_tasks", 0)
            
            # Get previous week for comparison
            prev_week_metrics = self.get_task_completion_metrics(14)
            prev_week = prev_week_metrics.get("completed_tasks", 0) - current_week
            
            trend = "up" if current_week > prev_week else "down" if current_week < prev_week else "stable"
            trend_percentage = abs(((current_week - prev_week) / prev_week * 100)) if prev_week > 0 else 0
            
            # Generate insights
            insights = []
            
            if task_metrics.get("completion_rate", 0) > 80:
                insights.append("Excellent task completion rate this week!")
            elif task_metrics.get("completion_rate", 0) < 50:
                insights.append("Task completion rate could be improved.")
            
            if code_metrics.get("quality_score", 0) > 90:
                insights.append("Code quality is excellent.")
            elif code_metrics.get("quality_score", 0) < 70:
                insights.append("Consider focusing on code quality improvements.")
            
            if improvement_metrics.get("overall_velocity", 0) > 75:
                insights.append("Great system improvement velocity!")
            
            return {
                "week_ending": datetime.now().strftime("%Y-%m-%d"),
                "task_metrics": task_metrics,
                "code_metrics": code_metrics,
                "improvement_metrics": improvement_metrics,
                "trend": {
                    "direction": trend,
                    "percentage": round(trend_percentage, 2),
                    "current_week": current_week,
                    "previous_week": prev_week
                },
                "insights": insights,
                "recommendations": self._generate_recommendations(task_metrics, code_metrics, improvement_metrics)
            }
            
        except Exception as e:
            logger.error(f"Failed to generate weekly report: {e}")
            return {"error": str(e)}
    
    def _generate_recommendations(self, task_metrics: Dict, code_metrics: Dict, improvement_metrics: Dict) -> List[str]:
        """Generate recommendations based on metrics"""
        recommendations = []
        
        # Task completion recommendations
        if task_metrics.get("completion_rate", 0) < 60:
            recommendations.append("Focus on completing more tasks - consider breaking down large tasks")
        
        # Priority-based recommendations
        priority_completion = task_metrics.get("priority_completion", {})
        for priority, data in priority_completion.items():
            if data.get("completion_rate", 0) < 50:
                recommendations.append(f"Improve completion rate for {priority} priority tasks")
        
        # Code quality recommendations
        if code_metrics.get("test_tasks", 0) < 5:
            recommendations.append("Increase testing activities - add more test-related tasks")
        
        if code_metrics.get("quality_score", 0) < 80:
            recommendations.append("Focus on code quality improvements and refactoring")
        
        # System improvement recommendations
        if improvement_metrics.get("improvement_rate", 0) < 60:
            recommendations.append("Increase focus on system improvements and optimizations")
        
        if improvement_metrics.get("feature_rate", 0) < 50:
            recommendations.append("Consider balancing new features with system improvements")
        
        return recommendations
    
    def get_velocity_trends(self, weeks: int = 8) -> Dict[str, Any]:
        """Get velocity trends over multiple weeks"""
        try:
            trends = []
            
            for week in range(weeks):
                week_start = datetime.now() - timedelta(weeks=week+1)
                week_end = datetime.now() - timedelta(weeks=week)
                
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                cursor.execute('''
                    SELECT COUNT(*) FROM dynamic_tasks 
                    WHERE created_at >= ? AND created_at < ? AND status = 'completed'
                ''', (week_start.isoformat(), week_end.isoformat()))
                
                completed = cursor.fetchone()[0]
                
                trends.append({
                    "week": f"Week {weeks - week}",
                    "completed_tasks": completed,
                    "date_range": f"{week_start.strftime('%Y-%m-%d')} to {week_end.strftime('%Y-%m-%d')}"
                })
                
                conn.close()
            
            # Calculate trend analysis
            recent_weeks = [t["completed_tasks"] for t in trends[:4]]
            older_weeks = [t["completed_tasks"] for t in trends[4:]]
            
            recent_avg = sum(recent_weeks) / len(recent_weeks) if recent_weeks else 0
            older_avg = sum(older_weeks) / len(older_weeks) if older_weeks else 0
            
            velocity_trend = "improving" if recent_avg > older_avg else "declining" if recent_avg < older_avg else "stable"
            
            return {
                "weeks_analyzed": weeks,
                "trends": list(reversed(trends)),
                "recent_average": round(recent_avg, 2),
                "historical_average": round(older_avg, 2),
                "velocity_trend": velocity_trend,
                "trend_percentage": round(((recent_avg - older_avg) / older_avg * 100), 2) if older_avg > 0 else 0
            }
            
        except Exception as e:
            logger.error(f"Failed to get velocity trends: {e}")
            return {"error": str(e)}
    
    def get_team_productivity_metrics(self) -> Dict[str, Any]:
        """Get team productivity metrics (simplified for single user)"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get tasks by assigned user
            cursor.execute('''
                SELECT assigned_to, 
                       COUNT(*) as total_tasks,
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_tasks,
                       AVG(execution_count) as avg_executions
                FROM dynamic_tasks 
                WHERE created_at >= datetime('now', '-30 days')
                GROUP BY assigned_to
            ''')
            
            user_metrics = {}
            for row in cursor.fetchall():
                user, total, completed, avg_exec = row
                completion_rate = (completed / total * 100) if total > 0 else 0
                
                user_metrics[user] = {
                    "total_tasks": total,
                    "completed_tasks": completed,
                    "completion_rate": round(completion_rate, 2),
                    "average_executions": round(avg_exec or 0, 2)
                }
            
            # Calculate overall productivity score
            total_tasks = sum(data["total_tasks"] for data in user_metrics.values())
            total_completed = sum(data["completed_tasks"] for data in user_metrics.values())
            overall_completion = (total_completed / total_tasks * 100) if total_tasks > 0 else 0
            
            return {
                "user_metrics": user_metrics,
                "overall_metrics": {
                    "total_tasks": total_tasks,
                    "total_completed": total_completed,
                    "overall_completion_rate": round(overall_completion, 2)
                },
                "productivity_score": min(100, round(overall_completion + (total_completed * 2), 2))
            }
            
        except Exception as e:
            logger.error(f"Failed to get team productivity metrics: {e}")
            return {"error": str(e)}
        finally:
            if 'conn' in locals():
                conn.close()
    
    def get_dashboard_summary(self) -> Dict[str, Any]:
        """Get comprehensive dashboard summary"""
        try:
            # Get all metrics
            task_metrics = self.get_task_completion_metrics(30)
            code_metrics = self.get_code_quality_metrics()
            improvement_metrics = self.get_system_improvement_metrics()
            velocity_trends = self.get_velocity_trends(4)
            productivity_metrics = self.get_team_productivity_metrics()
            
            # Calculate overall health score
            health_components = [
                task_metrics.get("completion_rate", 0),
                code_metrics.get("quality_score", 0),
                improvement_metrics.get("overall_velocity", 0),
                productivity_metrics.get("productivity_score", 0)
            ]
            
            health_score = sum(health_components) / len(health_components)
            
            return {
                "timestamp": datetime.now().isoformat(),
                "health_score": round(health_score, 2),
                "task_metrics": task_metrics,
                "code_metrics": code_metrics,
                "improvement_metrics": improvement_metrics,
                "velocity_trends": velocity_trends,
                "productivity_metrics": productivity_metrics,
                "status": "healthy" if health_score > 80 else "needs_attention" if health_score > 60 else "critical"
            }
            
        except Exception as e:
            logger.error(f"Failed to get dashboard summary: {e}")
            return {"error": str(e)}

# Global instance
development_metrics = DevelopmentMetrics()
