# Multica email (ZOE-48)

Zoe can send Multica-related email (verification, notifications) via **Resend** or **SMTP**. Configure in the host `.env`; do not commit secrets.

## Resend (recommended)

```bash
RESEND_API_KEY=re_xxxxxxxx
MULTICA_EMAIL_FROM=noreply@yourdomain.com
```

## SMTP

```bash
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASS=
MULTICA_EMAIL_FROM=noreply@yourdomain.com
```

See `docker-compose.modules.yml` (zoe-multica service comments) for the compose-level placeholder variables.

## Monitor

- **ZOE-1054**: observe OpenHuman rate caps via existing env limits; no code change required unless caps regress in production logs.
