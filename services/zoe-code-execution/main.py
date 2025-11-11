#!/usr/bin/env python3
"""
Zoe Code Execution Service
Secure sandbox for executing agent-generated code with MCP tools
Optimized for speed and reliability
"""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Zoe Code Execution Service")

# Add CORS middleware for fast responses
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class CodeExecutionRequest(BaseModel):
    code: str
    language: str = "typescript"  # typescript or python
    user_id: str
    session_id: Optional[str] = None
    timeout: int = 30  # seconds

class CodeExecutionResponse(BaseModel):
    success: bool
    output: str
    error: Optional[str] = None
    execution_time: float

# MCP server URL
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://zoe-mcp-server:8003")

class CodeExecutor:
    """Secure code executor with MCP tool access - optimized for speed"""
    
    def __init__(self):
        self.workspace_root = Path("/tmp/zoe-code-workspace")
        self.workspace_root.mkdir(exist_ok=True, parents=True)
        # Pre-create common directories for speed
        (self.workspace_root / "default" / "servers").mkdir(parents=True, exist_ok=True)
        
    async def execute_typescript(self, code: str, user_id: str) -> Dict[str, Any]:
        """Execute TypeScript code with MCP tool access - optimized"""
        import time
        start_time = time.time()
        
        # Create user-specific workspace
        user_workspace = self.workspace_root / user_id
        user_workspace.mkdir(exist_ok=True, parents=True)
        
        # Create servers directory structure
        servers_dir = user_workspace / "servers"
        servers_dir.mkdir(exist_ok=True, parents=True)
        
        # Copy tool files if they don't exist (only once per user)
        if not (servers_dir / "zoe-lists" / "index.ts").exists():
            await self._setup_tool_files(servers_dir)
        
        # Ensure MCP client exists
        client_file = user_workspace / "mcp_client.js"
        if not client_file.exists():
            await self._create_mcp_client(client_file)
        
        # Create tsconfig.json for TypeScript (only if needed)
        tsconfig_file = user_workspace / "tsconfig.json"
        if not tsconfig_file.exists():
            tsconfig_file.write_text("""{
  "compilerOptions": {
    "target": "ES2020",
    "module": "ESNext",
    "moduleResolution": "node",
    "esModuleInterop": true,
    "allowSyntheticDefaultImports": true,
    "strict": false,
    "skipLibCheck": true
  }
}""")
        
        # Create package.json (only if needed)
        package_file = user_workspace / "package.json"
        if not package_file.exists():
            package_file.write_text("""{
  "type": "module",
  "dependencies": {}
}""")
        
        # Create the code file
        code_file = user_workspace / "agent_code.ts"
        code_file.write_text(code)
        
        # Execute using tsx (faster TypeScript runner, installed globally)
        try:
            result = subprocess.run(
                ["tsx", str(code_file)],
                cwd=str(user_workspace),
                capture_output=True,
                text=True,
                timeout=30,
                env={
                    **os.environ,
                    "MCP_SERVER_URL": MCP_SERVER_URL,
                    "USER_ID": user_id
                }
            )
            
            execution_time = time.time() - start_time
            
            if result.returncode == 0:
                return {
                    "success": True,
                    "output": result.stdout,
                    "error": None,
                    "execution_time": execution_time
                }
            else:
                return {
                    "success": False,
                    "output": result.stdout,
                    "error": result.stderr,
                    "execution_time": execution_time
                }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "output": "",
                "error": "Execution timeout exceeded",
                "execution_time": time.time() - start_time
            }
        except Exception as e:
            return {
                "success": False,
                "output": "",
                "error": str(e),
                "execution_time": time.time() - start_time
            }
    
    async def _setup_tool_files(self, servers_dir: Path):
        """Set up tool files in servers directory - optimized"""
        # Create all tool categories
        categories = {
            "zoe-lists": ["add_to_list", "get_lists"],
            "zoe-calendar": ["create_calendar_event", "get_calendar_events"],
            "zoe-memory": ["search_memories", "create_person", "get_people"],
            "home-assistant": ["get_home_assistant_devices", "control_home_assistant_device"],
            "n8n": ["get_n8n_workflows", "execute_n8n_workflow"],
            "matrix": ["send_matrix_message", "get_matrix_rooms"]
        }
        
        for category, tools in categories.items():
            cat_dir = servers_dir / category
            cat_dir.mkdir(exist_ok=True)
            
            exports = []
            for tool in tools:
                tool_file = cat_dir / f"{tool}.ts"
                func_name = ''.join(word.capitalize() if i > 0 else word for i, word in enumerate(tool.split('_')))
                func_name = func_name[0].lower() + func_name[1:] if func_name else tool
                
                tool_file.write_text(f"""import {{ callMCPTool }} from '../../mcp_client.js';

export async function {func_name}(input: any) {{
    return callMCPTool('{tool}', input);
}}
""")
                exports.append(f"export {{ {func_name} }} from './{tool}.js';")
            
            # Create index.ts
            index_file = cat_dir / "index.ts"
            index_file.write_text('\n'.join(exports) + '\n')
    
    async def _create_mcp_client(self, client_file: Path):
        """Create MCP client library for code execution"""
        client_code = f"""
import https from 'https';
import http from 'http';

const MCP_SERVER_URL = process.env.MCP_SERVER_URL || '{MCP_SERVER_URL}';
const USER_ID = process.env.USER_ID || 'default';

export async function callMCPTool(toolName, input) {{
    const url = new URL(`${{MCP_SERVER_URL}}/tools/${{toolName}}`);
    
    const requestData = {{
        ...input,
        user_id: USER_ID,
        _auth_token: 'default',
        _session_id: 'default'
    }};
    
    const options = {{
        method: 'POST',
        headers: {{
            'Content-Type': 'application/json'
        }}
    }};
    
    return new Promise((resolve, reject) => {{
        const protocol = url.protocol === 'https:' ? https : http;
        
        const req = protocol.request(url, options, (res) => {{
            let data = '';
            res.on('data', (chunk) => {{ data += chunk; }});
            res.on('end', () => {{
                try {{
                    const jsonData = JSON.parse(data);
                    if (res.statusCode === 200) {{
                        resolve(jsonData);
                    }} else {{
                        reject(new Error(`Tool call failed: ${{jsonData.detail || data}}`));
                    }}
                }} catch (e) {{
                    reject(new Error(`Invalid JSON response: ${{data}}`));
                }}
            }});
        }});
        
        req.on('error', reject);
        req.write(JSON.stringify(requestData));
        req.end();
    }});
}}
"""
        client_file.write_text(client_code)

executor = CodeExecutor()

@app.post("/execute", response_model=CodeExecutionResponse)
async def execute_code(request: CodeExecutionRequest):
    """Execute code in secure sandbox - optimized for speed"""
    try:
        if request.language == "typescript":
            result = await executor.execute_typescript(request.code, request.user_id)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported language: {request.language}")
        
        return CodeExecutionResponse(**result)
    except Exception as e:
        logger.error(f"Error executing code: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "zoe-code-execution"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8010, log_level="info")
