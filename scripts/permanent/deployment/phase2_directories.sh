#!/bin/bash
# PHASE 2: Complete Directory Structure
echo "📁 PHASE 2: Creating Directory Structure"
echo "========================================"

cd /home/pi/zoe

# Create all directories
mkdir -p services/zoe-core/routers
mkdir -p services/zoe-ui/dist/developer
mkdir -p data/billing
mkdir -p documentation/core
mkdir -p documentation/dynamic
mkdir -p scripts/archive
mkdir -p checkpoints
mkdir -p models
mkdir -p configs
mkdir -p logs

# Create .gitkeep files to preserve empty directories
touch services/zoe-core/routers/.gitkeep
touch services/zoe-ui/dist/developer/.gitkeep
touch data/billing/.gitkeep
touch scripts/temporary/.gitkeep
touch scripts/archive/.gitkeep
touch checkpoints/.gitkeep
touch models/.gitkeep
touch logs/.gitkeep

echo "✅ Phase 2 complete: Directory structure created"
