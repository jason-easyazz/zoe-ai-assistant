#!/bin/bash
"""
Zoe Touch Panel Discovery System Installer
==========================================

Installs the auto-discovery system for touch panels to find Zoe instances.
Designed for mass adoption - works without technical configuration.
"""

set -e

echo "🚀 Installing Zoe Touch Panel Discovery System..."

# Create directories
DISCOVERY_DIR="/home/pi/zoe/services/touch-panel-discovery"
SCRIPTS_DIR="/home/pi/zoe/scripts/touch-panel"
SERVICE_DIR="/etc/systemd/system"

echo "📁 Creating directories..."
mkdir -p "$SCRIPTS_DIR"
sudo mkdir -p /etc/avahi/services

# Install Python dependencies
echo "📦 Installing Python dependencies..."
pip3 install --user requests netifaces zeroconf

# Copy discovery scripts
echo "📋 Setting up discovery scripts..."
cp "$DISCOVERY_DIR/simple_discovery_client.py" "$SCRIPTS_DIR/"
cp "$DISCOVERY_DIR/auto_discovery.py" "$SCRIPTS_DIR/"
cp "$DISCOVERY_DIR/enhanced_avahi_service.py" "$SCRIPTS_DIR/"

# Make scripts executable
chmod +x "$SCRIPTS_DIR"/*.py

# Create convenience symlinks
echo "🔗 Creating convenience commands..."
sudo ln -sf "$SCRIPTS_DIR/simple_discovery_client.py" /usr/local/bin/find-zoe
sudo ln -sf "$SCRIPTS_DIR/enhanced_avahi_service.py" /usr/local/bin/setup-zoe-discovery

# Setup enhanced Avahi announcements
echo "📡 Setting up enhanced mDNS announcements..."
python3 "$SCRIPTS_DIR/enhanced_avahi_service.py"

# Create systemd service for continuous discovery announcements
echo "⚙️  Creating discovery service..."
sudo tee "$SERVICE_DIR/zoe-discovery.service" > /dev/null << 'EOF'
[Unit]
Description=Zoe Auto-Discovery Service
After=network.target avahi-daemon.service
Wants=avahi-daemon.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/setup-zoe-discovery
RemainAfterExit=yes
User=pi
Group=pi

[Install]
WantedBy=multi-user.target
EOF

# Enable and start the service
echo "🔄 Enabling discovery service..."
sudo systemctl daemon-reload
sudo systemctl enable zoe-discovery.service
sudo systemctl start zoe-discovery.service

# Test the discovery system
echo "🧪 Testing discovery system..."
if python3 "$SCRIPTS_DIR/simple_discovery_client.py"; then
    echo "✅ Discovery test passed!"
else
    echo "⚠️  Discovery test failed - this may be normal if Zoe is not running"
fi

# Create a simple GUI discovery tool for touch panels
echo "🖥️  Creating touch panel discovery tool..."
cat > "$SCRIPTS_DIR/touch_panel_setup.py" << 'EOF'
#!/usr/bin/env python3
"""
Simple GUI for touch panel Zoe discovery
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
from simple_discovery_client import find_zoe, test_discovery

class TouchPanelSetup:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Zoe Touch Panel Setup")
        self.root.geometry("600x400")
        
        # Create UI
        self.create_widgets()
        
    def create_widgets(self):
        # Main frame
        main_frame = ttk.Frame(self.root, padding="20")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Title
        title = ttk.Label(main_frame, text="Zoe Touch Panel Setup", 
                         font=('Arial', 16, 'bold'))
        title.grid(row=0, column=0, columnspan=2, pady=(0, 20))
        
        # Status
        self.status_var = tk.StringVar(value="Ready to discover Zoe...")
        status_label = ttk.Label(main_frame, textvariable=self.status_var)
        status_label.grid(row=1, column=0, columnspan=2, pady=(0, 10))
        
        # Discover button
        discover_btn = ttk.Button(main_frame, text="Find Zoe", 
                                command=self.discover_zoe)
        discover_btn.grid(row=2, column=0, columnspan=2, pady=(0, 20))
        
        # Results area
        self.results_text = tk.Text(main_frame, height=15, width=70)
        self.results_text.grid(row=3, column=0, columnspan=2, pady=(0, 10))
        
        # Scrollbar for results
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", 
                                command=self.results_text.yview)
        scrollbar.grid(row=3, column=2, sticky=(tk.N, tk.S))
        self.results_text.configure(yscrollcommand=scrollbar.set)
        
    def discover_zoe(self):
        self.status_var.set("Discovering Zoe...")
        self.results_text.delete(1.0, tk.END)
        
        # Run discovery in thread to avoid blocking UI
        thread = threading.Thread(target=self._run_discovery)
        thread.daemon = True
        thread.start()
        
    def _run_discovery(self):
        try:
            config = find_zoe(use_cache=False)
            
            if config:
                self.root.after(0, self._show_success, config)
            else:
                self.root.after(0, self._show_failure)
                
        except Exception as e:
            self.root.after(0, self._show_error, str(e))
    
    def _show_success(self, config):
        self.status_var.set("✅ Zoe found successfully!")
        
        info = config.get('discovery_info', {})
        result = f"Zoe discovered at: {info.get('url')}\n"
        result += f"Discovery method: {info.get('method')}\n\n"
        
        result += "Available services:\n"
        services = config.get('services', {})
        for name, service in services.items():
            result += f"• {service.get('name', name)}: {service.get('url')}\n"
        
        result += "\n✅ Touch panel is ready to use!"
        result += "\nConfiguration saved for future use."
        
        self.results_text.insert(tk.END, result)
        
    def _show_failure(self):
        self.status_var.set("❌ Zoe not found")
        
        result = "Could not find Zoe instance.\n\n"
        result += "Troubleshooting:\n"
        result += "1. Ensure Zoe is running on the main device\n"
        result += "2. Check that both devices are on the same WiFi network\n"
        result += "3. Try accessing http://zoe.local in a browser\n"
        result += "4. Contact your Zoe administrator\n"
        
        self.results_text.insert(tk.END, result)
        
    def _show_error(self, error):
        self.status_var.set("❌ Discovery error")
        self.results_text.insert(tk.END, f"Error during discovery: {error}")
        
    def run(self):
        self.root.mainloop()

if __name__ == '__main__':
    app = TouchPanelSetup()
    app.run()
EOF

chmod +x "$SCRIPTS_DIR/touch_panel_setup.py"

echo ""
echo "✅ Zoe Touch Panel Discovery System installed successfully!"
echo ""
echo "📋 Available commands:"
echo "   find-zoe                  - Discover Zoe from command line"
echo "   setup-zoe-discovery       - Setup enhanced mDNS announcements"
echo "   python3 $SCRIPTS_DIR/touch_panel_setup.py - GUI setup tool"
echo ""
echo "📡 The system will automatically:"
echo "   • Announce Zoe services via mDNS/Bonjour"
echo "   • Make Zoe discoverable to touch panels"
echo "   • Provide fallback discovery methods"
echo ""
echo "🎯 For touch panel apps, use:"
echo "   from simple_discovery_client import find_zoe"
echo "   config = find_zoe()"
echo ""
echo "Mass adoption ready! Touch panels will find Zoe automatically."




