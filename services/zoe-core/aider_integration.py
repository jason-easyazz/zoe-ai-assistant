"""
Aider Integration for Zoe AI Assistant
AI pair programming capability using Aider
"""
import os
import subprocess
import json
import tempfile
from typing import Dict, List, Any, Optional
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class AiderIntegration:
    """Integration wrapper for Aider AI pair programming tool"""
    
    def __init__(self):
        # Check if running in container or host
        if os.path.exists("/app"):
            # Running in container
            self.aider_path = "python3"  # Use system Python
            self.aider_module = "aider"  # Use installed Aider module
            self.workspace_root = "/home/pi/zoe"
        else:
            # Running on host
            self.aider_path = "/home/pi/zoe/aider_env/bin/aider"
            self.aider_module = None
            self.workspace_root = "/home/pi/zoe"
        self.max_memory_mb = 400  # Memory limit for Aider
        
    def generate_code(self, request: str, context_files: List[str] = None, 
                     model: str = "ollama/llama3.2") -> Dict[str, Any]:
        """Generate code using Aider"""
        try:
            # Create temporary workspace
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_workspace = Path(temp_dir)
                
                # Copy context files to temp workspace
                if context_files:
                    for file_path in context_files:
                        if os.path.exists(file_path):
                            rel_path = os.path.relpath(file_path, self.workspace_root)
                            dest_path = temp_workspace / rel_path
                            dest_path.parent.mkdir(parents=True, exist_ok=True)
                            
                            # Copy file
                            with open(file_path, 'r', encoding='utf-8') as src:
                                with open(dest_path, 'w', encoding='utf-8') as dst:
                                    dst.write(src.read())
                
                # Create Aider configuration
                aider_config = {
                    "model": model,
                    "max_memory_mb": self.max_memory_mb,
                    "workspace": str(temp_workspace),
                    "auto_commits": False,
                    "dirty_commits": False,
                    "show_diffs": False,
                    "map_tokens": 0,
                    "max_tokens": 2000
                }
                
                # Write config file
                config_path = temp_workspace / ".aider.conf"
                with open(config_path, 'w') as f:
                    for key, value in aider_config.items():
                        f.write(f"{key} = {value}\n")
                
                # Prepare Aider command
                if self.aider_module:
                    # Use Python module approach
                    cmd = [
                        self.aider_path, "-m", self.aider_module,
                        "--model", model,
                        "--max-memory-mb", str(self.max_memory_mb),
                        "--no-auto-commits",
                        "--no-dirty-commits",
                        "--no-show-diffs",
                        "--map-tokens", "0",
                        "--max-tokens", "2000",
                        "--yes",  # Auto-accept changes
                        "--message", request
                    ]
                else:
                    # Use direct binary approach
                    cmd = [
                        self.aider_path,
                        "--model", model,
                        "--max-memory-mb", str(self.max_memory_mb),
                        "--no-auto-commits",
                        "--no-dirty-commits",
                        "--no-show-diffs",
                        "--map-tokens", "0",
                        "--max-tokens", "2000",
                        "--yes",  # Auto-accept changes
                        "--message", request
                    ]
                
                # Add context files if provided
                if context_files:
                    for file_path in context_files:
                        if os.path.exists(file_path):
                            rel_path = os.path.relpath(file_path, self.workspace_root)
                            cmd.append(str(temp_workspace / rel_path))
                
                # Run Aider
                result = subprocess.run(
                    cmd,
                    cwd=temp_workspace,
                    capture_output=True,
                    text=True,
                    timeout=300,  # 5 minute timeout
                    env={**os.environ, "AIDER_WORKSPACE": str(temp_workspace)}
                )
                
                # Parse results
                if result.returncode == 0:
                    # Extract generated code from output
                    generated_files = self._extract_generated_files(temp_workspace, context_files)
                    
                    return {
                        "success": True,
                        "generated_files": generated_files,
                        "output": result.stdout,
                        "model_used": model
                    }
                else:
                    return {
                        "success": False,
                        "error": result.stderr,
                        "output": result.stdout,
                        "return_code": result.returncode
                    }
                    
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "Aider execution timed out",
                "timeout": True
            }
        except Exception as e:
            logger.error(f"Aider execution failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def _extract_generated_files(self, workspace: Path, context_files: List[str]) -> Dict[str, str]:
        """Extract generated/modified files from workspace"""
        generated_files = {}
        
        try:
            # Walk through workspace and find modified files
            for root, dirs, files in os.walk(workspace):
                for file in files:
                    if file.endswith(('.py', '.js', '.ts', '.html', '.css', '.json', '.yaml', '.yml')):
                        file_path = Path(root) / file
                        rel_path = file_path.relative_to(workspace)
                        
                        # Read file content
                        try:
                            with open(file_path, 'r', encoding='utf-8') as f:
                                content = f.read()
                            
                            generated_files[str(rel_path)] = content
                        except Exception as e:
                            logger.warning(f"Could not read file {file_path}: {e}")
                            
        except Exception as e:
            logger.error(f"Error extracting generated files: {e}")
        
        return generated_files
    
    def suggest_improvements(self, file_path: str, issue_description: str) -> Dict[str, Any]:
        """Suggest code improvements using Aider"""
        try:
            if not os.path.exists(file_path):
                return {"success": False, "error": "File not found"}
            
            # Create improvement request
            request = f"Review this code and suggest improvements for: {issue_description}"
            
            # Generate improvements
            result = self.generate_code(
                request=request,
                context_files=[file_path],
                model="ollama/llama3.2"
            )
            
            if result["success"]:
                # Extract suggestions from the first generated file
                suggestions = []
                for file_content in result["generated_files"].values():
                    suggestions.append({
                        "file": file_path,
                        "suggested_code": file_content,
                        "description": issue_description
                    })
                
                return {
                    "success": True,
                    "suggestions": suggestions,
                    "original_file": file_path
                }
            else:
                return result
                
        except Exception as e:
            logger.error(f"Improvement suggestion failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def refactor_code(self, file_path: str, refactor_description: str) -> Dict[str, Any]:
        """Refactor code using Aider"""
        try:
            if not os.path.exists(file_path):
                return {"success": False, "error": "File not found"}
            
            # Create refactor request
            request = f"Refactor this code: {refactor_description}"
            
            # Generate refactored code
            result = self.generate_code(
                request=request,
                context_files=[file_path],
                model="ollama/llama3.2"
            )
            
            if result["success"]:
                return {
                    "success": True,
                    "refactored_files": result["generated_files"],
                    "original_file": file_path,
                    "refactor_description": refactor_description
                }
            else:
                return result
                
        except Exception as e:
            logger.error(f"Code refactoring failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def generate_tests(self, file_path: str, test_framework: str = "pytest") -> Dict[str, Any]:
        """Generate tests for code using Aider"""
        try:
            if not os.path.exists(file_path):
                return {"success": False, "error": "File not found"}
            
            # Create test generation request
            request = f"Generate comprehensive {test_framework} tests for this code. Include edge cases and error handling."
            
            # Generate tests
            result = self.generate_code(
                request=request,
                context_files=[file_path],
                model="ollama/llama3.2"
            )
            
            if result["success"]:
                # Create test file name
                test_file = f"test_{Path(file_path).name}"
                
                return {
                    "success": True,
                    "test_files": result["generated_files"],
                    "original_file": file_path,
                    "test_framework": test_framework,
                    "suggested_test_file": test_file
                }
            else:
                return result
                
        except Exception as e:
            logger.error(f"Test generation failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_available_models(self) -> List[str]:
        """Get list of available models for Aider"""
        return [
            "ollama/llama3.2",
            "ollama/llama3.1",
            "ollama/codellama",
            "ollama/mistral",
            "openai/gpt-4",
            "openai/gpt-3.5-turbo",
            "anthropic/claude-3-sonnet",
            "anthropic/claude-3-haiku"
        ]
    
    def check_health(self) -> Dict[str, Any]:
        """Check Aider integration health"""
        try:
            # Test Aider installation
            result = subprocess.run(
                [self.aider_path, "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                return {
                    "healthy": True,
                    "version": result.stdout.strip(),
                    "aider_path": self.aider_path,
                    "workspace_root": self.workspace_root,
                    "max_memory_mb": self.max_memory_mb
                }
            else:
                return {
                    "healthy": False,
                    "error": result.stderr,
                    "aider_path": self.aider_path
                }
                
        except Exception as e:
            return {
                "healthy": False,
                "error": str(e),
                "aider_path": self.aider_path
            }

# Global instance
aider_integration = AiderIntegration()
