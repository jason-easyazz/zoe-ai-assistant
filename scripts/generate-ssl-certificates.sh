#!/bin/bash

# Zoe SSL Certificate Generation Script
# For mass production deployment

set -e

# Configuration
SSL_DIR="/home/pi/zoe/ssl"
CERT_CONFIG="$SSL_DIR/zoe-openssl.cnf"
CERT_FILE="$SSL_DIR/zoe.crt"
KEY_FILE="$SSL_DIR/zoe.key"
VALIDITY_DAYS=365

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}🔐 Zoe SSL Certificate Generation${NC}"
echo "=================================="

# Check if OpenSSL is installed
if ! command -v openssl &> /dev/null; then
    echo -e "${RED}❌ OpenSSL is not installed. Please install it first.${NC}"
    exit 1
fi

# Create SSL directory if it doesn't exist
if [ ! -d "$SSL_DIR" ]; then
    echo -e "${YELLOW}📁 Creating SSL directory: $SSL_DIR${NC}"
    mkdir -p "$SSL_DIR"
fi

# Check if config file exists
if [ ! -f "$CERT_CONFIG" ]; then
    echo -e "${RED}❌ OpenSSL config file not found: $CERT_CONFIG${NC}"
    echo "Please ensure the zoe-openssl.cnf file exists."
    exit 1
fi

# Generate certificate
echo -e "${YELLOW}🔧 Generating SSL certificate...${NC}"
echo "Config file: $CERT_CONFIG"
echo "Certificate: $CERT_FILE"
echo "Private key: $KEY_FILE"
echo "Validity: $VALIDITY_DAYS days"

openssl req -x509 -newkey rsa:4096 -keyout "$KEY_FILE" -out "$CERT_FILE" -days "$VALIDITY_DAYS" -nodes -config "$CERT_CONFIG"

# Set proper permissions
chmod 644 "$CERT_FILE"
chmod 600 "$KEY_FILE"

echo -e "${GREEN}✅ SSL certificates generated successfully!${NC}"

# Display certificate information
echo ""
echo -e "${YELLOW}📋 Certificate Information:${NC}"
openssl x509 -in "$CERT_FILE" -text -noout | grep -E "(Subject:|Issuer:|Not Before|Not After|DNS:)"

echo ""
echo -e "${YELLOW}🔍 Certificate Details:${NC}"
echo "Subject: $(openssl x509 -in "$CERT_FILE" -subject -noout | cut -d'=' -f2-)"
echo "Issuer: $(openssl x509 -in "$CERT_FILE" -issuer -noout | cut -d'=' -f2-)"
echo "Valid From: $(openssl x509 -in "$CERT_FILE" -startdate -noout | cut -d'=' -f2)"
echo "Valid Until: $(openssl x509 -in "$CERT_FILE" -enddate -noout | cut -d'=' -f2)"

# Check if nginx container is running
if docker ps | grep -q "zoe-ui"; then
    echo ""
    echo -e "${YELLOW}🔄 Restarting nginx container to load new certificates...${NC}"
    cd /home/pi/zoe
    docker-compose restart zoe-ui
    echo -e "${GREEN}✅ Nginx container restarted${NC}"
else
    echo ""
    echo -e "${YELLOW}⚠️  Nginx container not running. Start it with: docker-compose up -d zoe-ui${NC}"
fi

echo ""
echo -e "${GREEN}🎉 SSL certificate setup complete!${NC}"
echo "Your Zoe instance should now work with HTTPS without browser warnings."
echo ""
echo -e "${YELLOW}📝 Next steps for mass production:${NC}"
echo "1. Copy this script to each Zoe device"
echo "2. Run: chmod +x generate-ssl-certificates.sh"
echo "3. Run: ./generate-ssl-certificates.sh"
echo "4. Verify HTTPS access at https://zoe.local"

