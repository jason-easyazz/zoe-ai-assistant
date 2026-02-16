# Channel Setup Skill

Connect and manage messaging channels (Telegram, Discord, WhatsApp) through conversation.

## Triggers

- connect telegram
- setup telegram
- setup discord
- connect discord
- add discord
- setup whatsapp
- connect whatsapp
- add a channel
- add a new channel
- link my telegram
- link my discord
- configure messaging
- set up a messaging channel

## Behavior

### Auto-Setup Path (Agent Zero available)
1. Ask which channel the user wants to connect
2. Attempt automated bot creation via Agent Zero browser automation
3. If successful, store credentials and configure webhook
4. Generate a verification code and optionally display QR on touch panel
5. Confirm setup is complete

### Manual Fallback Path
1. Ask which channel the user wants to connect
2. Provide step-by-step instructions for the platform
3. Wait for the user to provide credentials (bot token, etc.)
4. Store credentials and configure webhook
5. Test the connection
6. Generate a verification code for account linking

### Disconnect Flow
- "disconnect telegram" / "remove discord" / "unlink whatsapp"
- Removes stored credentials and webhook configuration

## API Endpoints (api_only)

- `POST /api/channels/{channel}/auto-setup` - Automated setup via Agent Zero
- `POST /api/channels/{channel}/configure` - Manual credential configuration
- `GET /api/channels/{channel}/status` - Check setup status
- `POST /api/channels/{channel}/test` - Test connection
- `DELETE /api/channels/{channel}/config` - Disconnect channel
- `POST /api/channels/{channel}/qr-code` - Generate linking QR code

## Response Style

Conversational and guided. Break complex steps into simple instructions.
If displaying a QR code, mention it will appear on the nearest touch panel.
