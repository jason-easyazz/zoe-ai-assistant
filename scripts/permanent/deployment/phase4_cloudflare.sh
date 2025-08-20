#!/bin/bash
# PHASE 4: Cloudflare Tunnel Setup
echo "🌐 PHASE 4: Cloudflare Tunnel Setup"
echo "===================================="

# Check if cloudflared is installed
if command -v cloudflared &> /dev/null; then
    echo "✅ Cloudflare tunnel already installed"
    sudo systemctl status cloudflared --no-pager || true
else
    echo "📝 Cloudflare not installed"
    echo "To install later, run:"
    echo "  curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb -o cloudflared.deb"
    echo "  sudo dpkg -i cloudflared.deb"
fi

echo "✅ Phase 4 complete: Cloudflare check done"
