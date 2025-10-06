# Zoe Touch Panel Auto-Discovery System

## Overview

This system enables touch panels and other client devices to automatically find and connect to Zoe instances without any technical configuration. It's designed for **mass adoption** - users just need to power on their touch panel and it will find Zoe automatically.

## How It Works

### Multi-Layer Discovery

1. **mDNS/Bonjour Discovery** (Primary)
   - Uses standard mDNS protocols supported by all modern devices
   - Announces Zoe services on the local network
   - Works with phones, tablets, computers automatically

2. **Known Hostname Discovery** (Secondary)
   - Tries common Zoe hostnames like `zoe.local`
   - Falls back to `zoe-ai.local`, `assistant.local`
   - Works even if mDNS is partially broken

3. **Network Scanning** (Tertiary)
   - Intelligently scans local network for Zoe instances
   - Checks common IP addresses and ports
   - Last resort when other methods fail

4. **Manual Configuration** (Fallback)
   - Provides clear instructions for manual setup
   - Guides users through troubleshooting steps

### Discovery Endpoints

Zoe provides a standardized discovery endpoint at `/api/services` that returns:

```json
{
  "zoe": {
    "name": "Zoe AI Assistant",
    "version": "5.0", 
    "hostname": "zoe.local",
    "main_url": "http://192.168.1.60",
    "api_url": "http://192.168.1.60/api",
    "discovery_method": "mDNS"
  },
  "services": {
    "automation": {
      "name": "N8N Automation",
      "url": "http://192.168.1.60:5678",
      "type": "workflow"
    },
    "home": {
      "name": "Home Assistant",
      "url": "http://192.168.1.60:8123", 
      "type": "home_automation"
    },
    "ollama": {
      "name": "Ollama AI",
      "url": "http://192.168.1.60:11434",
      "type": "ai_api"
    }
  }
}
```

## Installation

### Quick Install

```bash
cd /home/pi/zoe/services/touch-panel-discovery
chmod +x install_discovery.sh
./install_discovery.sh
```

### Manual Setup

1. Install dependencies:
```bash
pip3 install --user requests netifaces zeroconf
```

2. Setup enhanced mDNS announcements:
```bash
python3 enhanced_avahi_service.py
```

3. Test discovery:
```bash
python3 simple_discovery_client.py
```

## Usage

### For Touch Panel Apps

```python
from simple_discovery_client import find_zoe

# Discover Zoe automatically
config = find_zoe()

if config:
    zoe_url = config['discovery_info']['url']
    services = config['services']
    print(f"Connected to Zoe at: {zoe_url}")
else:
    print("Zoe not found - check network connection")
```

### Command Line Tools

```bash
# Quick discovery test
find-zoe

# Setup enhanced announcements
setup-zoe-discovery

# GUI setup tool for touch panels
python3 /home/pi/zoe/scripts/touch-panel/touch_panel_setup.py
```

### Direct Integration

For minimal dependencies, copy just `simple_discovery_client.py` into your touch panel app:

```python
import simple_discovery_client

# Get Zoe URL
zoe_url = simple_discovery_client.get_zoe_url()

# Get available services  
services = simple_discovery_client.get_zoe_services()
```

## Mass Adoption Features

### Zero Configuration
- No IP addresses to configure
- No hostnames to remember
- No network settings to change
- Works out of the box

### Universal Compatibility
- Works with iOS, Android, Windows, Linux, macOS
- Uses standard mDNS/Bonjour protocols
- Falls back to network scanning if needed
- Provides manual configuration guidance

### Intelligent Fallbacks
- Multiple discovery methods ensure connection
- Caches successful configurations
- Retries failed connections automatically
- Provides clear error messages

### Plug-and-Play Experience
1. Power on touch panel
2. Touch panel automatically finds Zoe
3. User starts using Zoe immediately
4. No technical setup required

## Troubleshooting

### Common Issues

**Touch panel can't find Zoe:**
1. Ensure both devices on same WiFi network
2. Check that Zoe is running (`docker ps`)
3. Test manual access: `http://zoe.local`
4. Restart Avahi: `sudo systemctl restart avahi-daemon`

**mDNS not working:**
1. Check Avahi status: `sudo systemctl status avahi-daemon`
2. Verify service files: `ls /etc/avahi/services/`
3. Test discovery: `avahi-browse -at`

**Network scanning fails:**
1. Check firewall settings
2. Ensure local network allows scanning
3. Try direct IP access

### Debug Commands

```bash
# Test mDNS announcements
avahi-browse -at

# Test discovery endpoint
curl http://zoe.local/api/services

# Check running services
docker ps | grep zoe

# Test network connectivity
ping zoe.local
```

## Architecture

### Discovery Flow

```
Touch Panel Startup
        ↓
    Load Cached Config?
        ↓ (if invalid)
    Try mDNS Discovery
        ↓ (if failed)
    Try Known Hostnames
        ↓ (if failed)
    Try Network Scanning
        ↓ (if failed)
    Manual Configuration
        ↓
    Save Configuration
        ↓
    Connect to Zoe
```

### Service Announcements

The system announces multiple service types for maximum compatibility:

- `_http._tcp.local` - Standard HTTP service
- `_https._tcp.local` - Secure HTTP service  
- `_zoe-api._tcp.local` - Custom Zoe API service

Each announcement includes TXT records with:
- Version information
- Service capabilities
- Discovery endpoints
- Authentication requirements

## Security Considerations

### Network Security
- Discovery only works on local networks
- No external internet requirements
- Respects firewall configurations
- Uses standard mDNS security practices

### Authentication
- Discovery provides information about auth requirements
- Touch panels must still authenticate with Zoe
- No sensitive information in discovery announcements
- Follows Zoe's existing security model

## Future Enhancements

### Planned Features
- Cloud-based discovery for remote access
- QR code configuration for easier setup
- Advanced network topology detection
- Integration with enterprise network management

### Extensibility
- Plugin system for custom discovery methods
- Configurable discovery timeouts and retries
- Support for multiple Zoe instances
- Load balancing for high-availability setups

This system makes Zoe truly plug-and-play for mass adoption while maintaining security and reliability.




