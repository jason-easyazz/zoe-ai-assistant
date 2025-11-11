# Installing Unsloth for Zoe Training

This guide explains how to install Unsloth for efficient LoRA fine-tuning on Raspberry Pi 5.

---

## Prerequisites

- Raspberry Pi 5 (4GB+ RAM recommended, 8GB optimal)
- Python 3.11+
- PyTorch installed
- ~5GB free disk space

---

## Installation Options

### Option 1: Standard Installation (Recommended)

```bash
cd /home/zoe/assistant

# Install Unsloth
pip install unsloth

# Verify installation
python3 -c "from unsloth import FastLanguageModel; print('✅ Unsloth installed successfully!')"
```

### Option 2: From Source (If Option 1 Fails)

```bash
# Clone Unsloth repository
git clone https://github.com/unslothai/unsloth.git /tmp/unsloth
cd /tmp/unsloth

# Install dependencies
pip install -e .

# Verify
python3 -c "from unsloth import FastLanguageModel; print('✅ Unsloth installed successfully!')"
```

### Option 3: Lightweight Installation (Pi-Optimized)

```bash
# Install minimal dependencies
pip install transformers peft accelerate bitsandbytes

# This gives you LoRA training capability without full Unsloth
# Training will be slower but still works on Pi 5
```

---

## Post-Installation Setup

### 1. Test Training Capability

```bash
cd /home/zoe/assistant
python3 scripts/train/test_training_setup.py
```

### 2. Set Up Cron Job

```bash
# Edit crontab
sudo crontab -e

# Add this line (runs at 2am daily)
0 2 * * * /home/zoe/assistant/scripts/train/nightly_training.sh >> /var/log/zoe-training.log 2>&1

# Verify cron job added
sudo crontab -l
```

### 3. Create Log File

```bash
sudo touch /var/log/zoe-training.log
sudo chown pi:pi /var/log/zoe-training.log
```

### 4. Enable Training in UI

1. Open http://localhost:8000/settings.html
2. Scroll to "AI Training & Learning"
3. Toggle "Overnight Training" to ON
4. Verify "Training Status" shows "Collecting Data"

---

## Verification

### Check All Components

```bash
# 1. Training database exists
ls -lh /app/data/training.db

# 2. Feedback endpoints work
curl -X GET "http://localhost:8000/api/chat/training-stats?user_id=default"

# 3. Model manager works
/home/zoe/assistant/tools/model-manager.py list

# 4. Graph engine loaded
python3 -c "from graph_engine import graph_engine; print(f'Nodes: {graph_engine.get_stats()}')"

# 5. Enhanced prompts active
curl -X POST "http://localhost:8000/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"message":"test","user_id":"default"}' | jq '.interaction_id'
```

If all commands succeed, you're ready for training!

---

## Manual Training Test

Before relying on overnight training, test manually:

```bash
# Collect some interactions first (chat with Zoe for 30 minutes)
# Then run:
python3 /home/zoe/assistant/scripts/train/nightly_training.py

# Check output
tail -50 /var/log/zoe-training.log
```

Expected output:
```
🌙 Starting nightly training at 2025-10-10 14:30:00
📊 Found 25 training examples
   • 3 corrections
   • 8 positive feedback
📝 Preparing training data...
💾 Saved training data to /home/zoe/assistant/models/adapters/training_data_2025-10-10.json
✅ Training pipeline completed
🌅 System ready for morning!
```

---

## Troubleshooting

### Unsloth Won't Install

**Error:** "No matching distribution found"

**Solution:**
```bash
# Update pip first
pip install --upgrade pip setuptools wheel

# Try again
pip install unsloth
```

**Alternative:** Use Option 3 (lightweight) instead

### Training Fails

**Check logs:**
```bash
tail -100 /var/log/zoe-training.log
```

**Common issues:**
- Not enough RAM → Reduce batch size
- Model not found → Pull model with Ollama first
- Permission denied → Run: `chmod +x scripts/train/*.sh`

### Cron Not Running

**Check cron status:**
```bash
sudo systemctl status cron

# Check cron logs
sudo grep CRON /var/log/syslog | tail -20
```

**Test manually:**
```bash
/home/zoe/assistant/scripts/train/nightly_training.sh
```

---

## Performance Expectations

### Raspberry Pi 5 (8GB RAM)

- **Training Time:** 2-4 hours for 50 examples
- **Memory Usage:** ~4-6GB during training
- **Adapter Size:** 10-50MB
- **Validation Time:** 5-10 minutes

### With 1B Parameter Models:

- ✅ llama3.2:1b - Fast, good tool calling
- ✅ gemma3:1b - Fast, good conversation
- ✅ qwen2.5:1.5b - Balanced performance

### With 3B Parameter Models:

- ⚠️ Possible but slower (4-6 hours)
- ⚠️ May need to reduce batch size
- ⚠️ Higher memory usage

---

## Next Steps After Installation

1. ✅ Verify installation with test script
2. ✅ Chat with Zoe for 5-7 days (collect data)
3. ✅ Use feedback buttons liberally
4. ✅ First manual training test
5. ✅ Enable overnight training
6. ✅ Monitor improvements over 2-4 weeks

---

**Questions?** Check `/home/zoe/assistant/docs/INTELLIGENCE_ENHANCEMENT_STATUS.md` for current status.












