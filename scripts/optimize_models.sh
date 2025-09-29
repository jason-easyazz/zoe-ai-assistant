#!/bin/bash

# Zoe Model Optimization Script
# Pre-loads models and optimizes performance

echo "🚀 Optimizing Zoe Model Performance..."

# Function to pre-load a model
preload_model() {
    local model_name=$1
    local display_name=$2
    echo "📌 Pre-loading $display_name..."
    docker exec zoe-ollama ollama run $model_name "Keep me loaded for optimal performance" --keepalive 30m &
    sleep 2
}

# Pre-load primary models (always loaded)
echo "🔄 Pre-loading primary models..."
preload_model "llama3.2:1b" "Ultra-Fast Model (1B)"
preload_model "qwen2.5:3b" "Balanced Model (3B)"

# Wait for models to load
echo "⏳ Waiting for models to load..."
sleep 10

# Test model performance
echo "🧪 Testing model performance..."

echo "Testing ultra-fast model..."
docker exec zoe-ollama ollama run llama3.2:1b "Quick test" --verbose

echo "Testing balanced model..."
docker exec zoe-ollama ollama run qwen2.5:3b "Performance test" --verbose

# Show memory usage
echo "📊 Current Memory Usage:"
free -h

echo "📊 Docker Container Status:"
docker stats zoe-ollama --no-stream

echo "✅ Model optimization complete!"
echo "💡 Primary models are now pre-loaded for optimal performance"
echo "🎯 Expected response times:"
echo "   - Ultra-simple queries: <2 seconds"
echo "   - Simple/Medium queries: 3-6 seconds"
echo "   - Code queries: 5-8 seconds"
echo "   - Complex queries: 8-15 seconds"

