#!/bin/bash

# Zoe SSL Certificate Verification Script
# Quick check for certificate status and validity

set -e

# Configuration
SSL_DIR="/home/pi/zoe/ssl"
CERT_FILE="$SSL_DIR/zoe.crt"
KEY_FILE="$SSL_DIR/zoe.key"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🔍 Zoe SSL Certificate Verification${NC}"
echo "====================================="

# Check if certificate files exist
if [ ! -f "$CERT_FILE" ]; then
    echo -e "${RED}❌ Certificate file not found: $CERT_FILE${NC}"
    exit 1
fi

if [ ! -f "$KEY_FILE" ]; then
    echo -e "${RED}❌ Private key file not found: $KEY_FILE${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Certificate files found${NC}"

# Check certificate validity
echo ""
echo -e "${YELLOW}📋 Certificate Information:${NC}"
echo "Subject: $(openssl x509 -in "$CERT_FILE" -subject -noout | cut -d'=' -f2-)"
echo "Issuer: $(openssl x509 -in "$CERT_FILE" -issuer -noout | cut -d'=' -f2-)"
echo "Valid From: $(openssl x509 -in "$CERT_FILE" -startdate -noout | cut -d'=' -f2)"
echo "Valid Until: $(openssl x509 -in "$CERT_FILE" -enddate -noout | cut -d'=' -f2)"

# Check if certificate is currently valid
CURRENT_DATE=$(date +%s)
NOT_AFTER=$(openssl x509 -in "$CERT_FILE" -enddate -noout | cut -d'=' -f2 | xargs -I {} date -d "{}" +%s)

if [ "$CURRENT_DATE" -lt "$NOT_AFTER" ]; then
    DAYS_LEFT=$(( (NOT_AFTER - CURRENT_DATE) / 86400 ))
    echo -e "${GREEN}✅ Certificate is valid (${DAYS_LEFT} days remaining)${NC}"
else
    echo -e "${RED}❌ Certificate has expired${NC}"
    exit 1
fi

# Check certificate and key match
echo ""
echo -e "${YELLOW}🔐 Checking certificate and key match...${NC}"
CERT_MD5=$(openssl x509 -noout -modulus -in "$CERT_FILE" | openssl md5)
KEY_MD5=$(openssl rsa -noout -modulus -in "$KEY_FILE" | openssl md5)

if [ "$CERT_MD5" = "$KEY_MD5" ]; then
    echo -e "${GREEN}✅ Certificate and private key match${NC}"
else
    echo -e "${RED}❌ Certificate and private key do not match${NC}"
    exit 1
fi

# Check nginx container status
echo ""
echo -e "${YELLOW}🐳 Checking nginx container status...${NC}"
if docker ps | grep -q "zoe-ui"; then
    echo -e "${GREEN}✅ Nginx container is running${NC}"
else
    echo -e "${YELLOW}⚠️  Nginx container is not running${NC}"
fi

# Test HTTPS connection (if possible)
echo ""
echo -e "${YELLOW}🌐 Testing HTTPS connection...${NC}"
if command -v curl &> /dev/null; then
    if curl -k -s -o /dev/null -w "%{http_code}" https://zoe.local | grep -q "200\|301\|302"; then
        echo -e "${GREEN}✅ HTTPS connection successful${NC}"
    else
        echo -e "${YELLOW}⚠️  HTTPS connection test failed (this may be normal if zoe.local is not accessible)${NC}"
    fi
else
    echo -e "${YELLOW}⚠️  curl not available for connection test${NC}"
fi

echo ""
echo -e "${GREEN}🎉 Certificate verification complete!${NC}"


