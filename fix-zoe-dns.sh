#!/bin/bash

# Force zoe.local to resolve to the correct IP
# This script ensures mDNS broadcasts the correct IP address

# Get the actual network IP
NETWORK_IP=$(ip route get 8.8.8.8 | awk '{print $7; exit}')

echo "Setting zoe.local to resolve to: $NETWORK_IP"

# Update avahi hosts file
echo "$NETWORK_IP zoe.local" > /etc/avahi/hosts

# Restart avahi
systemctl restart avahi-daemon

# Wait a moment for avahi to start
sleep 2

# Test resolution
echo "Testing resolution:"
avahi-resolve -n zoe.local

echo "Zoe DNS fix complete!"
