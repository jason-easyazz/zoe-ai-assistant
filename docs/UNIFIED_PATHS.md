# Unified Path Structure - Both Platforms

## ✅ Identical Folder Structure

Both platforms now use **identical paths**:

- **Jetson Orin NX:** `/home/zoe/assistant`
- **Raspberry Pi 5:** `/home/zoe/assistant` (after adding `zoe` user)

## Benefits

1. **No path differences** - Same `PROJECT_ROOT` on both platforms
2. **Simpler configuration** - No platform-specific path handling
3. **Easier maintenance** - Single path reference throughout codebase
4. **Consistent documentation** - Same commands work on both platforms

## Pi Setup Steps

To match Jetson structure on Pi:

```bash
# On Raspberry Pi
sudo useradd -m -s /bin/bash zoe
sudo usermod -aG docker zoe
sudo mkdir -p /home/zoe/assistant
sudo chown zoe:zoe /home/zoe/assistant

# Clone repository
cd /home/zoe/assistant
git clone https://github.com/jason-easyazz/zoe-ai-assistant.git .

# Or if already cloned elsewhere
sudo mv /home/zoe/assistant /home/zoe/assistant
sudo chown -R zoe:zoe /home/zoe/assistant
```

## Docker Compose Configuration

The `PROJECT_ROOT` environment variable is set identically on both:

```yaml
environment:
  - PROJECT_ROOT=/home/zoe/assistant
```

Volume mounts also use the same path:

```yaml
volumes:
  - /home/zoe/assistant:/home/zoe/assistant:rw
```

## Verification

After setup on Pi, verify paths match:

```bash
# On both platforms
echo $PROJECT_ROOT  # Should show: /home/zoe/assistant
pwd                  # Should show: /home/zoe/assistant
```

## Migration Notes

If Pi already has code in `/home/zoe/assistant`:

1. **Stop services:**
   ```bash
   cd /home/zoe/assistant
   docker-compose down
   ```

2. **Move directory:**
   ```bash
   sudo mv /home/zoe/assistant /home/zoe/assistant
   sudo chown -R zoe:zoe /home/zoe/assistant
   ```

3. **Update paths** (if any hardcoded):
   ```bash
   cd /home/zoe/assistant
   # Paths should already be correct if using PROJECT_ROOT env var
   ```

4. **Restart services:**
   ```bash
   docker-compose -f docker-compose.yml -f docker-compose.pi.yml up -d
   ```

## Status

✅ **Jetson:** `/home/zoe/assistant` (complete)  
🔄 **Pi:** `/home/zoe/assistant` (after adding `zoe` user)

Once Pi has `zoe` user, both platforms are **100% identical** in structure.

