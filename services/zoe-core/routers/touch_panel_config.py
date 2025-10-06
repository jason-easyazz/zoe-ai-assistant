"""
Touch Panel Remote Configuration API
===================================

Allows the main Zoe instance to remotely configure and manage touch panel Pis.
This leverages the main instance's knowledge of Zoe's architecture.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Dict, List, Optional, Any
import subprocess
import json
import requests
import time
import uuid
from datetime import datetime
import asyncio
import os

router = APIRouter(prefix="/api/touch-panels", tags=["touch-panels"])

class TouchPanelConfig(BaseModel):
    panel_id: str
    panel_name: str
    ip_address: str
    panel_type: str = "generic"  # generic, kiosk, control-panel, etc.
    capabilities: List[str] = []  # touch, audio, camera, etc.
    applications: List[str] = []  # which apps to install/configure
    zoe_services: List[str] = []  # which Zoe services this panel should access
    display_settings: Dict[str, Any] = {}
    network_settings: Dict[str, Any] = {}
    custom_config: Dict[str, Any] = {}

class TouchPanelStatus(BaseModel):
    panel_id: str
    status: str  # online, offline, configuring, error
    last_seen: datetime
    current_config_version: str
    installed_apps: List[str] = []
    system_info: Dict[str, Any] = {}

class ConfigurationTask(BaseModel):
    task_id: str
    panel_id: str
    task_type: str  # install, configure, update, restart
    parameters: Dict[str, Any]
    status: str = "pending"  # pending, running, completed, failed
    created_at: datetime
    logs: List[str] = []

# In-memory storage (in production, use database)
touch_panels: Dict[str, TouchPanelConfig] = {}
panel_status: Dict[str, TouchPanelStatus] = {}
configuration_tasks: Dict[str, ConfigurationTask] = {}

@router.get("/discover")
async def discover_touch_panels():
    """
    Discover touch panels on the network that are ready for configuration
    """
    discovered_panels = []
    
    # Scan network for touch panels
    try:
        # Get local network range
        result = subprocess.run(['ip', 'route', 'show', 'default'], 
                              capture_output=True, text=True)
        
        # Simple network scan for touch panels
        # Look for devices responding on common touch panel ports
        network_base = "192.168.1"  # Could be dynamic based on current network
        
        for i in range(1, 255):
            ip = f"{network_base}.{i}"
            try:
                # Check if it's a touch panel by looking for our discovery agent
                response = requests.get(f"http://{ip}:8888/touch-panel-info", timeout=1)
                if response.status_code == 200:
                    panel_info = response.json()
                    discovered_panels.append({
                        "ip_address": ip,
                        "panel_info": panel_info,
                        "discovered_at": datetime.now().isoformat()
                    })
            except:
                continue
                
    except Exception as e:
        pass
    
    return {"discovered_panels": discovered_panels}

@router.post("/register")
async def register_touch_panel(config: TouchPanelConfig):
    """
    Register a new touch panel for management
    """
    touch_panels[config.panel_id] = config
    
    # Initialize status
    panel_status[config.panel_id] = TouchPanelStatus(
        panel_id=config.panel_id,
        status="registered",
        last_seen=datetime.now(),
        current_config_version="1.0"
    )
    
    return {"message": "Touch panel registered successfully", "panel_id": config.panel_id}

@router.get("/panels")
async def list_touch_panels():
    """
    List all registered touch panels
    """
    panels_with_status = []
    for panel_id, config in touch_panels.items():
        status = panel_status.get(panel_id)
        panels_with_status.append({
            "config": config,
            "status": status
        })
    
    return {"panels": panels_with_status}

@router.get("/panels/{panel_id}")
async def get_touch_panel(panel_id: str):
    """
    Get specific touch panel configuration and status
    """
    if panel_id not in touch_panels:
        raise HTTPException(status_code=404, detail="Touch panel not found")
    
    return {
        "config": touch_panels[panel_id],
        "status": panel_status.get(panel_id),
        "recent_tasks": [task for task in configuration_tasks.values() 
                        if task.panel_id == panel_id][-10:]  # Last 10 tasks
    }

@router.post("/panels/{panel_id}/configure")
async def configure_touch_panel(panel_id: str, background_tasks: BackgroundTasks):
    """
    Configure a touch panel with Zoe-optimized settings
    """
    if panel_id not in touch_panels:
        raise HTTPException(status_code=404, detail="Touch panel not found")
    
    config = touch_panels[panel_id]
    task_id = str(uuid.uuid4())
    
    # Create configuration task
    task = ConfigurationTask(
        task_id=task_id,
        panel_id=panel_id,
        task_type="configure",
        parameters={"full_config": True},
        created_at=datetime.now()
    )
    
    configuration_tasks[task_id] = task
    
    # Run configuration in background
    background_tasks.add_task(run_touch_panel_configuration, panel_id, task_id)
    
    return {"message": "Configuration started", "task_id": task_id}

@router.post("/panels/{panel_id}/install-app")
async def install_app_on_panel(panel_id: str, app_name: str, background_tasks: BackgroundTasks):
    """
    Install a specific application on the touch panel
    """
    if panel_id not in touch_panels:
        raise HTTPException(status_code=404, detail="Touch panel not found")
    
    # Available Zoe-compatible apps
    available_apps = {
        "zoe-touch-interface": {
            "name": "Zoe Touch Interface",
            "description": "Main Zoe touch interface",
            "install_command": "install_zoe_touch_interface.sh"
        },
        "zoe-kiosk-mode": {
            "name": "Zoe Kiosk Mode", 
            "description": "Full-screen Zoe interface",
            "install_command": "install_zoe_kiosk.sh"
        },
        "home-assistant-panel": {
            "name": "Home Assistant Panel",
            "description": "Home automation control panel",
            "install_command": "install_ha_panel.sh"
        },
        "n8n-control-panel": {
            "name": "N8N Control Panel",
            "description": "Automation workflow control",
            "install_command": "install_n8n_panel.sh"
        }
    }
    
    if app_name not in available_apps:
        raise HTTPException(status_code=400, detail="App not available")
    
    task_id = str(uuid.uuid4())
    task = ConfigurationTask(
        task_id=task_id,
        panel_id=panel_id,
        task_type="install",
        parameters={"app_name": app_name, "app_config": available_apps[app_name]},
        created_at=datetime.now()
    )
    
    configuration_tasks[task_id] = task
    background_tasks.add_task(install_app_on_touch_panel, panel_id, app_name, task_id)
    
    return {"message": f"Installing {app_name}", "task_id": task_id}

@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """
    Get status of a configuration task
    """
    if task_id not in configuration_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return configuration_tasks[task_id]

@router.post("/panels/{panel_id}/deploy-zoe-config")
async def deploy_zoe_configuration(panel_id: str, background_tasks: BackgroundTasks):
    """
    Deploy complete Zoe configuration to touch panel using our knowledge
    """
    if panel_id not in touch_panels:
        raise HTTPException(status_code=404, detail="Touch panel not found")
    
    config = touch_panels[panel_id]
    
    # Create comprehensive Zoe configuration based on our knowledge
    zoe_config = {
        "zoe_instance": {
            "main_url": "http://zoe.local",
            "api_url": "http://zoe.local/api",
            "services": {
                "automation": "http://zoe.local:5678",
                "home": "http://zoe.local:8123", 
                "ollama": "http://zoe.local:11434",
                "whisper": "http://zoe.local:9001",
                "tts": "http://zoe.local:9002"
            }
        },
        "touch_panel": {
            "panel_id": panel_id,
            "panel_name": config.panel_name,
            "capabilities": config.capabilities,
            "display_settings": config.display_settings
        },
        "applications": get_recommended_apps_for_panel(config),
        "auto_discovery": {
            "enabled": True,
            "fallback_methods": ["hostname", "network_scan"],
            "cache_duration": 3600
        }
    }
    
    task_id = str(uuid.uuid4())
    task = ConfigurationTask(
        task_id=task_id,
        panel_id=panel_id,
        task_type="deploy_zoe_config",
        parameters={"zoe_config": zoe_config},
        created_at=datetime.now()
    )
    
    configuration_tasks[task_id] = task
    background_tasks.add_task(deploy_complete_zoe_config, panel_id, zoe_config, task_id)
    
    return {"message": "Deploying Zoe configuration", "task_id": task_id}

async def run_touch_panel_configuration(panel_id: str, task_id: str):
    """
    Run the actual configuration process
    """
    task = configuration_tasks[task_id]
    task.status = "running"
    
    try:
        config = touch_panels[panel_id]
        panel_ip = config.ip_address
        
        # Send configuration commands to touch panel
        configuration_script = generate_configuration_script(config)
        
        # Upload and execute configuration
        success = await send_configuration_to_panel(panel_ip, configuration_script, task)
        
        if success:
            task.status = "completed"
            task.logs.append("Configuration completed successfully")
            
            # Update panel status
            if panel_id in panel_status:
                panel_status[panel_id].status = "configured"
                panel_status[panel_id].last_seen = datetime.now()
        else:
            task.status = "failed"
            task.logs.append("Configuration failed")
            
    except Exception as e:
        task.status = "failed"
        task.logs.append(f"Configuration error: {str(e)}")

async def install_app_on_touch_panel(panel_id: str, app_name: str, task_id: str):
    """
    Install application on touch panel
    """
    task = configuration_tasks[task_id]
    task.status = "running"
    
    try:
        config = touch_panels[panel_id]
        panel_ip = config.ip_address
        
        # Generate app installation script based on Zoe knowledge
        install_script = generate_app_install_script(app_name, config)
        
        success = await send_configuration_to_panel(panel_ip, install_script, task)
        
        if success:
            task.status = "completed"
            task.logs.append(f"{app_name} installed successfully")
            
            # Update installed apps list
            if panel_id in panel_status:
                if app_name not in panel_status[panel_id].installed_apps:
                    panel_status[panel_id].installed_apps.append(app_name)
        else:
            task.status = "failed"
            
    except Exception as e:
        task.status = "failed"
        task.logs.append(f"Installation error: {str(e)}")

async def deploy_complete_zoe_config(panel_id: str, zoe_config: Dict, task_id: str):
    """
    Deploy complete Zoe configuration to touch panel
    """
    task = configuration_tasks[task_id]
    task.status = "running"
    
    try:
        config = touch_panels[panel_id]
        panel_ip = config.ip_address
        
        # Generate comprehensive deployment script
        deployment_script = generate_zoe_deployment_script(zoe_config)
        
        task.logs.append("Deploying Zoe configuration...")
        success = await send_configuration_to_panel(panel_ip, deployment_script, task)
        
        if success:
            task.status = "completed"
            task.logs.append("Zoe configuration deployed successfully")
            
            # Update panel status
            if panel_id in panel_status:
                panel_status[panel_id].status = "zoe_configured"
                panel_status[panel_id].current_config_version = "zoe_v5.0"
                panel_status[panel_id].last_seen = datetime.now()
        else:
            task.status = "failed"
            
    except Exception as e:
        task.status = "failed"
        task.logs.append(f"Deployment error: {str(e)}")

async def send_configuration_to_panel(panel_ip: str, script: str, task: ConfigurationTask) -> bool:
    """
    Send configuration script to touch panel for execution
    """
    try:
        # Send script to touch panel agent
        response = requests.post(
            f"http://{panel_ip}:8888/execute-config",
            json={"script": script, "task_id": task.task_id},
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            task.logs.extend(result.get("logs", []))
            return result.get("success", False)
        else:
            task.logs.append(f"Failed to send configuration: HTTP {response.status_code}")
            return False
            
    except Exception as e:
        task.logs.append(f"Communication error: {str(e)}")
        return False

def generate_configuration_script(config: TouchPanelConfig) -> str:
    """
    Generate configuration script based on panel config and Zoe knowledge
    """
    script = f"""#!/bin/bash
# Zoe Touch Panel Configuration Script
# Generated for panel: {config.panel_name} ({config.panel_id})

echo "🚀 Configuring touch panel for Zoe..."

# Install discovery system
cd /tmp
wget -q https://github.com/your-zoe-repo/touch-panel-discovery/archive/main.zip
unzip -q main.zip
cd touch-panel-discovery-main
sudo ./install_discovery.sh

# Configure display settings
"""
    
    if config.display_settings:
        script += f"""
# Display configuration
{generate_display_config(config.display_settings)}
"""
    
    script += f"""
# Install Zoe discovery client
pip3 install --user requests netifaces zeroconf

# Test Zoe connectivity
python3 -c "
from simple_discovery_client import find_zoe
config = find_zoe()
if config:
    print('✅ Zoe discovery successful')
    print(f'Zoe URL: {{config[\"discovery_info\"][\"url\"]}}')
else:
    print('❌ Zoe discovery failed')
    exit(1)
"

echo "✅ Touch panel configuration complete!"
"""
    
    return script

def generate_app_install_script(app_name: str, config: TouchPanelConfig) -> str:
    """
    Generate app installation script with Zoe integration
    """
    scripts = {
        "zoe-touch-interface": f"""#!/bin/bash
echo "📱 Installing Zoe Touch Interface..."

# Install dependencies
sudo apt update
sudo apt install -y chromium-browser unclutter

# Create Zoe touch interface
mkdir -p /home/pi/zoe-touch
cd /home/pi/zoe-touch

# Create simple touch interface
cat > index.html << 'EOF'
<!DOCTYPE html>
<html>
<head>
    <title>Zoe Touch Panel - {config.panel_name}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{ margin: 0; padding: 20px; font-family: Arial, sans-serif; background: #1a1a2e; color: white; }}
        .panel {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; }}
        .service {{ background: #16213e; padding: 20px; border-radius: 10px; text-align: center; cursor: pointer; }}
        .service:hover {{ background: #0f3460; }}
        h1 {{ text-align: center; color: #fff; }}
    </style>
</head>
<body>
    <h1>Zoe Assistant - {config.panel_name}</h1>
    <div class="panel">
        <div class="service" onclick="window.open('http://zoe.local', '_blank')">
            <h3>🤖 Main Zoe</h3>
            <p>AI Assistant Interface</p>
        </div>
        <div class="service" onclick="window.open('http://zoe.local:5678', '_blank')">
            <h3>⚡ Automation</h3>
            <p>N8N Workflows</p>
        </div>
        <div class="service" onclick="window.open('http://zoe.local:8123', '_blank')">
            <h3>🏠 Home Control</h3>
            <p>Home Assistant</p>
        </div>
        <div class="service" onclick="window.open('http://zoe.local:11434', '_blank')">
            <h3>🧠 AI Models</h3>
            <p>Ollama AI</p>
        </div>
    </div>
    
    <script>
        // Auto-refresh every hour to check for Zoe updates
        setTimeout(() => location.reload(), 3600000);
    </script>
</body>
</html>
EOF

# Create autostart for kiosk mode
mkdir -p /home/pi/.config/autostart
cat > /home/pi/.config/autostart/zoe-touch.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=Zoe Touch Panel
Exec=chromium-browser --kiosk --disable-infobars file:///home/pi/zoe-touch/index.html
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF

echo "✅ Zoe Touch Interface installed!"
""",
        
        "zoe-kiosk-mode": """#!/bin/bash
echo "🖥️ Installing Zoe Kiosk Mode..."

# Install kiosk dependencies
sudo apt install -y chromium-browser unclutter

# Configure auto-login
sudo raspi-config nonint do_boot_behaviour B4

# Create kiosk startup script
cat > /home/pi/start-zoe-kiosk.sh << 'EOF'
#!/bin/bash
# Hide cursor
unclutter -idle 0.5 -root &

# Start Zoe in kiosk mode
chromium-browser --noerrdialogs --disable-infobars --kiosk http://zoe.local
EOF

chmod +x /home/pi/start-zoe-kiosk.sh

# Add to autostart
mkdir -p /home/pi/.config/autostart
cat > /home/pi/.config/autostart/zoe-kiosk.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=Zoe Kiosk
Exec=/home/pi/start-zoe-kiosk.sh
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF

echo "✅ Zoe Kiosk Mode installed!"
"""
    }
    
    return scripts.get(app_name, "echo 'App not found'")

def generate_zoe_deployment_script(zoe_config: Dict) -> str:
    """
    Generate comprehensive Zoe deployment script
    """
    return f"""#!/bin/bash
# Complete Zoe Configuration Deployment
echo "🚀 Deploying complete Zoe configuration..."

# Save Zoe configuration
mkdir -p /home/pi/.zoe
cat > /home/pi/.zoe/config.json << 'EOF'
{json.dumps(zoe_config, indent=2)}
EOF

# Install all discovery components
cd /tmp
git clone https://github.com/your-zoe-repo/touch-panel-discovery.git || echo "Using local files"
cd touch-panel-discovery 2>/dev/null || cd /home/pi/zoe/services/touch-panel-discovery

# Install dependencies
pip3 install --user -r requirements.txt

# Setup discovery client
cp simple_discovery_client.py /home/pi/.zoe/
cp auto_discovery.py /home/pi/.zoe/

# Test connectivity
python3 -c "
import sys
sys.path.append('/home/pi/.zoe')
from simple_discovery_client import find_zoe
config = find_zoe()
if config:
    print('✅ Zoe connectivity verified')
else:
    print('❌ Cannot connect to Zoe')
    exit(1)
"

# Create status monitor
cat > /home/pi/.zoe/monitor.py << 'EOF'
#!/usr/bin/env python3
import time
import requests
import json

def monitor_zoe():
    while True:
        try:
            # Check Zoe status
            response = requests.get('http://zoe.local/health', timeout=5)
            status = "online" if response.status_code == 200 else "offline"
            
            # Report status back to main Zoe
            requests.post('http://zoe.local/api/touch-panels/{zoe_config["touch_panel"]["panel_id"]}/heartbeat', 
                         json={{"status": status, "timestamp": time.time()}}, timeout=2)
        except:
            pass
        
        time.sleep(60)  # Check every minute

if __name__ == '__main__':
    monitor_zoe()
EOF

chmod +x /home/pi/.zoe/monitor.py

# Start monitor service
nohup python3 /home/pi/.zoe/monitor.py > /dev/null 2>&1 &

echo "✅ Complete Zoe configuration deployed!"
echo "Touch panel is now fully configured for Zoe."
"""

def generate_display_config(display_settings: Dict) -> str:
    """Generate display configuration commands"""
    config = ""
    
    if "brightness" in display_settings:
        config += f"echo {display_settings['brightness']} | sudo tee /sys/class/backlight/*/brightness\n"
    
    if "orientation" in display_settings:
        config += f"echo 'display_rotate={display_settings['orientation']}' | sudo tee -a /boot/config.txt\n"
    
    if "resolution" in display_settings:
        config += f"# Set resolution to {display_settings['resolution']}\n"
    
    return config

def get_recommended_apps_for_panel(config: TouchPanelConfig) -> List[str]:
    """Get recommended apps based on panel type and capabilities"""
    apps = ["zoe-touch-interface"]  # Always include main interface
    
    if config.panel_type == "kiosk":
        apps.append("zoe-kiosk-mode")
    
    if "audio" in config.capabilities:
        apps.extend(["whisper-stt", "tts-interface"])
    
    if "home_automation" in config.zoe_services:
        apps.append("home-assistant-panel")
    
    if "automation" in config.zoe_services:
        apps.append("n8n-control-panel")
    
    return apps

@router.post("/panels/{panel_id}/heartbeat")
async def receive_heartbeat(panel_id: str, heartbeat_data: Dict):
    """
    Receive heartbeat from touch panel
    """
    if panel_id in panel_status:
        panel_status[panel_id].last_seen = datetime.now()
        panel_status[panel_id].status = heartbeat_data.get("status", "online")
    
    return {"message": "Heartbeat received"}

@router.get("/generate-setup-command/{panel_id}")
async def generate_setup_command(panel_id: str):
    """
    Generate a simple command that can be run on touch panel to configure it
    """
    if panel_id not in touch_panels:
        raise HTTPException(status_code=404, detail="Touch panel not found")
    
    # Generate a simple one-liner command
    setup_command = f"""curl -s http://zoe.local/api/touch-panels/setup-script/{panel_id} | bash"""
    
    return {
        "setup_command": setup_command,
        "description": "Run this command on the touch panel to auto-configure it",
        "alternative": f"wget -qO- http://zoe.local/api/touch-panels/setup-script/{panel_id} | bash"
    }

@router.get("/setup-script/{panel_id}")
async def get_setup_script(panel_id: str):
    """
    Return a setup script that can be downloaded and executed on touch panel
    """
    if panel_id not in touch_panels:
        # Create a generic setup script
        script = """#!/bin/bash
echo "🔍 Discovering and configuring for Zoe..."

# Install discovery system
pip3 install --user requests netifaces zeroconf

# Download and run discovery
curl -s http://zoe.local/api/touch-panels/discovery-client > /tmp/discovery.py
python3 /tmp/discovery.py

echo "✅ Basic Zoe configuration complete!"
"""
    else:
        config = touch_panels[panel_id]
        script = generate_configuration_script(config)
    
    return script

@router.get("/discovery-client")
async def get_discovery_client():
    """
    Return the discovery client Python script
    """
    from fastapi.responses import PlainTextResponse
    with open('/home/pi/zoe/services/touch-panel-discovery/simple_discovery_client.py', 'r') as f:
        return PlainTextResponse(f.read(), media_type='text/plain')

@router.get("/agent-script") 
async def get_agent_script():
    """
    Return the touch panel agent Python script
    """
    with open('/home/pi/zoe/services/touch-panel-discovery/touch_panel_agent.py', 'r') as f:
        return f.read()
