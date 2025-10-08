"""
API Documentation Generator for Zoe AI Assistant
Automatically generates OpenAPI spec and markdown docs from FastAPI routers
"""
import os
import ast
import json
import re
from typing import Dict, List, Any, Optional
from pathlib import Path
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class APIDocGenerator:
    """Generates comprehensive API documentation from FastAPI code"""
    
    def __init__(self):
        self.routers_dir = Path("/app/routers")
        self.output_dir = Path("/app/docs")
        self.output_dir.mkdir(exist_ok=True)
        
        # OpenAPI spec structure
        self.openapi_spec = {
            "openapi": "3.0.0",
            "info": {
                "title": "Zoe AI Assistant API",
                "description": "Comprehensive API for the Zoe AI Assistant system",
                "version": "2.0.0",
                "contact": {
                    "name": "Zoe Development Team",
                    "email": "dev@zoe-ai.local"
                }
            },
            "servers": [
                {
                    "url": "http://localhost:8000",
                    "description": "Development server"
                },
                {
                    "url": "http://192.168.1.60:8000",
                    "description": "Local network server"
                }
            ],
            "paths": {},
            "components": {
                "schemas": {},
                "securitySchemes": {
                    "BearerAuth": {
                        "type": "http",
                        "scheme": "bearer",
                        "bearerFormat": "JWT"
                    }
                }
            },
            "security": [{"BearerAuth": []}]
        }
    
    def generate_documentation(self) -> Dict[str, Any]:
        """Generate complete API documentation"""
        try:
            # Scan all router files
            endpoints = self._scan_routers()
            
            # Generate OpenAPI spec
            self._build_openapi_spec(endpoints)
            
            # Generate markdown documentation
            markdown_docs = self._generate_markdown_docs(endpoints)
            
            # Save files
            self._save_openapi_spec()
            self._save_markdown_docs(markdown_docs)
            
            return {
                "success": True,
                "endpoints_found": len(endpoints),
                "openapi_spec": self.openapi_spec,
                "markdown_docs": markdown_docs,
                "files_created": [
                    str(self.output_dir / "openapi.json"),
                    str(self.output_dir / "api_docs.md")
                ]
            }
            
        except Exception as e:
            logger.error(f"Documentation generation failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def _scan_routers(self) -> List[Dict[str, Any]]:
        """Scan all router files for endpoints"""
        endpoints = []
        
        for router_file in self.routers_dir.glob("*.py"):
            if router_file.name.startswith("__"):
                continue
                
            try:
                with open(router_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Parse the file
                tree = ast.parse(content)
                
                # Extract endpoints
                file_endpoints = self._extract_endpoints_from_ast(tree, router_file.name)
                endpoints.extend(file_endpoints)
                
            except Exception as e:
                logger.warning(f"Could not parse {router_file}: {e}")
                continue
        
        return endpoints
    
    def _extract_endpoints_from_ast(self, tree: ast.AST, filename: str) -> List[Dict[str, Any]]:
        """Extract endpoint information from AST"""
        endpoints = []
        
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                # Look for FastAPI decorators
                decorators = []
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call):
                        if isinstance(decorator.func, ast.Attribute):
                            decorators.append({
                                "type": decorator.func.attr,
                                "path": self._extract_string_literal(decorator.args[0]) if decorator.args else "",
                                "methods": self._extract_methods_from_decorator(decorator)
                            })
                
                if decorators:
                    # Extract function information
                    docstring = ast.get_docstring(node) or ""
                    parameters = self._extract_parameters(node)
                    return_type = self._extract_return_type(node)
                    
                    for decorator in decorators:
                        endpoint = {
                            "file": filename,
                            "function_name": node.name,
                            "path": decorator["path"],
                            "methods": decorator["methods"],
                            "description": docstring,
                            "parameters": parameters,
                            "return_type": return_type,
                            "tags": self._extract_tags(node)
                        }
                        endpoints.append(endpoint)
        
        return endpoints
    
    def _extract_string_literal(self, node: ast.AST) -> str:
        """Extract string literal from AST node"""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        elif isinstance(node, ast.Str):  # Python < 3.8 compatibility
            return node.s
        return ""
    
    def _extract_methods_from_decorator(self, decorator: ast.Call) -> List[str]:
        """Extract HTTP methods from decorator"""
        if isinstance(decorator.func, ast.Attribute):
            method = decorator.func.attr.upper()
            if method in ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]:
                return [method]
        return ["GET"]  # Default
    
    def _extract_parameters(self, node: ast.FunctionDef) -> List[Dict[str, Any]]:
        """Extract function parameters"""
        parameters = []
        
        for arg in node.args.args:
            param_info = {
                "name": arg.arg,
                "type": "str",  # Default type
                "required": True
            }
            
            # Try to extract type annotation
            if arg.annotation:
                param_info["type"] = self._extract_type_annotation(arg.annotation)
            
            # Check if parameter has default value
            if arg.arg in [arg.arg for arg in node.args.defaults]:
                param_info["required"] = False
            
            parameters.append(param_info)
        
        return parameters
    
    def _extract_type_annotation(self, annotation: ast.AST) -> str:
        """Extract type annotation as string"""
        if isinstance(annotation, ast.Name):
            return annotation.id
        elif isinstance(annotation, ast.Constant):
            return str(annotation.value)
        elif isinstance(annotation, ast.Str):  # Python < 3.8 compatibility
            return annotation.s
        return "Any"
    
    def _extract_return_type(self, node: ast.FunctionDef) -> str:
        """Extract return type annotation"""
        if node.returns:
            return self._extract_type_annotation(node.returns)
        return "Any"
    
    def _extract_tags(self, node: ast.FunctionDef) -> List[str]:
        """Extract tags from function docstring or decorators"""
        tags = []
        
        # Look for @router.get("/path", tags=["tag1", "tag2"])
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call):
                for keyword in decorator.keywords:
                    if keyword.arg == "tags" and isinstance(keyword.value, ast.List):
                        for elt in keyword.value.elts:
                            if isinstance(elt, ast.Constant):
                                tags.append(elt.value)
                            elif isinstance(elt, ast.Str):  # Python < 3.8 compatibility
                                tags.append(elt.s)
        
        return tags if tags else ["general"]
    
    def _build_openapi_spec(self, endpoints: List[Dict[str, Any]]):
        """Build OpenAPI specification from endpoints"""
        for endpoint in endpoints:
            path = endpoint["path"]
            if not path:
                continue
            
            if path not in self.openapi_spec["paths"]:
                self.openapi_spec["paths"][path] = {}
            
            for method in endpoint["methods"]:
                method_lower = method.lower()
                
                operation = {
                    "summary": endpoint["function_name"].replace("_", " ").title(),
                    "description": endpoint["description"],
                    "operationId": f"{endpoint['function_name']}_{method_lower}",
                    "tags": endpoint["tags"],
                    "responses": {
                        "200": {
                            "description": "Successful response",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object"
                                    }
                                }
                            }
                        },
                        "400": {
                            "description": "Bad request"
                        },
                        "500": {
                            "description": "Internal server error"
                        }
                    }
                }
                
                # Add parameters
                if endpoint["parameters"]:
                    operation["parameters"] = []
                    for param in endpoint["parameters"]:
                        if param["name"] not in ["self", "request", "response"]:
                            operation["parameters"].append({
                                "name": param["name"],
                                "in": "query" if param["name"] in ["limit", "offset", "status"] else "path",
                                "required": param["required"],
                                "schema": {
                                    "type": param["type"].lower() if param["type"] in ["str", "int", "bool"] else "string"
                                }
                            })
                
                # Add request body for POST/PUT methods
                if method_lower in ["post", "put", "patch"]:
                    operation["requestBody"] = {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "data": {
                                            "type": "object",
                                            "description": "Request payload"
                                        }
                                    }
                                }
                            }
                        }
                    }
                
                self.openapi_spec["paths"][path][method_lower] = operation
    
    def _generate_markdown_docs(self, endpoints: List[Dict[str, Any]]) -> str:
        """Generate markdown documentation"""
        docs = []
        
        # Header
        docs.append("# Zoe AI Assistant API Documentation")
        docs.append("")
        docs.append(f"*Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
        docs.append("")
        docs.append("## Overview")
        docs.append("")
        docs.append("This API provides comprehensive access to the Zoe AI Assistant system, including:")
        docs.append("- Chat and conversation management")
        docs.append("- Task creation and execution")
        docs.append("- System monitoring and metrics")
        docs.append("- Backup and restore operations")
        docs.append("- Code review and validation")
        docs.append("")
        
        # Group endpoints by tags
        endpoints_by_tag = {}
        for endpoint in endpoints:
            for tag in endpoint["tags"]:
                if tag not in endpoints_by_tag:
                    endpoints_by_tag[tag] = []
                endpoints_by_tag[tag].append(endpoint)
        
        # Generate sections for each tag
        for tag, tag_endpoints in endpoints_by_tag.items():
            docs.append(f"## {tag.title()} Endpoints")
            docs.append("")
            
            for endpoint in tag_endpoints:
                if not endpoint["path"]:
                    continue
                
                # Endpoint header
                methods_str = " | ".join(endpoint["methods"])
                docs.append(f"### {methods_str} {endpoint['path']}")
                docs.append("")
                
                if endpoint["description"]:
                    docs.append(f"**Description:** {endpoint['description']}")
                    docs.append("")
                
                # Parameters
                if endpoint["parameters"]:
                    docs.append("**Parameters:**")
                    docs.append("")
                    for param in endpoint["parameters"]:
                        if param["name"] not in ["self", "request", "response"]:
                            required = "Required" if param["required"] else "Optional"
                            docs.append(f"- `{param['name']}` ({param['type']}) - {required}")
                    docs.append("")
                
                # Example request
                if "POST" in endpoint["methods"] or "PUT" in endpoint["methods"]:
                    docs.append("**Example Request:**")
                    docs.append("```bash")
                    docs.append(f"curl -X {endpoint['methods'][0]} http://localhost:8000{endpoint['path']} \\")
                    docs.append("  -H 'Content-Type: application/json' \\")
                    docs.append("  -d '{\"data\": \"example\"}'")
                    docs.append("```")
                    docs.append("")
                else:
                    docs.append("**Example Request:**")
                    docs.append("```bash")
                    docs.append(f"curl -X {endpoint['methods'][0]} http://localhost:8000{endpoint['path']}")
                    docs.append("```")
                    docs.append("")
                
                docs.append("---")
                docs.append("")
        
        # Add general information
        docs.append("## Authentication")
        docs.append("")
        docs.append("Most endpoints require authentication. Include the API key in the Authorization header:")
        docs.append("")
        docs.append("```bash")
        docs.append("curl -H 'Authorization: Bearer YOUR_API_KEY' http://localhost:8000/api/endpoint")
        docs.append("```")
        docs.append("")
        
        docs.append("## Error Handling")
        docs.append("")
        docs.append("The API uses standard HTTP status codes:")
        docs.append("- `200` - Success")
        docs.append("- `400` - Bad Request")
        docs.append("- `401` - Unauthorized")
        docs.append("- `404` - Not Found")
        docs.append("- `500` - Internal Server Error")
        docs.append("")
        
        return "\n".join(docs)
    
    def _save_openapi_spec(self):
        """Save OpenAPI specification to file"""
        spec_path = self.output_dir / "openapi.json"
        with open(spec_path, 'w', encoding='utf-8') as f:
            json.dump(self.openapi_spec, f, indent=2)
    
    def _save_markdown_docs(self, markdown_content: str):
        """Save markdown documentation to file"""
        docs_path = self.output_dir / "api_docs.md"
        with open(docs_path, 'w', encoding='utf-8') as f:
            f.write(markdown_content)
    
    def get_documentation_status(self) -> Dict[str, Any]:
        """Get current documentation status"""
        try:
            openapi_path = self.output_dir / "openapi.json"
            markdown_path = self.output_dir / "api_docs.md"
            
            status = {
                "openapi_exists": openapi_path.exists(),
                "markdown_exists": markdown_path.exists(),
                "last_generated": None,
                "file_sizes": {}
            }
            
            if openapi_path.exists():
                stat = openapi_path.stat()
                status["last_generated"] = datetime.fromtimestamp(stat.st_mtime).isoformat()
                status["file_sizes"]["openapi"] = stat.st_size
            
            if markdown_path.exists():
                stat = markdown_path.stat()
                status["file_sizes"]["markdown"] = stat.st_size
            
            return status
            
        except Exception as e:
            return {"error": str(e)}

# Global instance
api_doc_generator = APIDocGenerator()
