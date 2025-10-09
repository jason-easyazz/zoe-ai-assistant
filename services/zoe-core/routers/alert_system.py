from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import asyncio
import json
import httpx
import psutil
from datetime import datetime, timedelta
import sqlite3

router = APIRouter(prefix="/api/developer/alerts")

class Alert(BaseModel):
    id: str
    service: str
    level: str  # critical, warning, info
    message: str
    timestamp: str
    resolved: bool = False
    resolved_at: Optional[str] = None

class AlertRule(BaseModel):
    id: str
    name: str
    service: str
    metric: str  # cpu, memory, status, response_time
    threshold: float
    operator: str  # >, <, ==, !=
    level: str
    enabled: bool = True

class AlertConfig(BaseModel):
    enabled: bool = True
    check_interval: int = 30  # seconds
    notification_channels: List[str] = ["log", "api"]
    alert_rules: List[AlertRule] = []

# Initialize database for alerts
def init_alert_db():
    conn = sqlite3.connect('/app/data/zoe.db')
    cursor = conn.cursor()
    
    # Create alerts table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id TEXT PRIMARY KEY,
            service TEXT NOT NULL,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            resolved BOOLEAN DEFAULT FALSE,
            resolved_at TEXT
        )
    """)
    
    # Create alert rules table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alert_rules (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            service TEXT NOT NULL,
            metric TEXT NOT NULL,
            threshold REAL NOT NULL,
            operator TEXT NOT NULL,
            level TEXT NOT NULL,
            enabled BOOLEAN DEFAULT TRUE
        )
    """)
    
    # Create default alert rules if they don't exist
    default_rules = [
        ("rule_1", "High CPU Usage", "core", "cpu", 80.0, ">", "warning"),
        ("rule_2", "Critical CPU Usage", "core", "cpu", 95.0, ">", "critical"),
        ("rule_3", "High Memory Usage", "core", "memory", 80.0, ">", "warning"),
        ("rule_4", "Critical Memory Usage", "core", "memory", 95.0, ">", "critical"),
        ("rule_5", "Service Down", "core", "status", 0, "==", "critical"),
        ("rule_6", "High Response Time", "core", "response_time", 5000.0, ">", "warning"),
        ("rule_7", "Critical Response Time", "core", "response_time", 10000.0, ">", "critical")
    ]
    
    for rule in default_rules:
        cursor.execute("""
            INSERT OR IGNORE INTO alert_rules 
            (id, name, service, metric, threshold, operator, level, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (*rule, True))  # Add enabled=True as the 8th parameter
    
    conn.commit()
    conn.close()

# Initialize database on startup
init_alert_db()

@router.get("/")
async def get_alerts(limit: int = 50, resolved: Optional[bool] = None):
    """Get all alerts with optional filtering"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = "SELECT * FROM alerts"
        params = []
        
        if resolved is not None:
            query += " WHERE resolved = ?"
            params.append(resolved)
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        alerts = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        return {"alerts": alerts, "total": len(alerts)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching alerts: {str(e)}")

@router.get("/active")
async def get_active_alerts():
    """Get only unresolved alerts"""
    return await get_alerts(resolved=False)

@router.post("/create")
async def create_alert(alert: Alert):
    """Create a new alert"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO alerts (id, service, level, message, timestamp, resolved, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            alert.id, alert.service, alert.level, alert.message, 
            alert.timestamp, alert.resolved, alert.resolved_at
        ))
        
        conn.commit()
        conn.close()
        return {"message": "Alert created successfully", "alert_id": alert.id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating alert: {str(e)}")

@router.post("/{alert_id}/resolve")
async def resolve_alert(alert_id: str):
    """Mark an alert as resolved"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE alerts 
            SET resolved = TRUE, resolved_at = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), alert_id))
        
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Alert not found")
        
        conn.commit()
        conn.close()
        return {"message": "Alert resolved successfully"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error resolving alert: {str(e)}")

@router.get("/rules")
async def get_alert_rules():
    """Get all alert rules"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM alert_rules ORDER BY service, level")
        rules = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        return {"rules": rules}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching alert rules: {str(e)}")

@router.post("/rules")
async def create_alert_rule(rule: AlertRule):
    """Create a new alert rule"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO alert_rules 
            (id, name, service, metric, threshold, operator, level, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            rule.id, rule.name, rule.service, rule.metric, 
            rule.threshold, rule.operator, rule.level, rule.enabled
        ))
        
        conn.commit()
        conn.close()
        return {"message": "Alert rule created successfully", "rule_id": rule.id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating alert rule: {str(e)}")

@router.post("/check")
async def check_alerts(background_tasks: BackgroundTasks):
    """Manually trigger alert checking"""
    background_tasks.add_task(run_alert_checks)
    return {"message": "Alert check triggered"}

async def run_alert_checks():
    """Run alert checks for all services"""
    try:
        # Get all enabled alert rules
        conn = sqlite3.connect('/app/data/zoe.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM alert_rules WHERE enabled = TRUE")
        rules = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        
        # Get current health data
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:8000/api/developer/health")
            health_data = response.json()
        
        # Check each rule
        for rule in rules:
            await check_rule(rule, health_data)
            
    except Exception as e:
        print(f"Error running alert checks: {e}")

async def check_rule(rule: dict, health_data: dict):
    """Check a specific alert rule"""
    try:
        service_name = rule['service']
        metric = rule['metric']
        threshold = rule['threshold']
        operator = rule['operator']
        level = rule['level']
        
        # Get current metric value
        current_value = await get_metric_value(service_name, metric, health_data)
        
        # Check if rule is triggered
        triggered = False
        if operator == ">":
            triggered = current_value > threshold
        elif operator == "<":
            triggered = current_value < threshold
        elif operator == "==":
            triggered = current_value == threshold
        elif operator == "!=":
            triggered = current_value != threshold
        
        if triggered:
            # Create alert
            alert_id = f"{service_name}_{metric}_{int(datetime.now().timestamp())}"
            message = f"{rule['name']}: {metric} is {current_value} (threshold: {threshold})"
            
            alert = Alert(
                id=alert_id,
                service=service_name,
                level=level,
                message=message,
                timestamp=datetime.now().isoformat()
            )
            
            # Save alert to database
            conn = sqlite3.connect('/app/data/zoe.db')
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR IGNORE INTO alerts 
                (id, service, level, message, timestamp, resolved, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                alert.id, alert.service, alert.level, alert.message, 
                alert.timestamp, alert.resolved, alert.resolved_at
            ))
            
            conn.commit()
            conn.close()
            
            print(f"ALERT: {message}")
            
    except Exception as e:
        print(f"Error checking rule {rule['name']}: {e}")

async def get_metric_value(service_name: str, metric: str, health_data: dict) -> float:
    """Get current value for a specific metric"""
    try:
        if metric == "status":
            # Check if service is running
            if 'services' in health_data and service_name in health_data['services']:
                return 1.0 if health_data['services'][service_name].get('ok', False) else 0.0
            return 0.0
            
        elif metric == "response_time":
            # Get response time from health data
            if 'services' in health_data and service_name in health_data['services']:
                return health_data['services'][service_name].get('latency_ms', 0.0)
            return 0.0
            
        elif metric == "cpu":
            # Get CPU usage (simplified - would need more detailed monitoring)
            return psutil.cpu_percent(interval=1)
            
        elif metric == "memory":
            # Get memory usage (simplified - would need more detailed monitoring)
            return psutil.virtual_memory().percent
            
        else:
            return 0.0
            
    except Exception as e:
        print(f"Error getting metric {metric} for {service_name}: {e}")
        return 0.0

@router.get("/stats")
async def get_alert_stats():
    """Get alert statistics"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get total alerts
        cursor.execute("SELECT COUNT(*) as total FROM alerts")
        total_alerts = cursor.fetchone()['total']
        
        # Get active alerts
        cursor.execute("SELECT COUNT(*) as active FROM alerts WHERE resolved = FALSE")
        active_alerts = cursor.fetchone()['active']
        
        # Get alerts by level
        cursor.execute("""
            SELECT level, COUNT(*) as count 
            FROM alerts 
            WHERE resolved = FALSE 
            GROUP BY level
        """)
        alerts_by_level = {row['level']: row['count'] for row in cursor.fetchall()}
        
        # Get alerts by service
        cursor.execute("""
            SELECT service, COUNT(*) as count 
            FROM alerts 
            WHERE resolved = FALSE 
            GROUP BY service
        """)
        alerts_by_service = {row['service']: row['count'] for row in cursor.fetchall()}
        
        conn.close()
        
        return {
            "total_alerts": total_alerts,
            "active_alerts": active_alerts,
            "resolved_alerts": total_alerts - active_alerts,
            "alerts_by_level": alerts_by_level,
            "alerts_by_service": alerts_by_service,
            "last_updated": datetime.now().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching alert stats: {str(e)}")

# Background task to run alert checks periodically
async def start_alert_monitoring():
    """Start background alert monitoring"""
    while True:
        try:
            await run_alert_checks()
            await asyncio.sleep(30)  # Check every 30 seconds
        except Exception as e:
            print(f"Error in alert monitoring: {e}")
            await asyncio.sleep(60)  # Wait longer on error

# Start monitoring when module loads
# asyncio.create_task(start_alert_monitoring())
