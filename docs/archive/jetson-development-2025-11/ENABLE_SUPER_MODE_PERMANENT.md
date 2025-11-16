# 🚀 Enable Super Mode Permanently on Jetson Orin NX

## What is Super Mode?

Super Mode consists of two components:
1. **nvpmodel MAXN** - Sets power mode to maximum (40W for Orin NX 16GB)
2. **jetson_clocks** - Maxes out CPU, GPU, and memory clocks

## Current Status

- **nvpmodel**: ✅ **Persists across reboots automatically**
- **jetson_clocks**: ❌ **Resets on reboot** (needs to be made permanent)

## Making Super Mode Permanent

### Step 1: Enable MAXN Mode (Persists Automatically)

```bash
sudo nvpmodel -m 0
```

This setting is **automatically saved** and will persist across reboots.

Verify current mode:
```bash
sudo nvpmodel -q
```

### Step 2: Make jetson_clocks Permanent

**Option A: Systemd Service (Recommended)**

Create a systemd service to run `jetson_clocks` on boot:

```bash
# Create service file
sudo tee /etc/systemd/system/jetson-clocks.service << 'EOF'
[Unit]
Description=Jetson Clocks - Maximize Performance
After=nvpmodel.service

[Service]
Type=oneshot
ExecStart=/usr/bin/jetson_clocks
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

# Enable the service
sudo systemctl daemon-reload
sudo systemctl enable jetson-clocks.service
sudo systemctl start jetson-clocks.service
```

**Option B: Add to rc.local (Alternative)**

```bash
# Edit rc.local
sudo nano /etc/rc.local

# Add before 'exit 0':
/usr/bin/jetson_clocks

# Make executable
sudo chmod +x /etc/rc.local
```

**Option C: Add to Crontab (Alternative)**

```bash
sudo crontab -e

# Add this line:
@reboot /usr/bin/jetson_clocks
```

### Step 3: Verify Super Mode is Active

```bash
# Check power mode
sudo nvpmodel -q

# Check clock speeds
sudo jetson_clocks --show

# Monitor with jtop
sudo jtop
```

## Complete Setup Script

Run this once to enable Super Mode permanently:

```bash
#!/bin/bash
# Enable Super Mode Permanently

echo "🚀 Enabling Super Mode permanently..."

# Step 1: Set MAXN mode (persists automatically)
echo "Setting MAXN power mode..."
sudo nvpmodel -m 0

# Step 2: Run jetson_clocks now
echo "Maximizing clocks..."
sudo jetson_clocks

# Step 3: Create systemd service for jetson_clocks
echo "Creating systemd service..."
sudo tee /etc/systemd/system/jetson-clocks.service << 'EOF'
[Unit]
Description=Jetson Clocks - Maximize Performance
After=nvpmodel.service

[Service]
Type=oneshot
ExecStart=/usr/bin/jetson_clocks
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

# Step 4: Enable and start service
echo "Enabling jetson-clocks service..."
sudo systemctl daemon-reload
sudo systemctl enable jetson-clocks.service
sudo systemctl start jetson-clocks.service

# Step 5: Verify
echo ""
echo "✅ Super Mode enabled permanently!"
echo ""
echo "Current power mode:"
sudo nvpmodel -q
echo ""
echo "Clock status:"
sudo jetson_clocks --show
echo ""
echo "Super Mode will now activate automatically on every boot."
```

## Expected Performance Impact

| Metric | Before | After Super Mode | Improvement |
|--------|--------|------------------|-------------|
| Response Time | 1.59s | **~0.8s** | **2x faster** |
| Tokens/Second | ~18 | **~35** | **2x faster** |
| GPU Clock | Variable | **Max (locked)** | Consistent |
| CPU Clock | Variable | **Max (locked)** | Consistent |

## Verification

After reboot, verify Super Mode is active:

```bash
# Check power mode
sudo nvpmodel -q
# Should show: NV Power Mode: MAXN

# Check if jetson_clocks service is running
systemctl status jetson-clocks.service
# Should show: active (exited)

# Check actual clock speeds
sudo jetson_clocks --show
# Should show maximum frequencies
```

## To Disable Super Mode (If Needed)

```bash
# Disable jetson_clocks service
sudo systemctl disable jetson-clocks.service
sudo systemctl stop jetson-clocks.service

# Switch to lower power mode (e.g., 15W mode)
sudo nvpmodel -m 2

# Reboot to apply
sudo reboot
```

## Additional Optimizations

Once Super Mode is permanent, consider:

1. **Disable Desktop GUI** (frees 800MB RAM):
```bash
sudo systemctl set-default multi-user.target
sudo reboot
```

2. **Add Swap Space** (if needed):
```bash
sudo fallocate -l 8G /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
# Add to /etc/fstab to make permanent
```

3. **Monitor Temperature**:
```bash
# Install jtop
sudo pip3 install -U jetson-stats
sudo jtop
```

## Troubleshooting

### jetson_clocks service fails to start

Check logs:
```bash
sudo journalctl -u jetson-clocks.service -e
```

### Clocks not maximized after reboot

Manually run:
```bash
sudo jetson_clocks
```

Check if service is enabled:
```bash
systemctl is-enabled jetson-clocks.service
```

### System overheating

Super Mode pushes hardware to limits. Ensure adequate cooling:
- Check fan is working
- Improve airflow
- Consider heatsink upgrade
- Monitor with `sudo jtop`

## Summary

✅ **nvpmodel MAXN**: Automatic - Set once, persists forever  
✅ **jetson_clocks**: Use systemd service to make permanent  
✅ **Expected benefit**: 2x performance boost  
✅ **After setup**: Super Mode activates automatically on every boot

Run the setup script once, and you'll have permanent 2x performance!

