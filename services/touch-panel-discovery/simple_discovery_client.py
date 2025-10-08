#!/usr/bin/env python3
"""
Simple Zoe Discovery Client for Touch Panels
===========================================

Lightweight client that touch panel apps can use to find and connect to Zoe.
Designed to be embedded in touch panel interfaces with minimal dependencies.

Usage:
    from simple_discovery_client import find_zoe
    
    zoe_config = find_zoe()
    if zoe_config:
        print(f"Zoe found at: {zoe_config['url']}")
"""

import json
import time
import socket
import subprocess
import requests
from typing import Optional, Dict, List

class SimpleZoeDiscovery:
    """
    Lightweight Zoe discovery for touch panels
    """
    
    def __init__(self):
        self.timeout = 10
        self.config_file = '/home/pi/.zoe-touch-panel.json'
    
    def find_zoe(self, use_cache: bool = True) -> Optional[Dict]:
        """
        Find Zoe instance using progressive discovery methods
        
        Args:
            use_cache: Whether to use cached configuration first
            
        Returns:
            Dict with Zoe configuration or None
        """
        
        # Try cached config first
        if use_cache:
            cached = self._load_cached_config()
            if cached and self._test_connection(cached.get('url')):
                return cached
        
        # Progressive discovery
        methods = [
            self._try_zoe_local,
            self._try_common_ips,
            self._try_network_scan_simple
        ]
        
        for method in methods:
            try:
                result = method()
                if result:
                    # Save successful discovery
                    self._save_config(result)
                    return result
            except Exception as e:
                continue
        
        return None
    
    def _try_zoe_local(self) -> Optional[Dict]:
        """Try zoe.local hostname"""
        hostnames = ['zoe.local', 'zoe-ai.local']
        
        for hostname in hostnames:
            for protocol in ['http', 'https']:
                url = f'{protocol}://{hostname}'
                if self._test_connection(url):
                    return self._get_full_config(url, 'hostname')
        
        return None
    
    def _try_common_ips(self) -> Optional[Dict]:
        """Try common IP addresses for Zoe"""
        common_ips = [
            '192.168.1.60',  # Current Zoe IP
            '192.168.1.100',
            '192.168.0.60',
            '192.168.0.100',
            '10.0.0.60',
            '10.0.0.100'
        ]
        
        for ip in common_ips:
            url = f'http://{ip}'
            if self._test_connection(url):
                return self._get_full_config(url, 'common_ip')
        
        return None
    
    def _try_network_scan_simple(self) -> Optional[Dict]:
        """Simple network scan for Zoe"""
        # Get local IP to determine network
        try:
            local_ip = self._get_local_ip()
            if not local_ip:
                return None
            
            # Extract network base (e.g., 192.168.1.x)
            ip_parts = local_ip.split('.')
            network_base = '.'.join(ip_parts[:3])
            
            # Scan common Zoe IPs in the network
            for i in [1, 50, 60, 100, 150, 200]:
                test_ip = f'{network_base}.{i}'
                if test_ip == local_ip:
                    continue
                    
                url = f'http://{test_ip}'
                if self._test_connection(url, timeout=1):
                    return self._get_full_config(url, 'network_scan')
            
        except Exception:
            pass
        
        return None
    
    def _get_local_ip(self) -> Optional[str]:
        """Get local IP address"""
        try:
            # Connect to a remote address to get local IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            return local_ip
        except:
            return None
    
    def _test_connection(self, url: str, timeout: int = 3) -> bool:
        """Test if URL responds and is Zoe"""
        try:
            # Quick test for Zoe headers
            response = requests.head(url, timeout=timeout)
            if response.status_code == 200:
                headers = response.headers
                if any(h.startswith('X-Zoe-') for h in headers):
                    return True
            
            # Test discovery endpoint
            response = requests.get(f'{url}/api/services', timeout=timeout)
            if response.status_code == 200:
                data = response.json()
                return 'zoe' in data
                
        except:
            pass
        
        return False
    
    def _get_full_config(self, url: str, method: str) -> Optional[Dict]:
        """Get full Zoe configuration"""
        try:
            response = requests.get(f'{url}/api/services', timeout=5)
            if response.status_code == 200:
                config = response.json()
                config['discovery_info'] = {
                    'url': url,
                    'method': method,
                    'timestamp': time.time()
                }
                return config
        except:
            pass
        
        return None
    
    def _load_cached_config(self) -> Optional[Dict]:
        """Load cached configuration"""
        try:
            with open(self.config_file, 'r') as f:
                config = json.load(f)
                # Check if cache is less than 1 hour old
                if time.time() - config.get('discovery_info', {}).get('timestamp', 0) < 3600:
                    return config
        except:
            pass
        
        return None
    
    def _save_config(self, config: Dict):
        """Save configuration to cache"""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
        except:
            pass

# Convenience functions for easy integration

def find_zoe(use_cache: bool = True) -> Optional[Dict]:
    """
    Simple function to find Zoe instance
    
    Returns:
        Dict with Zoe configuration or None
    """
    discovery = SimpleZoeDiscovery()
    return discovery.find_zoe(use_cache)

def get_zoe_url() -> Optional[str]:
    """
    Get just the Zoe URL for quick access
    
    Returns:
        Zoe URL string or None
    """
    config = find_zoe()
    if config:
        return config.get('discovery_info', {}).get('url')
    return None

def get_zoe_services() -> Optional[Dict]:
    """
    Get available Zoe services
    
    Returns:
        Dict of services or None
    """
    config = find_zoe()
    if config:
        return config.get('services', {})
    return None

def test_discovery():
    """Test the discovery system"""
    print("🔍 Testing Zoe discovery...")
    
    config = find_zoe(use_cache=False)  # Force fresh discovery
    
    if config:
        print("✅ Zoe found!")
        info = config.get('discovery_info', {})
        print(f"   URL: {info.get('url')}")
        print(f"   Method: {info.get('method')}")
        
        services = config.get('services', {})
        if services:
            print("📋 Available services:")
            for name, service in services.items():
                print(f"   • {service.get('name', name)}: {service.get('url')}")
        
        return True
    else:
        print("❌ Zoe not found")
        print("💡 Troubleshooting:")
        print("   1. Ensure Zoe is running")
        print("   2. Check network connection")
        print("   3. Try accessing http://zoe.local manually")
        
        return False

if __name__ == '__main__':
    test_discovery()




