# Qwen 2.5 7B Q4_0 Download Status

## Issue: Corrupted Download
**Error:** `tensor 'blk.24.ffn_up.weight' data is not within the file bounds`  
**Cause:** Incomplete/corrupted download (wget may have stopped early)

## Solution: Re-downloading
- Deleted corrupted file
- Restarting download with better progress monitoring
- Expected size: 3.86GB (exact)

## Monitor Progress
```bash
bash /home/zoe/assistant/check_download.sh
```

## Alternative: Use Llama 3.2 3B (Known Working)
If download keeps failing, we can use Llama 3.2 3B which:
- ✅ Works perfectly on Jetson
- ✅ Already downloaded (2GB)
- ⚡ Faster (smaller model)
- ⭐ Still great for voice

Switch command:
```bash
docker stop zoe-llamacpp && docker rm zoe-llamacpp
docker run -d \
  --name zoe-llamacpp \
  --runtime=nvidia \
  --network=zoe-network \
  -p 11434:11434 \
  -v /home/zoe/assistant/models:/models:ro \
  -e MODEL_PATH=/models/llama-3.2-3b-gguf/Llama-3.2-3B-Instruct-Q4_K_M.gguf \
  -e MODEL_NAME=llama-3.2-3b \
  -e CTX_SIZE=2048 \
  -e N_GPU_LAYERS=99 \
  -e THREADS=8 \
  -e PARALLEL=8 \
  -e N_BATCH=512 \
  -e N_UBATCH=256 \
  --ulimit memlock=-1 \
  --shm-size=2gb \
  --restart unless-stopped \
  zoe-llamacpp-optimized
```

## Current Status
- 🟡 Q4_0 re-downloading
- ⏳ ETA: ~10-15 minutes
- 💡 Llama 3.2 3B available as backup





