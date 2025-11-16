#!/bin/bash
# Export Database Schemas
# Extracts current schemas from databases and updates schema files
# Usage: ./scripts/maintenance/export_schema.sh

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PROJECT_ROOT="/home/zoe/assistant"
DATA_DIR="$PROJECT_ROOT/data"
SCHEMA_DIR="$DATA_DIR/schema"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Exporting Database Schemas${NC}"
echo -e "${BLUE}========================================${NC}\n"

# Create schema directory if it doesn't exist
mkdir -p "$SCHEMA_DIR"

# Function to export schema
export_schema() {
    local db_name=$1
    local schema_file=$2
    
    if [ ! -f "$DATA_DIR/$db_name" ]; then
        echo -e "${YELLOW}⚠${NC}  Database not found: $db_name (skipping)"
        return 1
    fi
    
    echo -e "Exporting ${db_name}..."
    sqlite3 "$DATA_DIR/$db_name" ".schema" > "$schema_file"
    
    if [ $? -eq 0 ]; then
        lines=$(wc -l < "$schema_file")
        echo -e "${GREEN}✓${NC} Exported to ${schema_file##*/} ($lines lines)"
        return 0
    else
        echo -e "${RED}✗${NC} Failed to export $db_name"
        return 1
    fi
}

# Export all database schemas
export_schema "zoe.db" "$SCHEMA_DIR/zoe_schema.sql"
export_schema "memory.db" "$SCHEMA_DIR/memory_schema.sql"
export_schema "training.db" "$SCHEMA_DIR/training_schema.sql"

echo ""
echo -e "${GREEN}✓ Schema export complete!${NC}"
echo ""
echo "Schema files updated in: $SCHEMA_DIR"
echo "Commit these files to git to track schema changes."



