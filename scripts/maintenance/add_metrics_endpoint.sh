#!/bin/bash
# ADD_METRICS_ENDPOINT.sh
# Adds system metrics endpoint to developer router

set -e

echo "Adding metrics endpoint to developer router..."

cd /home/pi/zoe

# Add metrics function to developer.py
docker exec zoe-core bash -c 'cat >> /app/routers/developer.py << "EOF"

@router.get("/metrics")
async def get_system_metrics():
    """Get system resource metrics"""
    import psutil
    
    # CPU
    cpu_percent = psutil.cpu_percent(interval=1)
    
    # Memory
    mem = psutil.virtual_memory()
    memory_data = {
        "percent": round(mem.percent, 1),
        "used": round(mem.used / (1024**3), 1),
        "total": round(mem.total / (1024**3), 1)
    }
    
    # Disk
    disk = psutil.disk_usage("/")
    disk_data = {
        "percent": round(disk.percent, 1),
        "used": round(disk.used / (1024**3), 1),
        "total": round(disk.total / (1024**3), 1)
    }
    
    return {
        "cpu": cpu_percent,
        "memory": memory_data,
        "disk": disk_data,
        "timestamp": datetime.now().isoformat()
    }
EOF'

# Restart to apply
docker compose restart zoe-core
sleep 5

# Test it
echo "Testing metrics endpoint..."
curl http://localhost:8000/api/developer/metrics | jq '.'

echo "✅ Metrics endpoint added!"
