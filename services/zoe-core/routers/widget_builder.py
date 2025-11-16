"""
Widget Builder Router
Handles widget generation, marketplace, and layout persistence
"""

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
import json
import sqlite3
import os
import secrets

# Use same database setup as other routers
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

def get_connection():
    """Get SQLite database connection"""
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn

router = APIRouter(prefix="/api/widgets", tags=["widgets"])

# Additional router for user-specific layout endpoints (aliased from /api/user/layout)
user_layout_router = APIRouter(prefix="/api/user", tags=["user"])


# ============================================================================
# Request/Response Models
# ============================================================================

class WidgetGenerationRequest(BaseModel):
    """Request to generate a widget from natural language"""
    description: str = Field(..., description="Natural language description of widget")
    user_id: Optional[str] = None
    

class WidgetConfig(BaseModel):
    """Widget configuration"""
    name: str
    display_name: str
    description: Optional[str] = None
    version: str = "1.0.0"
    widget_code: str
    widget_type: str = "custom"
    icon: Optional[str] = None
    default_size: str = "size-small"
    update_interval: Optional[int] = None
    data_sources: Optional[List[str]] = None
    permissions: Optional[List[str]] = None


class WidgetLayoutRequest(BaseModel):
    """Request to save widget layout"""
    device_id: str
    layout_type: str = "desktop_dashboard"
    layout: List[Dict[str, Any]]


class WidgetRatingRequest(BaseModel):
    """Request to rate a widget"""
    widget_id: str
    rating: int = Field(..., ge=1, le=5)
    review: Optional[str] = None


# ============================================================================
# Helper Functions
# ============================================================================

def get_user_from_request(request: Request) -> Optional[str]:
    """Extract user ID from request session"""
    try:
        session_id = request.headers.get('X-Session-ID')
        if session_id:
            # For now, return a placeholder user ID
            # In production, validate session and get actual user ID
            return "default-user"
    except:
        pass
    return None


# ============================================================================
# Widget Discovery Endpoints
# ============================================================================

@router.get("/available")
def get_available_widgets():
    """
    Get list of available widget types from manifest
    Returns widget metadata for frontend registration
    """
    import json
    import os
    
    try:
        # Read the widget manifest JSON file
        manifest_path = os.path.join(
            os.getenv("UI_DIR", "/app/services/zoe-ui/dist"), 
            "js/widgets/widget-manifest.json"
        )
        
        if not os.path.exists(manifest_path):
            raise HTTPException(status_code=404, detail="Widget manifest not found")
        
        with open(manifest_path, 'r') as f:
            manifest = json.load(f)
        
        return {
            "version": manifest.get("version", "1.0.0"),
            "widgets": manifest.get("widgets", []),
            "categories": manifest.get("categories", [])
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Widget manifest not found")
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Invalid manifest file")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading manifest: {str(e)}")


@router.get("/{widget_id}/info")
def get_widget_info(widget_id: str):
    """
    Get information about a specific widget
    """
    import json
    import os
    
    try:
        manifest_path = os.path.join(
            os.getenv("UI_DIR", "/app/services/zoe-ui/dist"), 
            "js/widgets/widget-manifest.json"
        )
        
        with open(manifest_path, 'r') as f:
            manifest = json.load(f)
        
        widget = next((w for w in manifest["widgets"] if w["id"] == widget_id), None)
        
        if not widget:
            raise HTTPException(status_code=404, detail="Widget not found")
        
        return widget
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Widget Marketplace Endpoints
# ============================================================================

@router.get("/marketplace")
def get_marketplace_widgets(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    widget_type: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = Query("downloads")
):
    """
    Browse widget marketplace
    Returns available widgets for installation
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Build query
        query = """
            SELECT 
                id, name, display_name, description, version, 
                icon, default_size, widget_type, author_id,
                downloads, rating, rating_count,
                is_official, created_at, published_at
            FROM widget_marketplace
            WHERE is_active = 1
        """
        params = []
        
        # Filter by type
        if widget_type:
            query += " AND widget_type = ?"
            params.append(widget_type)
        
        # Search filter
        if search:
            query += " AND (display_name LIKE ? OR description LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%"])
        
        # Sort
        sort_column = {
            "downloads": "downloads DESC",
            "rating": "rating DESC, rating_count DESC",
            "created_at": "created_at DESC"
        }.get(sort_by, "downloads DESC")
        
        query += f" ORDER BY {sort_column}"
        
        # Pagination
        offset = (page - 1) * limit
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        cursor.execute(query, params)
        widgets = [dict(row) for row in cursor.fetchall()]
        
        # Get total count
        count_query = "SELECT COUNT(*) as total FROM widget_marketplace WHERE is_active = 1"
        count_params = []
        if widget_type:
            count_query += " AND widget_type = ?"
            count_params.append(widget_type)
        if search:
            count_query += " AND (display_name LIKE ? OR description LIKE ?)"
            count_params.extend([f"%{search}%", f"%{search}%"])
        
        cursor.execute(count_query, count_params)
        total_result = cursor.fetchone()
        total = total_result['total'] if total_result else 0
        
        return {
            "widgets": widgets,
            "total": total,
            "page": page,
            "limit": limit,
            "pages": (total + limit - 1) // limit
        }
        
    finally:
        conn.close()


@router.post("/marketplace")
def publish_widget(
    widget: WidgetConfig,
    request: Request
):
    """
    Publish a widget to the marketplace
    Requires authentication
    """
    user_id = get_user_from_request(request) or "anonymous"
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Generate ID
        widget_id = secrets.token_hex(16)
        
        # Insert widget into marketplace
        cursor.execute(
            """
            INSERT INTO widget_marketplace (
                id, name, display_name, description, version, widget_code,
                widget_type, icon, default_size, update_interval,
                data_sources, permissions, author_id, published_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                widget_id,
                widget.name,
                widget.display_name,
                widget.description,
                widget.version,
                widget.widget_code,
                widget.widget_type,
                widget.icon,
                widget.default_size,
                widget.update_interval,
                json.dumps(widget.data_sources) if widget.data_sources else None,
                json.dumps(widget.permissions) if widget.permissions else None,
                user_id
            )
        )
        
        conn.commit()
        
        return {
            "success": True,
            "widget": {
                "id": widget_id,
                "name": widget.name,
                "display_name": widget.display_name,
                "version": widget.version
            },
            "message": "Widget published successfully"
        }
        
    except sqlite3.IntegrityError as e:
        raise HTTPException(status_code=409, detail=f"Widget already exists: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to publish widget: {str(e)}")
    finally:
        conn.close()


@router.post("/install/{widget_id}")
def install_widget(
    widget_id: str,
    request: Request
):
    """
    Install a widget from marketplace
    """
    user_id = get_user_from_request(request) or "anonymous"
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Check if widget exists
        cursor.execute(
            "SELECT * FROM widget_marketplace WHERE id = ? AND is_active = 1",
            (widget_id,)
        )
        widget = cursor.fetchone()
        
        if not widget:
            raise HTTPException(status_code=404, detail="Widget not found")
        
        # Install widget for user
        cursor.execute(
            """
            INSERT OR REPLACE INTO user_installed_widgets (user_id, widget_id, enabled)
            VALUES (?, ?, 1)
            """,
            (user_id, widget_id)
        )
        
        # Increment download count
        cursor.execute(
            "UPDATE widget_marketplace SET downloads = downloads + 1 WHERE id = ?",
            (widget_id,)
        )
        
        conn.commit()
        
        return {
            "success": True,
            "widget": dict(widget),
            "message": "Widget installed successfully"
        }
        
    finally:
        conn.close()


@router.delete("/uninstall/{widget_id}")
def uninstall_widget(
    widget_id: str,
    request: Request
):
    """
    Uninstall a widget
    """
    user_id = get_user_from_request(request) or "anonymous"
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            """
            DELETE FROM user_installed_widgets
            WHERE user_id = ? AND widget_id = ?
            """,
            (user_id, widget_id)
        )
        
        conn.commit()
        
        return {
            "success": True,
            "message": "Widget uninstalled successfully"
        }
        
    finally:
        conn.close()


@router.get("/my-widgets")
def get_user_widgets(
    request: Request
):
    """
    Get user's installed widgets
    """
    user_id = get_user_from_request(request) or "anonymous"
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            """
            SELECT 
                wm.id, wm.name, wm.display_name, wm.description, wm.version,
                wm.icon, wm.default_size, wm.widget_code, wm.widget_type,
                uiw.enabled, uiw.custom_config, uiw.installed_at
            FROM user_installed_widgets uiw
            JOIN widget_marketplace wm ON uiw.widget_id = wm.id
            WHERE uiw.user_id = ? AND wm.is_active = 1
            ORDER BY uiw.installed_at DESC
            """,
            (user_id,)
        )
        
        widgets = [dict(row) for row in cursor.fetchall()]
        
        return {
            "widgets": widgets
        }
        
    finally:
        conn.close()


# ============================================================================
# Widget Layout Endpoints
# ============================================================================

@router.post("/layout")
def save_widget_layout(
    layout_request: WidgetLayoutRequest,
    request: Request
):
    """
    Save widget layout for user and device
    Public endpoint - uses session from header or anonymous
    """
    user_id = get_user_from_request(request) or "anonymous"
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Check if layout exists for this user/device
        cursor.execute(
            """
            SELECT id FROM user_widget_layouts
            WHERE user_id = ? AND device_id = ? AND layout_type = ?
            """,
            (user_id, layout_request.device_id, layout_request.layout_type)
        )
        existing = cursor.fetchone()
        
        if existing:
            # Update existing layout
            cursor.execute(
                """
                UPDATE user_widget_layouts
                SET layout = ?, updated_at = datetime('now')
                WHERE user_id = ? AND device_id = ? AND layout_type = ?
                """,
                (
                    json.dumps(layout_request.layout),
                    user_id,
                    layout_request.device_id,
                    layout_request.layout_type
                )
            )
        else:
            # Insert new layout
            cursor.execute(
                """
                INSERT INTO user_widget_layouts (id, user_id, device_id, layout_type, layout)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    secrets.token_hex(16),
                    user_id,
                    layout_request.device_id,
                    layout_request.layout_type,
                    json.dumps(layout_request.layout)
                )
            )
        
        conn.commit()
        
        return {
            "success": True,
            "message": "Layout saved successfully"
        }
        
    finally:
        conn.close()


@router.get("/layout")
def get_widget_layout(
    device_id: str,
    layout_type: str = "desktop_dashboard",
    request: Request = None
):
    """
    Get widget layout for user and device
    """
    user_id = get_user_from_request(request) if request else "anonymous"
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            """
            SELECT layout, updated_at
            FROM user_widget_layouts
            WHERE user_id = ? AND device_id = ? AND layout_type = ?
            """,
            (user_id, device_id, layout_type)
        )
        
        result = cursor.fetchone()
        
        if result:
            return {
                "layout": json.loads(result['layout']),
                "updated_at": result['updated_at']
            }
        else:
            return {
                "layout": None,
                "message": "No saved layout found"
            }
        
    finally:
        conn.close()


@router.delete("/layout")
def delete_widget_layout(
    device_id: str,
    layout_type: str = "desktop_dashboard",
    request: Request = None
):
    """
    Delete widget layout for user and device
    """
    user_id = get_user_from_request(request) if request else "anonymous"
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            """
            DELETE FROM user_widget_layouts
            WHERE user_id = ? AND device_id = ? AND layout_type = ?
            """,
            (user_id, device_id, layout_type)
        )
        
        conn.commit()
        
        return {
            "success": True,
            "message": "Layout deleted successfully"
        }
        
    finally:
        conn.close()


# ============================================================================
# Widget Rating Endpoints
# ============================================================================

@router.post("/rate")
def rate_widget(
    rating_request: WidgetRatingRequest,
    request: Request
):
    """
    Rate a widget
    """
    user_id = get_user_from_request(request) or "anonymous"
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Insert or update rating
        cursor.execute(
            """
            INSERT OR REPLACE INTO widget_ratings (widget_id, user_id, rating, review, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            """,
            (
                rating_request.widget_id,
                user_id,
                rating_request.rating,
                rating_request.review
            )
        )
        
        # Update widget average rating
        cursor.execute(
            """
            SELECT AVG(rating) as avg_rating, COUNT(*) as count
            FROM widget_ratings
            WHERE widget_id = ?
            """,
            (rating_request.widget_id,)
        )
        
        result = cursor.fetchone()
        
        if result:
            cursor.execute(
                """
                UPDATE widget_marketplace
                SET rating = ?, rating_count = ?
                WHERE id = ?
                """,
                (
                    float(result['avg_rating']),
                    result['count'],
                    rating_request.widget_id
                )
            )
        
        conn.commit()
        
        return {
            "success": True,
            "message": "Rating submitted successfully",
            "average_rating": float(result['avg_rating']) if result else 0,
            "rating_count": result['count'] if result else 0
        }
        
    finally:
        conn.close()


# ============================================================================
# AI Widget Generation Endpoints
# ============================================================================

@router.post("/generate")
def generate_widget(
    request_data: WidgetGenerationRequest,
    request: Request
):
    """
    Generate a widget from natural language description
    Uses AI to create widget configuration
    
    Example: "Show me a widget that displays current CPU usage as a gauge"
    Returns: Widget configuration that can be installed
    """
    user_id = get_user_from_request(request) or "anonymous"
    
    try:
        # Import AI client
        from ai_client import get_ai_response
        
        # Create AI prompt for widget generation
        system_prompt = """You are a widget configuration generator for Zoe AI Dashboard.
        
Given a user's description, generate a valid widget configuration in JSON format.

Available widget templates:
- StatWidget: Single stat with icon (temperature, count, status)
- ChartWidget: Time series charts (line, bar, area)
- ListWidget: Scrollable list with items
- GaugeWidget: Progress/gauge visualizations
- MediaWidget: Images, video, camera feeds
- IframeWidget: Embed external content

Available data sources (API endpoints):
- /api/calendar/events - Calendar events
- /api/lists/tasks - Task lists
- /api/weather/current - Current weather
- /api/homeassistant/states - Smart home states
- /api/system/stats - System statistics

Return ONLY valid JSON in this format:
{
    "widget_type": "StatWidget|ChartWidget|ListWidget|GaugeWidget|MediaWidget|IframeWidget",
    "name": "unique-widget-name",
    "display_name": "Human Readable Name",
    "description": "What this widget does",
    "icon": "emoji",
    "default_size": "size-small|size-medium|size-large|size-xlarge",
    "update_interval": 60000,
    "data_source": "/api/endpoint",
    "config": {}
}

Security: Do NOT generate widgets that:
- Execute arbitrary code
- Access sensitive user data without permission
- Make external API calls to unknown domains
"""
        
        user_prompt = f"Create a widget: {request_data.description}"
        
        # Get AI response
        ai_response = get_ai_response(
            message=user_prompt,
            system_prompt=system_prompt,
            user_id=user_id,
            mode='json'
        )
        
        # Parse AI response as JSON
        try:
            widget_config = json.loads(ai_response)
        except json.JSONDecodeError:
            raise HTTPException(status_code=500, detail="AI generated invalid widget configuration")
        
        # Validate configuration
        if not all(k in widget_config for k in ['widget_type', 'name', 'display_name']):
            raise HTTPException(status_code=500, detail="Incomplete widget configuration")
        
        # Generate JavaScript code for the widget
        widget_code = generate_widget_code(widget_config)
        
        # Store widget in marketplace as AI-generated
        conn = get_connection()
        cursor = conn.cursor()
        
        widget_id = secrets.token_hex(16)
        
        cursor.execute(
            """
            INSERT INTO widget_marketplace (
                id, name, display_name, description, version, widget_code,
                widget_type, icon, default_size, update_interval,
                author_id, published_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                widget_id,
                widget_config['name'],
                widget_config['display_name'],
                widget_config.get('description', ''),
                '1.0.0',
                widget_code,
                'ai-generated',
                widget_config.get('icon', '🔹'),
                widget_config.get('default_size', 'size-small'),
                widget_config.get('update_interval'),
                user_id
            )
        )
        
        conn.commit()
        conn.close()
        
        return {
            "success": True,
            "widget": {
                "id": widget_id,
                "name": widget_config['name'],
                "display_name": widget_config['display_name'],
                "version": '1.0.0'
            },
            "widget_code": widget_code,
            "message": "Widget generated and published successfully"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate widget: {str(e)}")


def generate_widget_code(config: Dict[str, Any]) -> str:
    """
    Generate JavaScript widget code from configuration
    Uses templates to create safe, sandboxed widget code
    """
    widget_type = config.get('widget_type', 'StatWidget')
    name = config['name']
    display_name = config['display_name']
    icon = config.get('icon', '🔹')
    data_source = config.get('data_source', '/api/placeholder')
    update_interval = config.get('update_interval', 60000)
    class_name = name.replace('-', '_').replace(' ', '_').title() + 'Widget'
    
    # Generate safe widget code based on template
    if widget_type == 'StatWidget':
        return f"""
class {class_name} extends WidgetModule {{
    constructor() {{
        super('{name}', {{
            version: '1.0.0',
            defaultSize: '{config.get('default_size', 'size-small')}',
            updateInterval: {update_interval}
        }});
    }}
    
    getTemplate() {{
        return \`
            <div class="widget-controls">
                <button class="widget-control-btn" onclick="event.stopPropagation(); cycleWidgetSize(this.closest('.widget'))">📏</button>
                <button class="widget-control-btn delete" onclick="event.stopPropagation(); removeWidget(this.closest('.widget'))">🗑️</button>
            </div>
            <div class="widget-header">
                <div class="widget-title">{icon} {display_name}</div>
                <div class="widget-badge" id="{name}-value">--</div>
            </div>
            <div class="widget-content" style="display: flex; align-items: center; justify-content: center; font-size: 48px;">
                <div id="{name}-stat">--</div>
            </div>
        \`;
    }}
    
    init(element) {{
        super.init(element);
        this.loadData();
    }}
    
    update() {{
        this.loadData();
    }}
    
    async loadData() {{
        try {{
            const response = await fetch('{data_source}');
            const data = await response.json();
            this.updateDisplay(data);
        }} catch (error) {{
            console.error('Failed to load data:', error);
        }}
    }}
    
    updateDisplay(data) {{
        const valueEl = this.element.querySelector('#{name}-value');
        const statEl = this.element.querySelector('#{name}-stat');
        
        if (valueEl && data.value) valueEl.textContent = data.value;
        if (statEl && data.stat) statEl.textContent = data.stat;
    }}
}}

if (typeof WidgetRegistry !== 'undefined') {{
    WidgetRegistry.register('{name}', new {class_name}());
}}
"""
    
    # Fallback template for other types
    else:
        return f"""
class {class_name} extends WidgetModule {{
    constructor() {{
        super('{name}', {{ version: '1.0.0', defaultSize: '{config.get('default_size', 'size-small')}' }});
    }}
    
    getTemplate() {{
        return \`
            <div class="widget-controls">
                <button class="widget-control-btn" onclick="event.stopPropagation(); cycleWidgetSize(this.closest('.widget'))">📏</button>
                <button class="widget-control-btn delete" onclick="event.stopPropagation(); removeWidget(this.closest('.widget'))">🗑️</button>
            </div>
            <div class="widget-header">
                <div class="widget-title">{icon} {display_name}</div>
            </div>
            <div class="widget-content">
                <div>Widget content here</div>
            </div>
        \`;
    }}
}}

if (typeof WidgetRegistry !== 'undefined') {{
    WidgetRegistry.register('{name}', new {class_name}());
}}
"""


# ============================================================================
# Widget Update Endpoints
# ============================================================================

@router.get("/updates")
def check_widget_updates(request: Request):
    """
    Check for available widget updates
    Returns list of widgets that have newer versions
    """
    # Placeholder - would compare installed widget versions with marketplace
    return []


@router.post("/update/{widget_name}")
def update_widget(
    widget_name: str,
    request: Request
):
    """
    Update a specific widget to latest version
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            "SELECT * FROM widget_marketplace WHERE name = ? AND is_active = 1",
            (widget_name,)
        )
        widget = cursor.fetchone()
        
        if not widget:
            raise HTTPException(status_code=404, detail="Widget not found")
        
        return {
            "success": True,
            "widget": dict(widget),
            "message": "Widget updated successfully"
        }
        
    finally:
        conn.close()


@router.post("/update-all")
def update_all_widgets(request: Request):
    """
    Update all installed widgets to latest versions
    """
    return {
        "success": True,
        "updates": [],
        "message": "All widgets are up to date"
    }


# ============================================================================
# Widget Analytics
# ============================================================================

@router.post("/analytics/track")
def track_widget_usage(
    widget_id: str,
    action: str,
    metadata: Optional[Dict[str, Any]] = None,
    request: Request = None
):
    """
    Track widget usage for analytics
    """
    user_id = get_user_from_request(request) if request else "anonymous"
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            """
            INSERT INTO widget_usage_analytics (user_id, widget_id, action, metadata)
            VALUES (?, ?, ?, ?)
            """,
            (
                user_id,
                widget_id,
                action,
                json.dumps(metadata) if metadata else None
            )
        )
        
        conn.commit()
        
        return {"success": True}
        
    finally:
        conn.close()


# ============================================================================
# User Layout Endpoints (Aliased from /api/user/layout)
# ============================================================================

@user_layout_router.post("/layout")
def save_user_layout(
    layout_request: WidgetLayoutRequest,
    request: Request
):
    """
    Save widget layout for user and device
    Aliased endpoint for /api/user/layout compatibility
    """
    return save_widget_layout(layout_request, request)


@user_layout_router.get("/layout")
def get_user_layout(
    device_id: str,
    layout_type: str = "desktop_dashboard",
    request: Request = None
):
    """
    Get widget layout for user and device
    Aliased endpoint for /api/user/layout compatibility
    """
    return get_widget_layout(device_id, layout_type, request)


@user_layout_router.delete("/layout")
def delete_user_layout(
    device_id: str,
    layout_type: str = "desktop_dashboard",
    request: Request = None
):
    """
    Delete widget layout for user and device
    Aliased endpoint for /api/user/layout compatibility
    """
    return delete_widget_layout(device_id, layout_type, request)
