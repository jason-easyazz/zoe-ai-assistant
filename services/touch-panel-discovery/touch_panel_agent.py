#!/usr/bin/env python3
"""
Touch Panel Agent
=================

Lightweight agent that runs on touch panels to receive and execute 
configuration commands from the main Zoe instance.

This agent enables remote configuration without requiring SSH or manual setup.
"""

import os
import json
import time
import subprocess
import threading
import requests
from flask import Flask, request, jsonify
from datetime import datetime
import logging
import socket
import uuid

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TouchPanelAgent:
    def __init__(self, port=8888):
        self.port = port
        self.app = Flask(__name__)
        self.panel_id = self._get_panel_id()
        self.zoe_url = None
        self.config = {}
        self.setup_routes()
        
    def _get_panel_id(self):
        """Generate or load panel ID"""
        config_file = '/home/pi/.touch-panel-id'
        try:
            with open(config_file, 'r') as f:
                return f.read().strip()
        except:
            panel_id = f"panel_{socket.gethostname()}_{str(uuid.uuid4())[:8]}"
            try:
                with open(config_file, 'w') as f:
                    f.write(panel_id)
            except:
                pass
            return panel_id
    
    def setup_routes(self):
        """Setup Flask routes for the agent"""
        
        @self.app.route('/touch-panel-info', methods=['GET'])
        def get_panel_info():
            """Return touch panel information for discovery"""
            info = {
                'panel_id': self.panel_id,
                'hostname': socket.gethostname(),
                'ip_address': self._get_local_ip(),
                'agent_version': '1.0',
                'capabilities': self._detect_capabilities(),
                'system_info': self._get_system_info(),
                'zoe_configured': self.zoe_url is not None,
                'timestamp': datetime.now().isoformat()
            }
            return jsonify(info)
        
        @self.app.route('/execute-config', methods=['POST'])
        def execute_configuration():
            """Execute configuration script sent from main Zoe"""
            try:
                data = request.get_json()
                script = data.get('script', '')
                task_id = data.get('task_id', 'unknown')
                
                if not script:
                    return jsonify({'success': False, 'error': 'No script provided'})
                
                # Execute script and capture output
                logs, success = self._execute_script(script, task_id)
                
                return jsonify({
                    'success': success,
                    'logs': logs,
                    'task_id': task_id,
                    'timestamp': datetime.now().isoformat()
                })
                
            except Exception as e:
                logger.error(f"Error executing configuration: {e}")
                return jsonify({'success': False, 'error': str(e)})
        
        @self.app.route('/install-app', methods=['POST'])
        def install_application():
            """Install application from main Zoe"""
            try:
                data = request.get_json()
                app_name = data.get('app_name', '')
                app_config = data.get('app_config', {})
                
                logs, success = self._install_application(app_name, app_config)
                
                return jsonify({
                    'success': success,
                    'logs': logs,
                    'app_name': app_name
                })
                
            except Exception as e:
                return jsonify({'success': False, 'error': str(e)})
        
        @self.app.route('/status', methods=['GET'])
        def get_status():
            """Return current panel status"""
            return jsonify({
                'panel_id': self.panel_id,
                'status': 'online',
                'zoe_connected': self._test_zoe_connection(),
                'uptime': self._get_uptime(),
                'timestamp': datetime.now().isoformat()
            })
        
        @self.app.route('/discover-zoe', methods=['POST'])
        def discover_and_connect_zoe():
            """Discover and connect to Zoe instance"""
            try:
                # Use our discovery client
                from simple_discovery_client import find_zoe
                config = find_zoe(use_cache=False)
                
                if config:
                    self.zoe_url = config['discovery_info']['url']
                    self.config = config
                    
                    # Register with main Zoe
                    self._register_with_zoe()
                    
                    return jsonify({
                        'success': True,
                        'zoe_url': self.zoe_url,
                        'services': config.get('services', {})
                    })
                else:
                    return jsonify({'success': False, 'error': 'Zoe not found'})
                    
            except Exception as e:
                return jsonify({'success': False, 'error': str(e)})
        
        @self.app.route('/restart', methods=['POST'])
        def restart_panel():
            """Restart the touch panel"""
            threading.Thread(target=self._restart_system, daemon=True).start()
            return jsonify({'success': True, 'message': 'Restart initiated'})
    
    def _get_local_ip(self):
        """Get local IP address"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return '127.0.0.1'
    
    def _detect_capabilities(self):
        """Detect touch panel capabilities"""
        capabilities = ['touch']  # Assume touch capability
        
        # Check for audio
        try:
            result = subprocess.run(['aplay', '-l'], capture_output=True, text=True)
            if result.returncode == 0:
                capabilities.append('audio')
        except:
            pass
        
        # Check for camera
        try:
            if os.path.exists('/dev/video0'):
                capabilities.append('camera')
        except:
            pass
        
        # Check for display
        try:
            if os.environ.get('DISPLAY'):
                capabilities.append('display')
        except:
            pass
        
        return capabilities
    
    def _get_system_info(self):
        """Get system information"""
        info = {}
        
        try:
            # Get OS info
            with open('/etc/os-release', 'r') as f:
                for line in f:
                    if line.startswith('PRETTY_NAME='):
                        info['os'] = line.split('=')[1].strip().strip('"')
                        break
        except:
            info['os'] = 'Unknown'
        
        try:
            # Get hardware info
            with open('/proc/cpuinfo', 'r') as f:
                for line in f:
                    if line.startswith('Model'):
                        info['hardware'] = line.split(':')[1].strip()
                        break
        except:
            info['hardware'] = 'Unknown'
        
        try:
            # Get memory info
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if line.startswith('MemTotal:'):
                        info['memory'] = line.split()[1] + ' KB'
                        break
        except:
            pass
        
        return info
    
    def _execute_script(self, script, task_id):
        """Execute configuration script and return logs"""
        logs = []
        success = True
        
        try:
            # Save script to temporary file
            script_file = f'/tmp/config_script_{task_id}.sh'
            with open(script_file, 'w') as f:
                f.write(script)
            
            os.chmod(script_file, 0o755)
            
            # Execute script
            logs.append(f"Executing configuration script for task {task_id}")
            
            process = subprocess.Popen(
                ['bash', script_file],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            
            # Capture output in real-time
            for line in process.stdout:
                logs.append(line.rstrip())
                logger.info(f"Script output: {line.rstrip()}")
            
            process.wait()
            
            if process.returncode == 0:
                logs.append("✅ Configuration script completed successfully")
            else:
                logs.append(f"❌ Configuration script failed with exit code {process.returncode}")
                success = False
            
            # Clean up
            os.remove(script_file)
            
        except Exception as e:
            logs.append(f"❌ Error executing script: {str(e)}")
            success = False
        
        return logs, success
    
    def _install_application(self, app_name, app_config):
        """Install application on touch panel"""
        logs = []
        success = True
        
        try:
            logs.append(f"Installing application: {app_name}")
            
            # Get installation script from config
            install_command = app_config.get('install_command', '')
            if not install_command:
                logs.append("❌ No installation command provided")
                return logs, False
            
            # Execute installation
            process = subprocess.run(
                install_command,
                shell=True,
                capture_output=True,
                text=True
            )
            
            logs.extend(process.stdout.split('\n'))
            if process.stderr:
                logs.extend(process.stderr.split('\n'))
            
            if process.returncode == 0:
                logs.append(f"✅ {app_name} installed successfully")
            else:
                logs.append(f"❌ {app_name} installation failed")
                success = False
            
        except Exception as e:
            logs.append(f"❌ Error installing {app_name}: {str(e)}")
            success = False
        
        return logs, success
    
    def _test_zoe_connection(self):
        """Test connection to Zoe"""
        if not self.zoe_url:
            return False
        
        try:
            response = requests.get(f"{self.zoe_url}/health", timeout=5)
            return response.status_code == 200
        except:
            return False
    
    def _get_uptime(self):
        """Get system uptime"""
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_seconds = float(f.readline().split()[0])
                return f"{uptime_seconds:.0f} seconds"
        except:
            return "Unknown"
    
    def _register_with_zoe(self):
        """Register this panel with main Zoe instance"""
        if not self.zoe_url:
            return
        
        try:
            panel_data = {
                'panel_id': self.panel_id,
                'panel_name': f"Touch Panel {socket.gethostname()}",
                'ip_address': self._get_local_ip(),
                'panel_type': 'auto-discovered',
                'capabilities': self._detect_capabilities(),
                'zoe_services': ['automation', 'home', 'ai']
            }
            
            response = requests.post(
                f"{self.zoe_url}/api/touch-panels/register",
                json=panel_data,
                timeout=10
            )
            
            if response.status_code == 200:
                logger.info("✅ Successfully registered with Zoe")
            else:
                logger.warning(f"⚠️ Failed to register with Zoe: {response.status_code}")
                
        except Exception as e:
            logger.error(f"❌ Error registering with Zoe: {e}")
    
    def _restart_system(self):
        """Restart the system"""
        time.sleep(2)  # Give time for response to be sent
        subprocess.run(['sudo', 'reboot'])
    
    def start_heartbeat(self):
        """Start heartbeat to main Zoe instance"""
        def heartbeat_loop():
            while True:
                if self.zoe_url:
                    try:
                        heartbeat_data = {
                            'status': 'online',
                            'timestamp': time.time(),
                            'capabilities': self._detect_capabilities()
                        }
                        
                        requests.post(
                            f"{self.zoe_url}/api/touch-panels/{self.panel_id}/heartbeat",
                            json=heartbeat_data,
                            timeout=5
                        )
                    except:
                        pass
                
                time.sleep(60)  # Heartbeat every minute
        
        threading.Thread(target=heartbeat_loop, daemon=True).start()
    
    def run(self):
        """Start the touch panel agent"""
        logger.info(f"🚀 Starting Touch Panel Agent on port {self.port}")
        logger.info(f"📱 Panel ID: {self.panel_id}")
        
        # Try to discover Zoe on startup
        try:
            from simple_discovery_client import find_zoe
            config = find_zoe()
            if config:
                self.zoe_url = config['discovery_info']['url']
                self.config = config
                logger.info(f"✅ Found Zoe at: {self.zoe_url}")
                self._register_with_zoe()
        except:
            logger.info("⚠️ Zoe not found during startup - will be available for discovery")
        
        # Start heartbeat
        self.start_heartbeat()
        
        # Start Flask app
        self.app.run(host='0.0.0.0', port=self.port, debug=False)

def main():
    """Main function to start the touch panel agent"""
    agent = TouchPanelAgent()
    agent.run()

if __name__ == '__main__':
    main()




