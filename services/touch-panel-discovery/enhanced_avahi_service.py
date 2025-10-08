#!/usr/bin/env python3
"""
Enhanced Avahi/mDNS Service Announcements for Zoe
=================================================

This script enhances the default Avahi announcements to make Zoe
more discoverable by touch panels and other clients.

For mass adoption - announces multiple service types for better compatibility.
"""

import subprocess
import json
import time
import os
import socket
from typing import Dict, List

class ZoeAvahiAnnouncer:
    """Enhanced Avahi service announcements for Zoe"""
    
    def __init__(self):
        self.services_config = {
            'zoe-main': {
                'type': '_http._tcp',
                'port': 80,
                'txt_records': {
                    'version': '5.0',
                    'type': 'zoe-assistant',
                    'discovery': 'http://zoe.local/api/services',
                    'services': 'automation,home,ai,tts,whisper',
                    'auth': 'enabled'
                }
            },
            'zoe-https': {
                'type': '_https._tcp', 
                'port': 443,
                'txt_records': {
                    'version': '5.0',
                    'type': 'zoe-assistant',
                    'discovery': 'https://zoe.local/api/services'
                }
            },
            'zoe-api': {
                'type': '_zoe-api._tcp',
                'port': 8000,
                'txt_records': {
                    'version': '5.0',
                    'api': 'core',
                    'endpoint': '/api'
                }
            },
            'zoe-automation': {
                'type': '_http._tcp',
                'port': 5678,
                'txt_records': {
                    'version': '5.0',
                    'type': 'n8n-automation',
                    'service': 'workflow'
                }
            }
        }
        
    def create_avahi_service_file(self, service_name: str, config: Dict, output_dir: str = '/etc/avahi/services'):
        """Create Avahi service file for a service"""
        
        # Build TXT records
        txt_records = ''
        for key, value in config['txt_records'].items():
            txt_records += f'    <txt-record>{key}={value}</txt-record>\n'
        
        service_xml = f"""<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">{service_name} on %h</name>
  <service>
    <type>{config['type']}</type>
    <port>{config['port']}</port>
{txt_records.rstrip()}
  </service>
</service-group>"""
        
        # Write service file
        filename = f"{output_dir}/{service_name}.service"
        try:
            with open(filename, 'w') as f:
                f.write(service_xml)
            return filename
        except PermissionError:
            # Try alternative location for non-root users
            alt_dir = '/home/pi/zoe/services/avahi'
            os.makedirs(alt_dir, exist_ok=True)
            filename = f"{alt_dir}/{service_name}.service"
            with open(filename, 'w') as f:
                f.write(service_xml)
            return filename
    
    def announce_all_services(self):
        """Create and announce all Zoe services"""
        created_files = []
        
        for service_name, config in self.services_config.items():
            try:
                filename = self.create_avahi_service_file(service_name, config)
                created_files.append(filename)
                print(f"✅ Created service file: {filename}")
            except Exception as e:
                print(f"❌ Failed to create {service_name}: {e}")
        
        # Reload Avahi to pick up new services
        try:
            subprocess.run(['sudo', 'systemctl', 'reload', 'avahi-daemon'], check=True)
            print("✅ Avahi daemon reloaded")
        except subprocess.CalledProcessError as e:
            print(f"⚠️  Could not reload Avahi daemon: {e}")
            print("   You may need to run: sudo systemctl reload avahi-daemon")
        
        return created_files
    
    def create_dynamic_announcements(self):
        """Create dynamic service announcements based on running containers"""
        try:
            # Get running Docker containers
            result = subprocess.run(['docker', 'ps', '--format', 'json'], 
                                  capture_output=True, text=True, check=True)
            
            containers = []
            for line in result.stdout.strip().split('\n'):
                if line:
                    containers.append(json.loads(line))
            
            # Find Zoe services
            zoe_services = {}
            for container in containers:
                name = container.get('Names', '')
                if 'zoe-' in name:
                    ports = container.get('Ports', '')
                    # Extract port info (simplified)
                    if ':' in ports:
                        port_info = ports.split('->')[0].split(':')[-1] if '->' in ports else '80'
                        try:
                            port = int(port_info)
                            service_name = name.replace('zoe-', '')
                            zoe_services[service_name] = port
                        except ValueError:
                            continue
            
            # Create service announcements for discovered services
            for service_name, port in zoe_services.items():
                config = {
                    'type': '_http._tcp',
                    'port': port,
                    'txt_records': {
                        'version': '5.0',
                        'type': f'zoe-{service_name}',
                        'container': name
                    }
                }
                
                try:
                    filename = self.create_avahi_service_file(f'zoe-{service_name}', config)
                    print(f"✅ Dynamic service created: {filename}")
                except Exception as e:
                    print(f"❌ Failed to create dynamic service {service_name}: {e}")
                    
        except Exception as e:
            print(f"⚠️  Could not create dynamic announcements: {e}")

def setup_enhanced_discovery():
    """Setup enhanced mDNS discovery for Zoe"""
    print("🚀 Setting up enhanced Zoe discovery...")
    
    announcer = ZoeAvahiAnnouncer()
    
    # Create static service announcements
    announcer.announce_all_services()
    
    # Create dynamic announcements based on running services
    announcer.create_dynamic_announcements()
    
    print("\n✅ Enhanced discovery setup complete!")
    print("Touch panels should now be able to discover Zoe more easily.")
    print("\nTo verify, run: avahi-browse -at")

def main():
    setup_enhanced_discovery()

if __name__ == '__main__':
    main()




