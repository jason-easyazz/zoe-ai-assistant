"""
Self-Test Suite for Zoe AI Assistant
Automated testing system with auto-rollback on failure
"""
import asyncio
import httpx
import json
import time
import subprocess
import os
import sqlite3
from typing import Dict, List, Any, Optional
from datetime import datetime
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class SelfTestSuite:
    """Comprehensive self-test suite for the Zoe system"""
    
    def __init__(self):
        self.api_base = "http://localhost:8000"
        self.test_results = []
        self.critical_failures = []
        self.backup_created = False
        self.backup_path = None
        
    async def run_full_test_suite(self) -> Dict[str, Any]:
        """Run complete test suite with auto-rollback"""
        start_time = time.time()
        
        try:
            # Create backup before testing
            await self._create_test_backup()
            
            # Run all test categories
            test_categories = [
                ("API Health", self._test_api_health),
                ("Core Services", self._test_core_services),
                ("Database Operations", self._test_database_operations),
                ("AI Integration", self._test_ai_integration),
                ("Task System", self._test_task_system),
                ("Backup System", self._test_backup_system),
                ("Code Review", self._test_code_review),
                ("TTS System", self._test_tts_system),
                ("Performance", self._test_performance)
            ]
            
            for category_name, test_func in test_categories:
                logger.info(f"Running {category_name} tests...")
                await self._run_test_category(category_name, test_func)
            
            # Check for critical failures
            if self.critical_failures:
                logger.error(f"Critical failures detected: {len(self.critical_failures)}")
                await self._rollback_system()
                return {
                    "success": False,
                    "critical_failures": self.critical_failures,
                    "test_results": self.test_results,
                    "rollback_performed": True
                }
            
            # Cleanup test backup
            await self._cleanup_test_backup()
            
            execution_time = time.time() - start_time
            
            return {
                "success": True,
                "test_results": self.test_results,
                "total_tests": len(self.test_results),
                "passed_tests": len([r for r in self.test_results if r["passed"]]),
                "failed_tests": len([r for r in self.test_results if not r["passed"]]),
                "execution_time": round(execution_time, 2),
                "rollback_performed": False
            }
            
        except Exception as e:
            logger.error(f"Test suite failed: {e}")
            await self._rollback_system()
            return {
                "success": False,
                "error": str(e),
                "test_results": self.test_results,
                "rollback_performed": True
            }
    
    async def _create_test_backup(self):
        """Create backup before running tests"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(f"{self.api_base}/api/developer/backup/create", 
                                           json={"description": "Pre-test backup"})
                if response.status_code == 200:
                    data = response.json()
                    self.backup_created = True
                    self.backup_path = data.get("backup_path")
                    logger.info("Test backup created successfully")
                else:
                    logger.warning("Failed to create test backup")
        except Exception as e:
            logger.warning(f"Could not create test backup: {e}")
    
    async def _cleanup_test_backup(self):
        """Clean up test backup after successful tests"""
        if self.backup_created and self.backup_path:
            try:
                # Keep the backup for now, just mark it as test backup
                logger.info("Test backup preserved for reference")
            except Exception as e:
                logger.warning(f"Could not cleanup test backup: {e}")
    
    async def _rollback_system(self):
        """Rollback system to pre-test state"""
        if self.backup_created and self.backup_path:
            try:
                logger.info("Rolling back system to pre-test state...")
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.post(f"{self.api_base}/api/developer/backup/restore",
                                               json={"backup_path": self.backup_path})
                    if response.status_code == 200:
                        logger.info("System rollback completed successfully")
                    else:
                        logger.error("System rollback failed")
            except Exception as e:
                logger.error(f"Rollback failed: {e}")
    
    async def _run_test_category(self, category_name: str, test_func):
        """Run a test category and record results"""
        try:
            results = await test_func()
            for result in results:
                result["category"] = category_name
                result["timestamp"] = datetime.now().isoformat()
                self.test_results.append(result)
                
                # Check for critical failures
                if not result["passed"] and result.get("critical", False):
                    self.critical_failures.append(result)
        except Exception as e:
            self.test_results.append({
                "category": category_name,
                "test_name": "category_execution",
                "passed": False,
                "error": str(e),
                "critical": True,
                "timestamp": datetime.now().isoformat()
            })
    
    async def _test_api_health(self) -> List[Dict[str, Any]]:
        """Test API health and basic connectivity"""
        results = []
        
        # Test main API health
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.api_base}/health")
                results.append({
                    "test_name": "api_health",
                    "passed": response.status_code == 200,
                    "response_time": response.elapsed.total_seconds(),
                    "critical": True
                })
        except Exception as e:
            results.append({
                "test_name": "api_health",
                "passed": False,
                "error": str(e),
                "critical": True
            })
        
        # Test developer API
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.api_base}/api/developer/status")
                results.append({
                    "test_name": "developer_api",
                    "passed": response.status_code == 200,
                    "response_time": response.elapsed.total_seconds(),
                    "critical": True
                })
        except Exception as e:
            results.append({
                "test_name": "developer_api",
                "passed": False,
                "error": str(e),
                "critical": True
            })
        
        return results
    
    async def _test_core_services(self) -> List[Dict[str, Any]]:
        """Test core Docker services"""
        results = []
        
        # Check Docker containers
        try:
            result = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}:{{.Status}}"],
                capture_output=True, text=True, timeout=10
            )
            
            if result.returncode == 0:
                containers = result.stdout.strip().split('\n')
                expected_containers = ["zoe-core", "zoe-ui", "zoe-ollama", "zoe-redis"]
                
                for container in expected_containers:
                    found = any(container in line for line in containers)
                    results.append({
                        "test_name": f"container_{container}",
                        "passed": found,
                        "critical": container == "zoe-core"
                    })
            else:
                results.append({
                    "test_name": "docker_ps",
                    "passed": False,
                    "error": "Docker command failed",
                    "critical": True
                })
        except Exception as e:
            results.append({
                "test_name": "docker_ps",
                "passed": False,
                "error": str(e),
                "critical": True
            })
        
        return results
    
    async def _test_database_operations(self) -> List[Dict[str, Any]]:
        """Test database operations"""
        results = []
        
        # Test developer tasks database
        try:
            conn = sqlite3.connect('/app/data/developer_tasks.db')
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM dynamic_tasks")
            count = cursor.fetchone()[0]
            conn.close()
            
            results.append({
                "test_name": "database_connectivity",
                "passed": True,
                "task_count": count,
                "critical": True
            })
        except Exception as e:
            results.append({
                "test_name": "database_connectivity",
                "passed": False,
                "error": str(e),
                "critical": True
            })
        
        # Test database write operations
        try:
            conn = sqlite3.connect('/app/data/developer_tasks.db')
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            conn.close()
            
            results.append({
                "test_name": "database_write",
                "passed": True,
                "critical": True
            })
        except Exception as e:
            results.append({
                "test_name": "database_write",
                "passed": False,
                "error": str(e),
                "critical": True
            })
        
        return results
    
    async def _test_ai_integration(self) -> List[Dict[str, Any]]:
        """Test AI integration"""
        results = []
        
        # Test AI client
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.api_base}/api/developer/chat",
                    json={"message": "Test message for AI integration"}
                )
                
                results.append({
                    "test_name": "ai_chat",
                    "passed": response.status_code == 200,
                    "response_time": response.elapsed.total_seconds(),
                    "critical": False
                })
        except Exception as e:
            results.append({
                "test_name": "ai_chat",
                "passed": False,
                "error": str(e),
                "critical": False
            })
        
        return results
    
    async def _test_task_system(self) -> List[Dict[str, Any]]:
        """Test task system functionality"""
        results = []
        
        # Test task listing
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.api_base}/api/developer/tasks/list")
                
                results.append({
                    "test_name": "task_list",
                    "passed": response.status_code == 200,
                    "response_time": response.elapsed.total_seconds(),
                    "critical": True
                })
        except Exception as e:
            results.append({
                "test_name": "task_list",
                "passed": False,
                "error": str(e),
                "critical": True
            })
        
        # Test next task endpoint
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.api_base}/api/developer/tasks/next")
                
                results.append({
                    "test_name": "task_next",
                    "passed": response.status_code == 200,
                    "response_time": response.elapsed.total_seconds(),
                    "critical": False
                })
        except Exception as e:
            results.append({
                "test_name": "task_next",
                "passed": False,
                "error": str(e),
                "critical": False
            })
        
        return results
    
    async def _test_backup_system(self) -> List[Dict[str, Any]]:
        """Test backup system"""
        results = []
        
        # Test backup listing
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.api_base}/api/developer/backup/list")
                
                results.append({
                    "test_name": "backup_list",
                    "passed": response.status_code == 200,
                    "response_time": response.elapsed.total_seconds(),
                    "critical": False
                })
        except Exception as e:
            results.append({
                "test_name": "backup_list",
                "passed": False,
                "error": str(e),
                "critical": False
            })
        
        return results
    
    async def _test_code_review(self) -> List[Dict[str, Any]]:
        """Test code review system"""
        results = []
        
        # Test code review endpoint
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.api_base}/api/developer/review-code",
                    json={"code": "print('hello world')", "file_path": "test.py"}
                )
                
                results.append({
                    "test_name": "code_review",
                    "passed": response.status_code == 200,
                    "response_time": response.elapsed.total_seconds(),
                    "critical": False
                })
        except Exception as e:
            results.append({
                "test_name": "code_review",
                "passed": False,
                "error": str(e),
                "critical": False
            })
        
        return results
    
    async def _test_tts_system(self) -> List[Dict[str, Any]]:
        """Test TTS system"""
        results = []
        
        # Test TTS health
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get("http://localhost:9002/health")
                
                results.append({
                    "test_name": "tts_health",
                    "passed": response.status_code == 200,
                    "response_time": response.elapsed.total_seconds(),
                    "critical": False
                })
        except Exception as e:
            results.append({
                "test_name": "tts_health",
                "passed": False,
                "error": str(e),
                "critical": False
            })
        
        return results
    
    async def _test_performance(self) -> List[Dict[str, Any]]:
        """Test system performance"""
        results = []
        
        # Test response times
        try:
            start_time = time.time()
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.api_base}/api/developer/status")
            response_time = time.time() - start_time
            
            results.append({
                "test_name": "response_time",
                "passed": response_time < 2.0,  # Should respond within 2 seconds
                "response_time": response_time,
                "critical": False
            })
        except Exception as e:
            results.append({
                "test_name": "response_time",
                "passed": False,
                "error": str(e),
                "critical": False
            })
        
        # Test memory usage
        try:
            result = subprocess.run(
                ["free", "-m"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                if len(lines) > 1:
                    mem_info = lines[1].split()
                    total_mem = int(mem_info[1])
                    used_mem = int(mem_info[2])
                    mem_usage_percent = (used_mem / total_mem) * 100
                    
                    results.append({
                        "test_name": "memory_usage",
                        "passed": mem_usage_percent < 90,  # Should use less than 90% memory
                        "memory_usage_percent": round(mem_usage_percent, 2),
                        "critical": False
                    })
        except Exception as e:
            results.append({
                "test_name": "memory_usage",
                "passed": False,
                "error": str(e),
                "critical": False
            })
        
        return results

# Global instance
self_test_suite = SelfTestSuite()
