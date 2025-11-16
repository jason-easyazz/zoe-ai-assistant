#!/usr/bin/env python3
"""
Generate TypeScript tool wrappers for MCP tools
Creates the servers/ directory structure with tool files
"""

import json
import os
from pathlib import Path

# Tool definitions from MCP server
TOOLS = [
    # Zoe Memory System
    {"name": "search_memories", "category": "zoe-memory", "description": "Search through Zoe's memory system"},
    {"name": "create_person", "category": "zoe-memory", "description": "Create a new person"},
    {"name": "create_collection", "category": "zoe-memory", "description": "Create a new collection"},
    {"name": "get_people", "category": "zoe-memory", "description": "Get all people"},
    {"name": "get_person_analysis", "category": "zoe-memory", "description": "Get person analysis"},
    {"name": "get_collections", "category": "zoe-memory", "description": "Get all collections"},
    {"name": "get_collection_analysis", "category": "zoe-memory", "description": "Get collection analysis"},
    
    # Calendar & Lists
    {"name": "create_calendar_event", "category": "zoe-calendar", "description": "Create calendar event"},
    {"name": "add_to_list", "category": "zoe-lists", "description": "Add item to list"},
    {"name": "get_calendar_events", "category": "zoe-calendar", "description": "Get calendar events"},
    {"name": "get_lists", "category": "zoe-lists", "description": "Get all lists"},
    
    # Home Assistant
    {"name": "get_home_assistant_devices", "category": "home-assistant", "description": "Get HA devices"},
    {"name": "control_home_assistant_device", "category": "home-assistant", "description": "Control HA device"},
    {"name": "get_home_assistant_automations", "category": "home-assistant", "description": "Get HA automations"},
    {"name": "trigger_home_assistant_automation", "category": "home-assistant", "description": "Trigger HA automation"},
    {"name": "get_home_assistant_scenes", "category": "home-assistant", "description": "Get HA scenes"},
    {"name": "activate_home_assistant_scene", "category": "home-assistant", "description": "Activate HA scene"},
    
    # N8N
    {"name": "get_n8n_workflows", "category": "n8n", "description": "Get N8N workflows"},
    {"name": "create_n8n_workflow", "category": "n8n", "description": "Create N8N workflow"},
    {"name": "execute_n8n_workflow", "category": "n8n", "description": "Execute N8N workflow"},
    {"name": "get_n8n_executions", "category": "n8n", "description": "Get N8N executions"},
    {"name": "get_n8n_nodes", "category": "n8n", "description": "Get N8N nodes"},
    
    # Developer
    {"name": "get_developer_tasks", "category": "developer", "description": "Get developer tasks"},
    
    # Matrix
    {"name": "send_matrix_message", "category": "matrix", "description": "Send Matrix message"},
    {"name": "get_matrix_rooms", "category": "matrix", "description": "Get Matrix rooms"},
    {"name": "create_matrix_room", "category": "matrix", "description": "Create Matrix room"},
    {"name": "join_matrix_room", "category": "matrix", "description": "Join Matrix room"},
    {"name": "get_matrix_messages", "category": "matrix", "description": "Get Matrix messages"},
    {"name": "set_matrix_presence", "category": "matrix", "description": "Set Matrix presence"},
]

def generate_tool_file(tool_name: str, category: str, description: str) -> str:
    """Generate TypeScript code for a tool"""
    # Convert snake_case to camelCase for function name
    func_name = ''.join(word.capitalize() if i > 0 else word for i, word in enumerate(tool_name.split('_')))
    func_name = func_name[0].lower() + func_name[1:] if func_name else tool_name
    
    return f"""import {{ callMCPTool }} from '../mcp_client.js';

/**
 * {description}
 */
export async function {func_name}(input: any) {{
    return callMCPTool('{tool_name}', input);
}}
"""

def generate_index_file(tools: list, category: str) -> str:
    """Generate index.ts file for a category"""
    imports = []
    exports = []
    
    for tool in tools:
        if tool['category'] == category:
            func_name = ''.join(word.capitalize() if i > 0 else word for i, word in enumerate(tool['name'].split('_')))
            func_name = func_name[0].lower() + func_name[1:] if func_name else tool['name']
            imports.append(f"import {{ {func_name} }} from './{tool['name']}.js';")
            exports.append(f"export {{ {func_name} }};")
    
    return '\n'.join(imports) + '\n\n' + '\n'.join(exports)

def main():
    """Generate all tool files"""
    base_dir = Path("/tmp/zoe-tool-servers")
    base_dir.mkdir(exist_ok=True)
    
    # Group tools by category
    categories = {}
    for tool in TOOLS:
        cat = tool['category']
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(tool)
    
    # Generate files for each category
    for category, tools in categories.items():
        cat_dir = base_dir / category
        cat_dir.mkdir(exist_ok=True)
        
        # Generate individual tool files
        for tool in tools:
            tool_file = cat_dir / f"{tool['name']}.ts"
            tool_file.write_text(generate_tool_file(
                tool['name'],
                tool['category'],
                tool['description']
            ))
        
        # Generate index.ts
        index_file = cat_dir / "index.ts"
        index_file.write_text(generate_index_file(TOOLS, category))
    
    # Generate root index.ts
    root_index = base_dir / "index.ts"
    root_exports = []
    for category in categories.keys():
        root_exports.append(f"export * as {category.replace('-', '_')} from './{category}/index.js';")
    root_index.write_text('\n'.join(root_exports))
    
    print(f"✅ Generated {len(TOOLS)} tool files in {len(categories)} categories")
    print(f"📁 Output directory: {base_dir}")

if __name__ == "__main__":
    main()

