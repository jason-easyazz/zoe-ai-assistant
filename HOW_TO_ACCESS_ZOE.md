# How to Access Your Zoe AI Assistant

## Quick Access (Works on ANY Network)

### From the Raspberry Pi:
```bash
http://localhost:8080        # Main UI
http://localhost:8080/developer/  # Developer Dashboard
http://localhost:8000/docs   # API Documentation
```

### From Other Devices:
1. Find your Pi's IP address:
   ```bash
   hostname -I | cut -d' ' -f1
   ```

2. Access from any device on same network:
   ```
   http://[your-pi-ip]:8080        # Main UI
   http://[your-pi-ip]:8080/developer/  # Developer Dashboard
   ```

### Using Hostname (if configured):
```
http://raspberrypi.local:8080
http://zoe.local:8080  # If you set custom hostname
```

## No Configuration Needed!
- The system uses relative URLs
- Works on any network automatically
- No IP addresses to change
- Fully portable

## Testing Access
```bash
# From the Pi
curl http://localhost:8080/api/health

# From another device (replace with your Pi's IP)
curl http://[your-pi-ip]:8080/api/health
```
