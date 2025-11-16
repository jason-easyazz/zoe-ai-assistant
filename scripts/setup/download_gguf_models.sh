#!/bin/bash
set -e

echo "📦 Downloading Pre-Quantized GGUF Models"
echo "========================================="
echo ""

MODELS_DIR="/home/zoe/assistant/models"

# Install huggingface-cli if not present
if ! command -v huggingface-cli &> /dev/null; then
    echo "Installing huggingface-hub..."
    pip3 install -U huggingface-hub
fi

# Create models directory structure
mkdir -p "$MODELS_DIR/llama-3.2-3b-gguf"
mkdir -p "$MODELS_DIR/qwen2.5-coder-7b-gguf"

echo "1️⃣ Downloading Llama 3.2 3B Instruct (Q4_K_M - ~2GB)..."
echo "   Source: bartowski/Llama-3.2-3B-Instruct-GGUF"
huggingface-cli download \
  bartowski/Llama-3.2-3B-Instruct-GGUF \
  Llama-3.2-3B-Instruct-Q4_K_M.gguf \
  --local-dir "$MODELS_DIR/llama-3.2-3b-gguf" \
  --local-dir-use-symlinks False

echo ""
echo "2️⃣ Downloading Qwen 2.5 Coder 7B Instruct (Q4_K_M - ~4.4GB)..."
echo "   Source: bartowski/Qwen2.5-Coder-7B-Instruct-GGUF"
huggingface-cli download \
  bartowski/Qwen2.5-Coder-7B-Instruct-GGUF \
  Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf \
  --local-dir "$MODELS_DIR/qwen2.5-coder-7b-gguf" \
  --local-dir-use-symlinks False

echo ""
echo "✅ Downloads complete!"
echo ""
echo "📊 Model Details:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Llama 3.2 3B:"
echo "  Location: $MODELS_DIR/llama-3.2-3b-gguf/Llama-3.2-3B-Instruct-Q4_K_M.gguf"
echo "  Size:     $(du -h "$MODELS_DIR/llama-3.2-3b-gguf/Llama-3.2-3B-Instruct-Q4_K_M.gguf" 2>/dev/null | cut -f1 || echo 'checking...')"
echo ""
echo "Qwen 2.5 Coder 7B:"
echo "  Location: $MODELS_DIR/qwen2.5-coder-7b-gguf/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"
echo "  Size:     $(du -h "$MODELS_DIR/qwen2.5-coder-7b-gguf/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf" 2>/dev/null | cut -f1 || echo 'checking...')"
echo ""
echo "💾 Total space used: $(du -sh "$MODELS_DIR"/*gguf 2>/dev/null | awk '{sum+=$1}END{print sum}' || echo 'calculating...')GB"
echo ""
echo "🚀 Ready for llama.cpp!"






