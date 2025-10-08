#!/usr/bin/env python3
"""
Zoe Touch Panel Auto-Discovery System
=====================================

Multi-layer discovery system that works without any technical setup:
1. mDNS/Bonjour discovery (primary)
2. Network scanning (secondary) 
3. Manual IP fallback (tertiary)
4. Cloud discovery endpoint (future)

For mass adoption - zero configuration required.
"""

import socket
import json
import time
import threading
import subprocess
import requests
from typing import Optional, Dict, List, Tuple
import netifaces
import ipaddress
from zeroconf import ServiceBrowser, Zeroconf, ServiceListener
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ZoeDiscoveryListener(ServiceListener):
    """Listens for Zoe mDNS announcements"""
    
    def __init__(self):
        self.discovered_services = {}
        self.zoe_instance = None
        
    def remove_service(self, zeroconf, type, name):
        logger.info(f"Service {name} removed")
        if name in self.discovered_services:
            del self.discovered_services[name]
    
    def add_service(self, zeroconf, type, name):
        info = zeroconf.get_service_info(type, name)
        if info:
            # Look for Zoe services
            if b'zoe' in info.name.lower() or b'zoe' in str(info.properties).lower():
                address = socket.inet_ntoa(info.addresses[0])
                port = info.port
                
                self.discovered_services[name] = {
                    'address': address,
                    'port': port,
                    'properties': info.properties
                }
                
                # Test if this is the main Zoe instance
                if self._test_zoe_instance(address, port):
                    self.zoe_instance = {
                        'address': address,
                        'port': port,
                        'url': f'http://{address}:{port}' if port != 80 else f'http://{address}',
                        'discovery_method': 'mDNS'
                    }
                    logger.info(f"✅ Found Zoe main instance at {address}:{port}")
    
    def _test_zoe_instance(self, address: str, port: int) -> bool:
        """Test if this is a Zoe instance by checking for discovery endpoint"""
        try:
            url = f'http://{address}:{port}/api/services' if port != 80 else f'http://{address}/api/services'
            response = requests.get(url, timeout=3)
            if response.status_code == 200:
                data = response.json()
                return 'zoe' in data and 'services' in data
        except:
            pass
        return False

class ZoeAutoDiscovery:
    """
    Main auto-discovery class for touch panels to find Zoe instances
    """
    
    def __init__(self):
        self.discovered_instances = []
        self.primary_instance = None
        self.discovery_running = False
        
    def discover_zoe(self, timeout: int = 10) -> Optional[Dict]:
        """
        Main discovery method - tries all methods in order of reliability
        
        Returns:
            Dict with Zoe instance details or None if not found
        """
        logger.info("🔍 Starting Zoe auto-discovery...")
        
        # Method 1: mDNS Discovery (most reliable)
        zoe_instance = self._discover_via_mdns(timeout=5)
        if zoe_instance:
            return zoe_instance
            
        # Method 2: Known hostname attempts
        zoe_instance = self._discover_via_known_hostnames()
        if zoe_instance:
            return zoe_instance
            
        # Method 3: Network scanning (fallback)
        zoe_instance = self._discover_via_network_scan()
        if zoe_instance:
            return zoe_instance
            
        # Method 4: Manual configuration prompt
        return self._prompt_manual_configuration()
    
    def _discover_via_mdns(self, timeout: int = 5) -> Optional[Dict]:
        """Discovery using mDNS/Bonjour (primary method)"""
        logger.info("🎯 Trying mDNS discovery...")
        
        try:
            zeroconf = Zeroconf()
            listener = ZoeDiscoveryListener()
            
            # Listen for HTTP services
            browser = ServiceBrowser(zeroconf, "_http._tcp.local.", listener)
            
            # Wait for discovery
            time.sleep(timeout)
            
            if listener.zoe_instance:
                zeroconf.close()
                logger.info(f"✅ mDNS discovery successful: {listener.zoe_instance['address']}")
                return listener.zoe_instance
                
            zeroconf.close()
            
        except Exception as e:
            logger.warning(f"mDNS discovery failed: {e}")
        
        return None
    
    def _discover_via_known_hostnames(self) -> Optional[Dict]:
        """Try known Zoe hostnames"""
        logger.info("🏠 Trying known hostnames...")
        
        hostnames = [
            'zoe.local',
            'zoe-ai.local', 
            'zoe-assistant.local',
            'assistant.local'
        ]
        
        for hostname in hostnames:
            try:
                # Test both HTTP and HTTPS
                for protocol in ['http', 'https']:
                    url = f'{protocol}://{hostname}'
                    if self._test_zoe_endpoint(url):
                        logger.info(f"✅ Found Zoe at {url}")
                        return {
                            'address': hostname,
                            'port': 443 if protocol == 'https' else 80,
                            'url': url,
                            'discovery_method': 'hostname'
                        }
            except:
                continue
                
        return None
    
    def _discover_via_network_scan(self) -> Optional[Dict]:
        """Scan local network for Zoe instances (fallback method)"""
        logger.info("🔍 Scanning local network...")
        
        # Get local network interfaces
        networks = self._get_local_networks()
        
        for network in networks:
            logger.info(f"Scanning {network}...")
            zoe_instance = self._scan_network_range(network)
            if zoe_instance:
                return zoe_instance
                
        return None
    
    def _get_local_networks(self) -> List[str]:
        """Get list of local network ranges to scan"""
        networks = []
        
        try:
            for interface in netifaces.interfaces():
                addrs = netifaces.ifaddresses(interface)
                if netifaces.AF_INET in addrs:
                    for addr in addrs[netifaces.AF_INET]:
                        if 'addr' in addr and 'netmask' in addr:
                            ip = addr['addr']
                            netmask = addr['netmask']
                            
                            # Skip loopback
                            if ip.startswith('127.'):
                                continue
                                
                            # Calculate network
                            network = ipaddress.IPv4Network(f"{ip}/{netmask}", strict=False)
                            networks.append(str(network))
        except Exception as e:
            logger.warning(f"Error getting networks: {e}")
            # Fallback to common networks
            networks = ['192.168.1.0/24', '192.168.0.0/24', '10.0.0.0/24']
            
        return networks
    
    def _scan_network_range(self, network_range: str) -> Optional[Dict]:
        """Scan a network range for Zoe instances"""
        try:
            network = ipaddress.IPv4Network(network_range)
            
            # Limit scan to reasonable size networks
            if network.num_addresses > 256:
                logger.warning(f"Network {network_range} too large, skipping")
                return None
            
            # Common Zoe ports to check
            ports = [80, 443, 8000, 8080]
            
            for ip in network.hosts():
                for port in ports:
                    if self._test_zoe_endpoint(f'http://{ip}:{port}'):
                        logger.info(f"✅ Found Zoe at {ip}:{port}")
                        return {
                            'address': str(ip),
                            'port': port,
                            'url': f'http://{ip}:{port}' if port != 80 else f'http://{ip}',
                            'discovery_method': 'network_scan'
                        }
                        
        except Exception as e:
            logger.warning(f"Network scan failed: {e}")
            
        return None
    
    def _test_zoe_endpoint(self, url: str) -> bool:
        """Test if URL is a Zoe instance"""
        try:
            # Quick test - check for Zoe headers or discovery endpoint
            response = requests.get(f'{url}/api/services', timeout=2)
            if response.status_code == 200:
                data = response.json()
                return 'zoe' in data and 'services' in data
                
            # Fallback - check for Zoe headers
            response = requests.head(url, timeout=2)
            headers = response.headers
            return (
                'X-Zoe-Version' in headers or
                'X-Zoe-Services' in headers or
                'X-Zoe-Discovery' in headers
            )
            
        except:
            return False
    
    def _prompt_manual_configuration(self) -> Optional[Dict]:
        """Last resort - prompt for manual configuration"""
        logger.info("❓ No Zoe instance found automatically")
        logger.info("💡 Manual configuration options:")
        logger.info("   1. Check if Zoe is running on the main device")
        logger.info("   2. Ensure both devices are on the same network")  
        logger.info("   3. Try accessing http://192.168.1.60 directly")
        logger.info("   4. Contact your Zoe administrator")
        
        # In a GUI environment, this would show a configuration dialog
        # For now, return None to indicate manual setup needed
        return None
    
    def get_zoe_config(self, zoe_instance: Dict) -> Optional[Dict]:
        """Get full configuration from discovered Zoe instance"""
        try:
            url = zoe_instance['url']
            response = requests.get(f'{url}/api/services', timeout=5)
            if response.status_code == 200:
                config = response.json()
                config['discovery'] = zoe_instance
                return config
        except Exception as e:
            logger.error(f"Failed to get config: {e}")
        
        return None
    
    def save_discovered_config(self, config: Dict, config_file: str = '/home/pi/.zoe-config.json'):
        """Save discovered configuration for future use"""
        try:
            with open(config_file, 'w') as f:
                json.dump(config, f, indent=2)
            logger.info(f"✅ Configuration saved to {config_file}")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
    
    def load_saved_config(self, config_file: str = '/home/pi/.zoe-config.json') -> Optional[Dict]:
        """Load previously saved configuration"""
        try:
            with open(config_file, 'r') as f:
                return json.load(f)
        except:
            return None

def main():
    """Main discovery function for testing"""
    discovery = ZoeAutoDiscovery()
    
    # Try to load saved config first
    saved_config = discovery.load_saved_config()
    if saved_config:
        logger.info("📁 Found saved configuration")
        # Test if saved config still works
        discovery_info = saved_config.get('discovery', {})
        if discovery_info and discovery._test_zoe_endpoint(discovery_info.get('url', '')):
            logger.info("✅ Saved configuration still valid")
            return saved_config
        else:
            logger.info("❌ Saved configuration outdated, discovering fresh...")
    
    # Discover Zoe instance
    zoe_instance = discovery.discover_zoe()
    
    if zoe_instance:
        logger.info(f"✅ Zoe discovered successfully!")
        logger.info(f"   Address: {zoe_instance['address']}")
        logger.info(f"   URL: {zoe_instance['url']}")
        logger.info(f"   Method: {zoe_instance['discovery_method']}")
        
        # Get full configuration
        config = discovery.get_zoe_config(zoe_instance)
        if config:
            discovery.save_discovered_config(config)
            
            # Display available services
            logger.info("\n📋 Available services:")
            for service_name, service_info in config.get('services', {}).items():
                logger.info(f"   • {service_info['name']}: {service_info['url']}")
                
            return config
    else:
        logger.error("❌ Could not discover Zoe instance")
        logger.info("💡 Please ensure:")
        logger.info("   1. Zoe is running on the main device") 
        logger.info("   2. Both devices are on the same WiFi network")
        logger.info("   3. Firewall allows local network access")
        
    return None

if __name__ == '__main__':
    main()




