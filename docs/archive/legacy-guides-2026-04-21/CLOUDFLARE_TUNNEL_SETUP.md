# Cloudflare Tunnel Setup Guide

## Overview

The Zoe AI Assistant includes optional Cloudflare Tunnel support for secure remote access. The `cloudflared` service is **disabled by default** and requires manual setup.

## Prerequisites

1. A Cloudflare account with Tunnel access
2. A domain configured in Cloudflare
3. Cloudflare tunnel credentials

## Setup Instructions

### 1. Install Cloudflare Tunnel CLI

```bash
# Download and install cloudflared
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
sudo mv cloudflared-linux-amd64 /usr/local/bin/cloudflared
sudo chmod +x /usr/local/bin/cloudflared
```

### 2. Authenticate with Cloudflare

```bash
cloudflared tunnel login
```

This will open a browser and download a `cert.pem` file to `~/.cloudflared/cert.pem`.

### 3. Create a Tunnel

```bash
cloudflared tunnel create zoe-assistant
```

This creates a tunnel and generates a credentials JSON file.

### 4. Copy Required Files

Copy the generated files to the Zoe config directory:

```bash
# Copy the cert.pem
cp ~/.cloudflared/cert.pem /home/zoe/assistant/config/cert.pem

# The credentials file should already be at:
# /home/zoe/assistant/config/d417c04a-babc-48e4-85e6-a9badc20a7a6.json

# Verify the config file exists:
ls -la /home/zoe/assistant/config/cloudflared-config.yml
```

### 5. Configure the Tunnel

Edit `/home/zoe/assistant/config/cloudflared-config.yml`:

```yaml
tunnel: d417c04a-babc-48e4-85e6-a9badc20a7a6
credentials-file: /etc/cloudflared/d417c04a-babc-48e4-85e6-a9badc20a7a6.json

ingress:
  - hostname: zoe.yourdomain.com
    service: https://zoe-core:8000
    originRequest:
      noTLSVerify: true
  - service: http_status:404
```

### 6. Start Cloudflared Service

Enable the cloudflare profile when starting Docker Compose:

```bash
cd /home/zoe/assistant
docker compose --profile cloudflare up -d
```

## Verification

Check that the tunnel is running:

```bash
docker logs zoe-cloudflared
```

You should see:
```
INF Connection registered connIndex=0
INF Each HA connection's tunnel IDs have been registered
```

Visit your configured hostname (e.g., `https://zoe.yourdomain.com`) to access Zoe remotely.

## Troubleshooting

### Missing cert.pem

**Error**: `Error response from daemon: invalid mount config for type "bind": bind source path does not exist`

**Solution**: Follow step 2 above to authenticate and generate the cert.pem file.

### Invalid tunnel credentials

**Error**: `unable to find tunnel`

**Solution**: Ensure the tunnel ID in `cloudflared-config.yml` matches the credentials file name.

### Connection failed

**Error**: `cloudflared cannot reach the origin service`

**Solution**: 
1. Verify all Zoe services are running: `docker compose ps`
2. Check that services are on the same network: `docker network inspect zoe-network`
3. Test internal connectivity: `docker exec zoe-cloudflared ping zoe-core`

## Security Notes

⚠️ **Important Security Considerations:**

1. **Never commit** `cert.pem` or credentials JSON files to git
2. **Restrict access** to the tunnel hostname using Cloudflare Access policies
3. **Enable authentication** on all Zoe endpoints before exposing publicly
4. **Monitor logs** regularly for unauthorized access attempts
5. **Rotate credentials** periodically

## Disabling Cloudflared

To run Zoe without the Cloudflare tunnel:

```bash
# Standard startup (cloudflared disabled)
docker compose up -d
```

The service will not start unless you explicitly enable the `cloudflare` profile.

## References

- [Cloudflare Tunnel Documentation](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/)
- [Cloudflared GitHub](https://github.com/cloudflare/cloudflared)

