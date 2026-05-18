#!/bin/bash
# Database Initialization Script for Zoe AI Assistant
# Creates all required databases from schema files
# Usage: ./scripts/setup/init_databases.sh [--with-seed-data]

set -e  # Exit on error
set -u  # Exit on undefined variable

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Project root
PROJECT_ROOT="/home/zoe/assistant"
DATA_DIR="$PROJECT_ROOT/data"
SCHEMA_DIR="$DATA_DIR/schema"

# Parse arguments
SEED_DATA=false
if [[ "${1:-}" == "--with-seed-data" ]]; then
    SEED_DATA=true
fi

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Zoe Database Initialization${NC}"
echo -e "${BLUE}========================================${NC}\n"

# Check if sqlite3 is installed
if ! command -v sqlite3 &> /dev/null; then
    echo -e "${RED}✗ Error: sqlite3 is not installed${NC}"
    echo "  Install it with: sudo apt-get install sqlite3"
    exit 1
fi

# Check if schema directory exists
if [ ! -d "$SCHEMA_DIR" ]; then
    echo -e "${RED}✗ Error: Schema directory not found: $SCHEMA_DIR${NC}"
    exit 1
fi

# Function to initialize a database
init_database() {
    local db_name=$1
    local schema_file=$2
    local db_path="$DATA_DIR/$db_name"
    
    echo -e "${YELLOW}Initializing $db_name...${NC}"
    
    # Check if schema file exists
    if [ ! -f "$schema_file" ]; then
        echo -e "${RED}✗ Schema file not found: $schema_file${NC}"
        return 1
    fi
    
    # Backup existing database if it exists
    if [ -f "$db_path" ]; then
        backup_path="${db_path}.backup.$(date +%Y%m%d_%H%M%S)"
        echo -e "  ${YELLOW}⚠${NC}  Existing database found, backing up to: ${backup_path##*/}"
        cp "$db_path" "$backup_path"
    fi
    
    # Create new database
    echo -e "  Creating database: $db_name"
    sqlite3 "$db_path" < "$schema_file"
    
    if [ $? -eq 0 ]; then
        echo -e "  ${GREEN}✓${NC} $db_name created successfully"
        
        # Set proper permissions
        chmod 644 "$db_path"
        
        # Show table count
        table_count=$(sqlite3 "$db_path" "SELECT COUNT(*) FROM sqlite_master WHERE type='table';")
        echo -e "  Tables created: $table_count"
        return 0
    else
        echo -e "  ${RED}✗${NC} Failed to create $db_name"
        return 1
    fi
}

# Create data directory if it doesn't exist
mkdir -p "$DATA_DIR"

# Initialize databases
echo ""
init_database "zoe.db" "$SCHEMA_DIR/zoe_schema.sql"
echo ""
init_database "memory.db" "$SCHEMA_DIR/memory_schema.sql"
echo ""
init_database "training.db" "$SCHEMA_DIR/training_schema.sql"

# Apply seed data if requested
if [ "$SEED_DATA" = true ]; then
    echo ""
    echo -e "${YELLOW}Applying seed data...${NC}"
    
    if [ -f "$SCHEMA_DIR/seed_data.sql" ]; then
        sqlite3 "$DATA_DIR/zoe.db" < "$SCHEMA_DIR/seed_data.sql"
        
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✓${NC} Seed data applied successfully"
            echo -e "  Demo user created: demo / demo123"
        else
            echo -e "${RED}✗${NC} Failed to apply seed data"
        fi
    else
        echo -e "${YELLOW}⚠${NC}  Seed data file not found, skipping"
    fi
fi

# Summary
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}✓ Database initialization complete!${NC}"
echo -e "${BLUE}========================================${NC}\n"

echo "Databases created:"
echo "  • zoe.db      - Main application database"
echo "  • memory.db   - LightRAG memory database"
echo "  • training.db - Training data database"
echo ""

if [ "$SEED_DATA" = true ]; then
    echo "Demo user credentials:"
    echo "  Username: demo"
    echo "  Password: demo123"
    echo ""
fi

echo "Next steps:"
echo "  1. Start Zoe services: docker-compose up -d"
echo "  2. Access UI: http://localhost:3080"
if [ "$SEED_DATA" = false ]; then
    echo "  3. Create your user via onboarding"
fi
echo ""
echo -e "${GREEN}Ready to use Zoe!${NC}"

# ── OIDC secret generation ─────────────────────────────────────────────────
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}OIDC Secret Generation${NC}"
echo -e "${BLUE}========================================${NC}"

ROOT_ENV="$PROJECT_ROOT/.env"
HA_SECRETS="$PROJECT_ROOT/homeassistant/secrets.yaml"

if [ ! -f "$ROOT_ENV" ]; then
    echo -e "${YELLOW}⚠${NC}  .env not found — skipping OIDC secret generation"
else
    # Generate HA_OIDC_CLIENT_SECRET if blank or missing
    current_ha_secret=$(grep "^HA_OIDC_CLIENT_SECRET=" "$ROOT_ENV" | cut -d= -f2-)
    if [ -z "$current_ha_secret" ]; then
        ha_secret=$(openssl rand -hex 32)
        # Update or append
        if grep -q "^HA_OIDC_CLIENT_SECRET=" "$ROOT_ENV"; then
            sed -i "s|^HA_OIDC_CLIENT_SECRET=.*|HA_OIDC_CLIENT_SECRET=${ha_secret}|" "$ROOT_ENV"
        else
            echo "HA_OIDC_CLIENT_SECRET=${ha_secret}" >> "$ROOT_ENV"
        fi
        echo -e "  ${GREEN}✓${NC} HA_OIDC_CLIENT_SECRET generated"
    else
        ha_secret="$current_ha_secret"
        echo -e "  ${YELLOW}⚠${NC}  HA_OIDC_CLIENT_SECRET already set — keeping existing"
    fi

    # Generate MULTICA_OIDC_CLIENT_SECRET if blank or missing
    current_multica_secret=$(grep "^MULTICA_OIDC_CLIENT_SECRET=" "$ROOT_ENV" | cut -d= -f2-)
    if [ -z "$current_multica_secret" ]; then
        multica_secret=$(openssl rand -hex 32)
        if grep -q "^MULTICA_OIDC_CLIENT_SECRET=" "$ROOT_ENV"; then
            sed -i "s|^MULTICA_OIDC_CLIENT_SECRET=.*|MULTICA_OIDC_CLIENT_SECRET=${multica_secret}|" "$ROOT_ENV"
        else
            echo "MULTICA_OIDC_CLIENT_SECRET=${multica_secret}" >> "$ROOT_ENV"
        fi
        echo -e "  ${GREEN}✓${NC} MULTICA_OIDC_CLIENT_SECRET generated"
    else
        echo -e "  ${YELLOW}⚠${NC}  MULTICA_OIDC_CLIENT_SECRET already set — keeping existing"
    fi

    # Write HA secrets.yaml if client secret is not yet set
    if [ -f "$HA_SECRETS" ]; then
        current_ha_yaml_secret=$(grep "^ha_oidc_client_secret:" "$HA_SECRETS" | sed 's/ha_oidc_client_secret: *//;s/"//g' | xargs)
        if [ -z "$current_ha_yaml_secret" ]; then
            sed -i "s|^ha_oidc_client_id:.*|ha_oidc_client_id: \"home-assistant\"|" "$HA_SECRETS"
            sed -i "s|^ha_oidc_client_secret:.*|ha_oidc_client_secret: \"${ha_secret}\"|" "$HA_SECRETS"
            echo -e "  ${GREEN}✓${NC} homeassistant/secrets.yaml updated with HA OIDC secret"
        else
            echo -e "  ${YELLOW}⚠${NC}  homeassistant/secrets.yaml already has ha_oidc_client_secret — keeping existing"
        fi
    fi

    # Apply OIDC DDL to PostgreSQL
    echo -e "  Applying OIDC DDL to PostgreSQL..."
    if docker exec -i zoe-database psql -U zoe -d zoe \
        < "$PROJECT_ROOT/scripts/setup/migrate_auth_to_postgres.sql" > /dev/null 2>&1; then
        echo -e "  ${GREEN}✓${NC} OIDC tables verified in PostgreSQL"
    else
        echo -e "  ${YELLOW}⚠${NC}  Could not apply DDL (is zoe-database running?)"
    fi
fi



