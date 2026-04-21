"""
Widget Generator
==================

Phase 8: Generates widget definitions from user requests or patterns.

Generated widgets are HTML/JS only -- no server-side code execution.
They declare allowed_endpoints and the widget runtime enforces this.
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

USER_WIDGETS_DIR = os.path.expanduser(
    os.getenv("USER_WIDGETS_DIR", "~/.zoe/widgets")
)

# Widget template
WIDGET_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
        .widget-container {{
            padding: 16px;
            background: var(--widget-bg, #ffffff);
            border-radius: 12px;
            color: var(--widget-text, #1a1a1a);
        }}
        .widget-title {{
            font-size: 1.1em;
            font-weight: 600;
            margin-bottom: 12px;
        }}
        .widget-content {{
            font-size: 0.95em;
            line-height: 1.5;
        }}
        {custom_css}
    </style>
</head>
<body>
    <div class="widget-container">
        <div class="widget-title">{title}</div>
        <div class="widget-content" id="content">
            Loading...
        </div>
    </div>
    <script>
        // Allowed endpoints: {allowed_endpoints}
        const ALLOWED_ENDPOINTS = {allowed_endpoints_json};

        async function fetchData(endpoint) {{
            // Enforce endpoint whitelist
            const isAllowed = ALLOWED_ENDPOINTS.some(allowed => {{
                const [method, path] = allowed.split(' ');
                return endpoint.startsWith(path);
            }});

            if (!isAllowed) {{
                console.error('Blocked: endpoint not in allowed_endpoints:', endpoint);
                return null;
            }}

            try {{
                const resp = await fetch(endpoint, {{
                    headers: {{ 'X-Session-ID': window.sessionId || 'dev-localhost' }}
                }});
                return await resp.json();
            }} catch (e) {{
                console.error('Widget fetch error:', e);
                return null;
            }}
        }}

        {custom_js}
    </script>
</body>
</html>
"""


def generate_widget(
    name: str,
    title: str,
    description: str,
    allowed_endpoints: List[str],
    custom_css: str = "",
    custom_js: str = "",
) -> Dict[str, Any]:
    """Generate a widget definition.

    Args:
        name: Widget identifier (kebab-case)
        title: Display title
        description: Widget description
        allowed_endpoints: API endpoints the widget can call
        custom_css: Additional CSS
        custom_js: JavaScript for data fetching and rendering

    Returns:
        Dict with success status and widget path
    """
    try:
        widget_dir = os.path.join(USER_WIDGETS_DIR, name)
        os.makedirs(widget_dir, exist_ok=True)

        # Generate manifest
        manifest = {
            "name": name,
            "title": title,
            "description": description,
            "version": "1.0.0",
            "author": "zoe-self-created",
            "auto_generated": True,
            "allowed_endpoints": allowed_endpoints,
            "created_at": datetime.utcnow().isoformat() + "Z",
        }

        manifest_path = os.path.join(widget_dir, "manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        # Generate HTML
        html_content = WIDGET_TEMPLATE.format(
            title=title,
            custom_css=custom_css,
            custom_js=custom_js,
            allowed_endpoints=", ".join(allowed_endpoints),
            allowed_endpoints_json=json.dumps(allowed_endpoints),
        )

        html_path = os.path.join(widget_dir, "index.html")
        with open(html_path, "w") as f:
            f.write(html_content)

        logger.info(f"Generated widget: {name} at {widget_dir}")

        return {
            "success": True,
            "name": name,
            "path": widget_dir,
            "manifest": manifest,
        }

    except Exception as e:
        logger.error(f"Failed to generate widget: {e}")
        return {"success": False, "error": str(e)}


def list_user_widgets() -> List[Dict[str, Any]]:
    """List all user-created widgets."""
    widgets = []
    if not os.path.isdir(USER_WIDGETS_DIR):
        return widgets

    for entry in sorted(os.listdir(USER_WIDGETS_DIR)):
        manifest_path = os.path.join(USER_WIDGETS_DIR, entry, "manifest.json")
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path, "r") as f:
                    manifest = json.load(f)
                widgets.append(manifest)
            except Exception as e:
                logger.warning(f"Failed to read widget manifest {entry}: {e}")

    return widgets
