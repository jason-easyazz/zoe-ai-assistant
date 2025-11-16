#!/bin/bash
# Monitor Qwen 2.5 7B download progress
# Usage: bash scripts/setup/monitor_qwen_download.sh

MODEL_FILE="/home/zoe/assistant/models/qwen2.5-7b-gguf/Qwen2.5-7B-Instruct-Q4_K_M.gguf"
TARGET_SIZE=4400  # MB

echo "📊 Monitoring Qwen 2.5 7B Download"
echo "==================================="
echo ""

while true; do
    clear
    echo "📊 Qwen 2.5 7B Download Monitor"
    echo "================================="
    echo ""
    
    if [ -f "$MODEL_FILE" ]; then
        # Get file size in MB
        SIZE_BYTES=$(stat -c%s "$MODEL_FILE" 2>/dev/null || stat -f%z "$MODEL_FILE" 2>/dev/null)
        SIZE_MB=$((SIZE_BYTES / 1024 / 1024))
        SIZE_HUMAN=$(du -h "$MODEL_FILE" | cut -f1)
        
        # Calculate percentage
        PERCENT=$((SIZE_MB * 100 / TARGET_SIZE))
        
        # Progress bar
        BAR_LENGTH=50
        FILLED=$((PERCENT * BAR_LENGTH / 100))
        BAR=$(printf "%${FILLED}s" | tr ' ' '█')
        EMPTY=$(printf "%$((BAR_LENGTH - FILLED))s" | tr ' ' '░')
        
        echo "File: Qwen2.5-7B-Instruct-Q4_K_M.gguf"
        echo "Size: $SIZE_HUMAN / 4.4GB ($SIZE_MB MB / $TARGET_SIZE MB)"
        echo "Progress: $PERCENT%"
        echo ""
        echo "[$BAR$EMPTY] $PERCENT%"
        echo ""
        
        # Check if download is complete
        if [ $SIZE_MB -ge $TARGET_SIZE ]; then
            echo "✅ DOWNLOAD COMPLETE!"
            echo ""
            echo "Next steps:"
            echo "1. sudo bash scripts/setup/optimize_jetson_performance.sh"
            echo "2. docker-compose restart zoe-llamacpp"
            break
        fi
        
        # Check if download is still running
        if ps aux | grep -q "[w]get.*Qwen"; then
            echo "Status: 📥 Downloading..."
        else
            echo "⚠️  Warning: wget process not found"
            echo "   Download may have stopped or completed"
        fi
    else
        echo "❌ Model file not found!"
        echo "   Expected: $MODEL_FILE"
        echo ""
        echo "Start download with:"
        echo "bash scripts/setup/download_qwen_optimized.sh"
        break
    fi
    
    echo ""
    echo "Refreshing in 5 seconds... (Ctrl+C to exit)"
    sleep 5
done





