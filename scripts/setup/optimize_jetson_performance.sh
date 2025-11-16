#!/bin/bash
# Optimize Jetson Orin NX for Maximum LLM Performance
# Run with sudo: sudo bash scripts/setup/optimize_jetson_performance.sh

set -e

echo "🚀 Optimizing Jetson Orin NX for Real-Time Voice LLM"
echo "======================================================"

# Set to MAXN mode (maximum performance)
echo "1. Setting power mode to MAXN..."
nvpmodel -m 0
echo "✅ Power mode set"

# Maximize all clocks
echo "2. Maximizing clock speeds..."
jetson_clocks
echo "✅ Clocks maximized"

# Check GPU frequency
echo "3. Verifying GPU frequency..."
if [ -f /sys/devices/gpu.0/devfreq/17000000.gpu/cur_freq ]; then
    FREQ=$(cat /sys/devices/gpu.0/devfreq/17000000.gpu/cur_freq)
    echo "✅ GPU frequency: $FREQ Hz"
else
    echo "⚠️  GPU frequency file not found (may be normal)"
fi

# Display current mode
echo ""
echo "4. Current configuration:"
nvpmodel -q 2>&1 || echo "nvpmodel query not available"

echo ""
echo "5. GPU Status:"
nvidia-smi --query-gpu=name,power.draw,power.limit,clocks.gr,clocks.mem,utilization.gpu,memory.used,memory.total --format=csv,noheader 2>&1 || echo "nvidia-smi not available"

echo ""
echo "✅ Jetson optimization complete!"
echo ""
echo "Performance settings:"
echo "  - Power Mode: MAXN (maximum performance)"
echo "  - Clocks: Maximized"
echo "  - Ready for: Real-time voice LLM inference"
echo ""
echo "Note: These settings reset on reboot."
echo "      To make permanent, add to startup script."





