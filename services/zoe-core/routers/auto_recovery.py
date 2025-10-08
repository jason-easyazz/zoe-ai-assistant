from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import asyncio
import json
import httpx
import time
import sqlite3
from datetime import datetime, timedelta
import subprocess

router = APIRouter(prefix="/api/developer/auto-recovery")

class RecoveryAttempt(BaseModel):
    id: str
    service: str
    attempt_number: int
    timestamp: str
    success: bool
    error_message: Optional[str] = None
    backoff_delay: int

class RecoveryConfig(BaseModel):
    enabled: bool = True
    max_attempts: int = 5
    base_delay: int = 30  # seconds
    max_delay: int = 300  # 5 minutes
    backoff_multiplier: float = 2.0
    services: List[str] = ["core", "ui", "ollama", "redis", "whisper", "tts", "n8n"]

# Initialize database for recovery tracking
def init_recovery_db():
    conn = sqlite3.connect('/app/data/developer_tasks.db')
    cursor = conn.cursor()
    
    # Create recovery attempts table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recovery_attempts (
            id TEXT PRIMARY KEY,
            service TEXT NOT NULL,
            attempt_number INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            success BOOLEAN NOT NULL,
            error_message TEXT,
            backoff_delay INTEGER NOT NULL
        )
    """)
    
    # Create recovery config table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recovery_config (
            id INTEGER PRIMARY KEY,
            enabled BOOLEAN DEFAULT TRUE,
            max_attempts INTEGER DEFAULT 5,
            base_delay INTEGER DEFAULT 30,
            max_delay INTEGER DEFAULT 300,
            backoff_multiplier REAL DEFAULT 2.0,
            services TEXT DEFAULT '["core", "ui", "ollama", "redis", "whisper", "tts", "n8n"]'
        )
    """)
    
    # Insert default config if it doesn't exist
    cursor.execute("""
        INSERT OR IGNORE INTO recovery_config 
        (id, enabled, max_attempts, base_delay, max_delay, backoff_multiplier, services)
        VALUES (1, TRUE, 5, 30, 300, 2.0, '["core", "ui", "ollama", "redis", "whisper", "tts", "n8n"]')
    """)
    
    conn.commit()
    conn.close()

# Initialize database on startup
init_recovery_db()

@router.get("/config")
async def get_recovery_config():
    """Get current recovery configuration"""
    try:
        conn = sqlite3.connect('/app/data/developer_tasks.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM recovery_config WHERE id = 1")
        config_row = cursor.fetchone()
        
        conn.close()
        
        if config_row:
            config = dict(config_row)
            config['services'] = json.loads(config['services'])
            return config
        else:
            return {
                "enabled": True,
                "max_attempts": 5,
                "base_delay": 30,
                "max_delay": 300,
                "backoff_multiplier": 2.0,
                "services": ["core", "ui", "ollama", "redis", "whisper", "tts", "n8n"]
            }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching recovery config: {str(e)}")

@router.put("/config")
async def update_recovery_config(config: RecoveryConfig):
    """Update recovery configuration"""
    try:
        conn = sqlite3.connect('/app/data/developer_tasks.db')
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE recovery_config 
            SET enabled = ?, max_attempts = ?, base_delay = ?, 
                max_delay = ?, backoff_multiplier = ?, services = ?
            WHERE id = 1
        """, (
            config.enabled, config.max_attempts, config.base_delay,
            config.max_delay, config.backoff_multiplier, json.dumps(config.services)
        ))
        
        conn.commit()
        conn.close()
        return {"message": "Recovery configuration updated successfully"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating recovery config: {str(e)}")

@router.get("/attempts")
async def get_recovery_attempts(service: Optional[str] = None, limit: int = 50):
    """Get recovery attempts with optional filtering"""
    try:
        conn = sqlite3.connect('/app/data/developer_tasks.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = "SELECT * FROM recovery_attempts"
        params = []
        
        if service:
            query += " WHERE service = ?"
            params.append(service)
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        attempts = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        return {"attempts": attempts, "total": len(attempts)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching recovery attempts: {str(e)}")

@router.post("/restart/{service}")
async def restart_service(service: str, background_tasks: BackgroundTasks):
    """Manually restart a specific service"""
    try:
        background_tasks.add_task(restart_service_task, service)
        return {"message": f"Restart initiated for service {service}"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error restarting service: {str(e)}")

@router.post("/check-and-recover")
async def check_and_recover(background_tasks: BackgroundTasks):
    """Check all services and recover failed ones"""
    try:
        background_tasks.add_task(run_recovery_checks)
        return {"message": "Recovery check initiated"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error running recovery checks: {str(e)}")

@router.get("/status")
async def get_recovery_status():
    """Get current recovery system status"""
    try:
        # Get config
        config = await get_recovery_config()
        
        # Get recent attempts
        attempts = await get_recovery_attempts(limit=10)
        
        # Get current service status
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:8000/api/developer/health")
            health_data = response.json()
        
        # Analyze service status
        services_status = {}
        if 'services' in health_data:
            for service_name, service_info in health_data['services'].items():
                clean_name = service_name.replace('zoe-', '')
                services_status[clean_name] = {
                    "healthy": service_info.get('ok', False),
                    "response_time": service_info.get('latency_ms', 0)
                }
        
        # Calculate recovery stats
        total_attempts = len(attempts['attempts'])
        successful_attempts = len([a for a in attempts['attempts'] if a['success']])
        failed_attempts = total_attempts - successful_attempts
        
        return {
            "config": config,
            "services_status": services_status,
            "recovery_stats": {
                "total_attempts": total_attempts,
                "successful_attempts": successful_attempts,
                "failed_attempts": failed_attempts,
                "success_rate": (successful_attempts / total_attempts * 100) if total_attempts > 0 else 0
            },
            "last_updated": datetime.now().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching recovery status: {str(e)}")

async def restart_service_task(service: str):
    """Restart a specific service with recovery tracking"""
    try:
        # Get recovery config
        config = await get_recovery_config()
        
        # Get recent attempts for this service
        attempts = await get_recovery_attempts(service=service, limit=10)
        recent_attempts = [a for a in attempts['attempts'] if a['service'] == service]
        
        # Calculate backoff delay
        attempt_number = len(recent_attempts) + 1
        backoff_delay = min(
            config['base_delay'] * (config['backoff_multiplier'] ** (attempt_number - 1)),
            config['max_delay']
        )
        
        # Check if we've exceeded max attempts
        if attempt_number > config['max_attempts']:
            print(f"Max recovery attempts exceeded for service {service}")
            return
        
        # Wait for backoff delay
        await asyncio.sleep(backoff_delay)
        
        # Attempt to restart the service
        success = False
        error_message = None
        
        try:
            # Use docker compose to restart the service
            result = subprocess.run(
                ["docker", "compose", "restart", f"zoe-{service}"],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode == 0:
                success = True
                print(f"Successfully restarted service {service}")
            else:
                error_message = result.stderr
                print(f"Failed to restart service {service}: {error_message}")
                
        except subprocess.TimeoutExpired:
            error_message = "Restart timeout exceeded"
            print(f"Restart timeout for service {service}")
        except Exception as e:
            error_message = str(e)
            print(f"Error restarting service {service}: {e}")
        
        # Record the attempt
        attempt_id = f"{service}_{int(time.time())}"
        attempt = RecoveryAttempt(
            id=attempt_id,
            service=service,
            attempt_number=attempt_number,
            timestamp=datetime.now().isoformat(),
            success=success,
            error_message=error_message,
            backoff_delay=backoff_delay
        )
        
        # Save attempt to database
        conn = sqlite3.connect('/app/data/developer_tasks.db')
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO recovery_attempts 
            (id, service, attempt_number, timestamp, success, error_message, backoff_delay)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            attempt.id, attempt.service, attempt.attempt_number,
            attempt.timestamp, attempt.success, attempt.error_message,
            attempt.backoff_delay
        ))
        
        conn.commit()
        conn.close()
        
        # If restart failed and we haven't exceeded max attempts, schedule another attempt
        if not success and attempt_number < config['max_attempts']:
            print(f"Scheduling retry for service {service} in {backoff_delay} seconds")
            asyncio.create_task(restart_service_task(service))
        
    except Exception as e:
        print(f"Error in restart task for {service}: {e}")

async def run_recovery_checks():
    """Check all services and recover failed ones"""
    try:
        # Get recovery config
        config = await get_recovery_config()
        
        if not config['enabled']:
            print("Recovery system is disabled")
            return
        
        # Get current health data
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:8000/api/developer/health")
            health_data = response.json()
        
        # Check each configured service
        if 'services' in health_data:
            for service_name, service_info in health_data['services'].items():
                clean_name = service_name.replace('zoe-', '')
                
                # Only check configured services
                if clean_name in config['services']:
                    is_healthy = service_info.get('ok', False)
                    
                    if not is_healthy:
                        print(f"Service {clean_name} is unhealthy, initiating recovery")
                        asyncio.create_task(restart_service_task(clean_name))
                    else:
                        print(f"Service {clean_name} is healthy")
        
    except Exception as e:
        print(f"Error in recovery checks: {e}")

# Background task to run recovery checks periodically
async def start_recovery_monitoring():
    """Start background recovery monitoring"""
    while True:
        try:
            await run_recovery_checks()
            await asyncio.sleep(60)  # Check every minute
        except Exception as e:
            print(f"Error in recovery monitoring: {e}")
            await asyncio.sleep(300)  # Wait 5 minutes on error

# Start monitoring when module loads (commented out for now)
# asyncio.create_task(start_recovery_monitoring())






