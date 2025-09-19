#!/bin/bash

# Zoe Time Synchronization Setup Script
# This script sets up automatic time synchronization for Zoe

set -e

echo "🕒 Setting up Zoe Time Synchronization System..."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root
if [[ $EUID -eq 0 ]]; then
    print_error "This script should not be run as root"
    exit 1
fi

# Check if required packages are installed
print_status "Checking required packages..."

# Check for ntpdate or chrony
if ! command -v ntpdate &> /dev/null && ! command -v chrony &> /dev/null; then
    print_warning "ntpdate or chrony not found. Installing ntpdate..."
    sudo apt update
    sudo apt install -y ntpdate
fi

# Check for timedatectl
if ! command -v timedatectl &> /dev/null; then
    print_error "timedatectl not found. Please install systemd or run on a compatible system."
    exit 1
fi

# Check for Python 3
if ! command -v python3 &> /dev/null; then
    print_error "Python 3 not found. Please install Python 3."
    exit 1
fi

# Install required Python packages
print_status "Installing Python dependencies..."
pip3 install --user pytz requests

# Create necessary directories
print_status "Creating directories..."
mkdir -p /home/pi/zoe/data
mkdir -p /home/pi/zoe/logs

# Set permissions
chmod 755 /home/pi/zoe/data
chmod 755 /home/pi/zoe/logs

# Make the time sync service executable
chmod +x /home/pi/zoe/scripts/time_sync_service.py

# Test the time sync service
print_status "Testing time sync service..."
python3 /home/pi/zoe/scripts/time_sync_service.py sync

if [ $? -eq 0 ]; then
    print_success "Time sync service test passed"
else
    print_warning "Time sync service test failed, but continuing setup"
fi

# Setup sudo permissions for time sync
print_status "Setting up sudo permissions for time sync..."

# Create sudoers file for time sync
sudo tee /etc/sudoers.d/zoe-time-sync > /dev/null << EOF
# Zoe Time Sync Service
pi ALL=(ALL) NOPASSWD: /usr/bin/timedatectl set-ntp *, /usr/bin/timedatectl set-timezone *, /usr/bin/ntpdate *
EOF

# Set proper permissions
sudo chmod 440 /etc/sudoers.d/zoe-time-sync

# Install systemd service
print_status "Installing systemd service..."

# Copy service file to systemd directory
sudo cp /home/pi/zoe/scripts/zoe-time-sync.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable the service
sudo systemctl enable zoe-time-sync.service

print_success "Time sync service installed and enabled"

# Test the service
print_status "Testing systemd service..."
sudo systemctl start zoe-time-sync.service
sleep 5

if sudo systemctl is-active --quiet zoe-time-sync.service; then
    print_success "Time sync service is running"
else
    print_warning "Time sync service failed to start"
    sudo systemctl status zoe-time-sync.service
fi

# Configure initial timezone (optional)
print_status "Configuring timezone..."

# Get current timezone
CURRENT_TZ=$(timedatectl show --property=Timezone --value)

if [ "$CURRENT_TZ" = "UTC" ]; then
    print_warning "System timezone is set to UTC. You may want to change this in Zoe settings."
fi

# Create initial settings file
print_status "Creating initial settings file..."
cat > /home/pi/zoe/data/time_settings.json << EOF
{
  "timezone": "$CURRENT_TZ",
  "ntp_servers": [
    "pool.ntp.org",
    "time.nist.gov", 
    "time.google.com"
  ],
  "sync_interval": 3600,
  "auto_sync": true,
  "last_sync": null,
  "sync_attempts": 0,
  "location": null
}
EOF

# Set proper permissions
chmod 644 /home/pi/zoe/data/time_settings.json

print_success "Initial settings file created"

# Test time synchronization
print_status "Testing time synchronization..."
python3 /home/pi/zoe/scripts/time_sync_service.py sync

# Show status
print_status "Time sync service status:"
sudo systemctl status zoe-time-sync.service --no-pager -l

# Show current time info
print_status "Current time information:"
python3 /home/pi/zoe/scripts/time_sync_service.py status

print_success "Zoe Time Synchronization System setup complete!"
print_status ""
print_status "The time sync service is now running and will:"
print_status "  - Automatically sync time with NTP servers every hour"
print_status "  - Maintain accurate system time"
print_status "  - Support timezone changes through Zoe settings"
print_status ""
print_status "You can manage the service with:"
print_status "  sudo systemctl start zoe-time-sync.service"
print_status "  sudo systemctl stop zoe-time-sync.service"
print_status "  sudo systemctl status zoe-time-sync.service"
print_status "  sudo systemctl restart zoe-time-sync.service"
print_status ""
print_status "Manual sync:"
print_status "  python3 /home/pi/zoe/scripts/time_sync_service.py sync"
print_status ""
print_status "View status:"
print_status "  python3 /home/pi/zoe/scripts/time_sync_service.py status"


