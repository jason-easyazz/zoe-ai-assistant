#!/bin/bash
# Download Qwen 2.5 7B Instruct GGUF (Optimized for Real-Time Voice)
# This script downloads the Q4_K_M quantization for best speed/quality balance

set -e

MODEL_DIR="/home/zoe/assistant/models/qwen2.5-7b-gguf"
MODEL_FILE="Qwen2.5-7B-Instruct-Q4_K_M.gguf"
MODEL_URL="https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF/resolve/main/$MODEL_FILE"

echo "📦 Downloading Qwen 2.5 7B Instruct (Q4_K_M)"
echo "=========================================="
echo "Model: $MODEL_FILE"
echo "Size: ~4.4GB"
echo "Quantization: Q4_K_M (balanced speed/quality)"
echo ""

# Create directory
mkdir -p "$MODEL_DIR"
cd "$MODEL_DIR"

# Check if already downloaded
if [ -f "$MODEL_FILE" ]; then
    FILE_SIZE=$(du -h "$MODEL_FILE" | cut -f1)
    echo "✅ Model already exists: $MODEL_FILE ($FILE_SIZE)"
    echo ""
    
    # Verify it's complete (should be ~4.4GB)
    ACTUAL_SIZE=$(stat -c%s "$MODEL_FILE")
    if [ "$ACTUAL_SIZE" -gt 4000000000 ]; then
        echo "✅ Model appears complete (${ACTUAL_SIZE} bytes)"
        exit 0
    else
        echo "⚠️  Model appears incomplete (${ACTUAL_SIZE} bytes), re-downloading..."
        rm -f "$MODEL_FILE"
    fi
fi

# Download with wget (supports resume)
echo "⬇️  Downloading from Hugging Face..."
echo "URL: $MODEL_URL"
echo ""

if command -v wget &> /dev/null; then
    wget -c --progress=bar:force:noscroll "$MODEL_URL" -O "$MODEL_FILE"
elif command -v curl &> /dev/null; then
    curl -L -C - --progress-bar "$MODEL_URL" -o "$MODEL_FILE"
else
    echo "❌ ERROR: Neither wget nor curl found"
    exit 1
fi

echo ""
echo "✅ Download complete!"
echo "📊 Model details:"
ls -lh "$MODEL_FILE"
echo ""
echo "Location: $MODEL_DIR/$MODEL_FILE"
echo "Ready for llama.cpp real-time voice inference!"





