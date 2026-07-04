# Hardware Compatibility Guide

## Supported Platforms

Zoe AI Assistant is designed to run across multiple ARM64 platforms with adaptive performance optimization.

---

## Platform Comparison

| Feature | Jetson Orin NX 16GB | Raspberry Pi 5 16GB | Mac Mini M4* |
|---------|---------------------|---------------------|--------------|
| **Status** | ✅ Primary (Production) | 🚫 Retired (Apr 2026) | 📝 Planned |
| **Architecture** | ARM64 + NVIDIA GPU | ARM64 CPU-only | ARM64 CPU-only |
| **Memory** | 16GB Unified | 16GB LPDDR4X | 16GB+ |
| **LLM Inference** | GPU-accelerated | CPU-only | CPU-only |
| **GPU Layers** | 99 (all on GPU) | 0 (CPU only) | 0 (CPU only) |
| **Concurrent Users** | 10+ | 3-5 | TBD |
| **Primary Models** | gemma-4-e4b-qat (+ MTP) | phi3:mini, llama3.2:3b | TBD |
| **Model Size** | Up to 8GB | Up to 4GB | TBD |
| **Tokens/sec** | 50+ (GPU) | 8-12 (CPU) | TBD |
| **Context Window** | 4096 tokens | 2048 tokens | TBD |
| **Power Usage** | 15-25W | 5-10W | 10-20W (est) |

\* Mac Mini M4 support is planned but untested. ARM64 architecture should be compatible.

> **Note (Apr 2026):** Raspberry Pi 5 support has been retired. Pi-specific scripts and configs are archived under `docs/archive/retired-2026-04-18/`. The Jetson Orin NX is the sole current production target.

---

## Detailed Platform Information

### NVIDIA Jetson Orin NX 16GB

**Hardware:**
- CPU: 8-core ARM Cortex-A78AE @ 2.0GHz
- GPU: NVIDIA Orin (1024 CUDA cores, Ampere architecture)
- Memory: 16GB LPDDR5 (unified CPU/GPU)
- VRAM: ~8-9GB available for models
- Storage: NVMe SSD recommended

**Software Requirements:**
- JetPack 5.1.3+ (R36.2.0+)
- NVIDIA Container Runtime
- CUDA 12.6+
- Docker & Docker Compose

**Optimizations:**
- GPU acceleration for all LLM layers
- CUDA-accelerated inference
- TensorRT optimization (optional)
- `jetson_clocks` for maximum performance

**Runtime Configuration:**
The Jetson production stack is split between Docker and host-native user
services. Docker runs PostgreSQL/auth/UI/HA bridge services from
`docker-compose.yml`; `zoe-data`, `llama-server`, Hermes, OpenClaw, and Kokoro
run as `systemctl --user` services. `docker-compose.jetson.yml` is a no-op
compatibility overlay kept only so older compose commands validate.

**Recommended Models:**
- `gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf` (~3.5GB, Q4_K_XL) — **canonical live brain** (+ `mtp-gemma-4-E4B-it.gguf` drafter), served host-native by `llama-server` on `:11434`
- `qwen2.5-coder-7b-gguf` (4.5GB, Q4_K_M) — optional coding model

**Performance:**
- First token: <2s
- Generation: 50+ tokens/sec
- Concurrent requests: 10+
- VRAM usage: 6-8GB under load

---

### Raspberry Pi 5 16GB *(Retired April 2026)*

> Pi 5 support is retired. Scripts and configs archived at `docs/archive/retired-2026-04-18/`. Content below is kept for historical reference only.

**Hardware:**
- CPU: 4-core ARM Cortex-A76 @ 2.4GHz
- GPU: VideoCore VII (not used for AI)
- Memory: 16GB LPDDR4X
- Storage: microSD or NVMe SSD (via HAT)

**Software Requirements:**
- Raspberry Pi OS (64-bit)
- Docker & Docker Compose
- Sufficient cooling (fan recommended)

**Optimizations:**
- CPU thread pinning (4 threads)
- Memory-mapped model loading
- Smaller quantized models
- Aggressive model unloading

**Runtime Configuration:**
The Pi 5 server overlay is retired. `docker-compose.pi.yml` is a no-op
compatibility overlay; do not use it to run server-side LLM/STT containers.
Touch-panel and voice-device setup lives under `scripts/setup/touchscreen/` and
the voice installer scripts.

**Recommended Models:**
- `phi3:mini` (2.2GB, Q4_K_M)
- `llama3.2:3b` (2.0GB, Q4_K_M)
- `qwen2.5:3b` (2.0GB, Q4_K_M)
- `gemma2:2b` (1.6GB, Q4_K_M)

**Performance:**
- First token: <5s
- Generation: 8-12 tokens/sec
- Concurrent requests: 3-5
- RAM usage: 4-6GB under load

---

### Mac Mini M4 (Planned)

**Hardware:**
- CPU: Apple M4 (10-core)
- GPU: Apple Neural Engine (not used for llama.cpp)
- Memory: 16GB+ unified
- Storage: SSD

**Software Requirements:**
- macOS Sequoia 15.0+
- Docker Desktop for Mac (ARM64)
- Rosetta 2 (if needed)

**Expected Optimizations:**
- CPU-only inference
- Metal acceleration (if supported by llama.cpp)
- Efficient unified memory access

**Docker Configuration:**
```bash
docker-compose -f docker-compose.yml -f docker-compose.mac.yml up -d
```

**Expected Models:**
- Similar to Raspberry Pi 5
- `phi3:mini`, `llama3.2:3b`
- 2-4GB models recommended

**Expected Performance:**
- First token: <3s (estimate)
- Generation: 15-25 tokens/sec (estimate)
- Concurrent requests: 5-8 (estimate)

**Status:** 🚧 Untested - contributions welcome!

---

## Hardware Detection

Zoe automatically detects hardware platform:

```python
def detect_hardware():
    # Check for Jetson
    if os.path.exists('/etc/nv_tegra_release'):
        return 'jetson'
    
    # Check for Raspberry Pi
    if os.path.exists('/proc/device-tree/model'):
        with open('/proc/device-tree/model', 'r') as f:
            if 'Raspberry Pi' in f.read():
                return 'pi5'
    
    # Check for macOS
    if platform.system() == 'Darwin':
        if platform.machine() == 'arm64':
            return 'mac_silicon'
    
    return 'unknown'
```

---

## Model Selection Strategy

### By Platform

**Jetson Orin NX:**
```python
PRIMARY_MODELS = {
    "chat": "gemma-4-E4B-it-qat",     # ~3.5GB Q4_K_XL, GPU-accelerated (canonical brain rock + MTP drafter)
    "code": "qwen2.5-coder-7b-gguf",  # 4.5GB, GPU-accelerated (optional coding model)
}
GPU_CONFIG = {
    "num_gpu": 99,  # All layers on GPU
    "n_gpu_layers": 99,
    "use_mlock": True
}
```

**Raspberry Pi 5:**
```python
PRIMARY_MODELS = {
    "chat": "phi3:mini",           # 2.2GB, CPU-optimized
    "code": "llama3.2:3b",         # 2.0GB, CPU-optimized  
    "fast": "gemma2:2b",           # 1.6GB, CPU-optimized
}
CPU_CONFIG = {
    "num_gpu": 0,        # CPU only
    "num_threads": 4,    # Match CPU cores
    "use_mmap": True     # Memory-mapped models
}
```

---

## Performance Tuning

### Jetson Orin NX

**Enable Super Mode:**
```bash
sudo /usr/bin/jetson_clocks
```

**Monitor GPU:**
```bash
watch -n 1 nvidia-smi
```

**Check Tegra Stats:**
```bash
tegrastats
```

### Raspberry Pi 5

**Set CPU Governor:**
```bash
echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor
```

**Monitor Resources:**
```bash
htop
```

**Check Temperature:**
```bash
vcgencmd measure_temp
```

---

## Storage Requirements

| Component | Size | Notes |
|-----------|------|-------|
| Docker Images | ~15GB | All services combined |
| Models (Jetson) | ~10-20GB | Depends on models downloaded |
| Models (Pi) | ~5-10GB | Smaller models |
| Databases | <1GB | PostgreSQL (primary), plus module-local SQLite files |
| Logs | ~1-2GB | Varies with usage |
| **Total (Jetson)** | **~30-40GB** | Recommended: 128GB+ SSD |
| **Total (Pi)** | **~20-30GB** | Recommended: 64GB+ SD/SSD |

---

## Network Requirements

**Bandwidth:**
- Minimum: 10 Mbps down, 5 Mbps up
- Recommended: 50+ Mbps down, 10+ Mbps up (for cloud API fallbacks)

**Ports Used:**
- 80/443: Web UI (nginx)
- 8000: Core API (zoe-data, host-native FastAPI + OpenClaw bridge)
- 8002: Auth API (zoe-auth)
- 8003: MCP Server
- 11434: LLM Inference (llama-server, host-native)
- 7880-7882: LiveKit (WebRTC)
- 8123: Home Assistant
- 5678: N8N

---

## Migration Between Platforms

### From Jetson to Pi

1. Backup data: `tar -czf zoe-data-backup.tar.gz data/`
2. Copy backup to Pi
3. On Pi: Extract and use Pi docker-compose override
4. Models will automatically switch to CPU-optimized versions

### From Pi to Jetson

1. Backup data: `tar -czf zoe-data-backup.tar.gz data/`
2. Copy backup to Jetson
3. On Jetson: Extract and use Jetson docker-compose override
4. Download GPU-accelerated models
5. Enjoy 5-10x performance boost!

---

## Troubleshooting

### Jetson Issues

**GPU not detected:**
```bash
# Check NVIDIA runtime
docker run --rm --runtime nvidia nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi
```

**Out of memory:**
- Reduce model size (use smaller quantization)
- Reduce `num_gpu` layers
- Monitor with `nvidia-smi`

### Pi Issues *(Historical -- Pi retired Apr 2026)*

**Slow inference:**
- Check CPU governor is set to `performance`
- Reduce context window size
- Use smaller models (2-3B parameters)

**Out of memory:**
- Close other applications
- Reduce concurrent requests
- Use lighter models

---

## Future Platform Support

**Under Consideration:**
- **Intel x86_64** - Standard desktop/server (CPU-only)
- **NVIDIA GTX/RTX** - Consumer GPUs (via CUDA)
- **AMD GPU** - Via ROCm support
- **Orange Pi 5** - Similar to Pi but with NPU

**Contributions Welcome!**

If you successfully run Zoe on a platform not listed here, please open an issue or PR with:
- Hardware specifications
- Performance measurements
- Configuration needed
- Any platform-specific optimizations

---

## Recommendations

**For Production Use:**
- **Primary**: Jetson Orin NX (sole current production target)

**For Development:**
- **Mac M4** - Coming soon, ideal for developers
- Any ARM64 host running `llama-server` should work for basic dev

**For Home Use:**
- **Jetson** -- best performance, current recommendation
- Raspberry Pi 5 support was retired April 2026

---

**Questions?** See [docs/guides/](docs/guides/) for platform-specific setup instructions.

---

## TTS Voice Stack (Jetson Orin NX)

Zoe uses a waterfall of TTS providers, attempted in order until one succeeds:

```
1. Kokoro PyTorch sidecar  :10201  af_sky  GPU  ~150–400ms warm  ← primary / natural voice
2. wyoming-piper           :10200  en_GB-cori  CPU  ~111ms       ← fast fallback (British accent)
3. Kokoro ONNX             in-process  af_sky  CPU  ~900ms       ← slow fallback
4. Edge TTS (cloud)        internet    en-AU-NatashaNeural       ← cloud fallback
5. espeak-ng               in-process  robotic                   ← last resort
```

### Primary voice: Kokoro PyTorch sidecar

- **Voice**: `af_sky` (American English family — perceived as natural Australian by users)
- **Package**: `kokoro==0.9.4` (PyTorch-based, uses GPU via CUDA)
- **Service**: `~/.config/systemd/user/kokoro-tts.service`
- **Script**: `scripts/setup/kokoro_sidecar.py`
- **Port**: `127.0.0.1:10201`
- **Env (systemd)**: `KOKORO_VOICE=af_sky`, `KOKORO_SIDECAR_PORT=10201`
- **Env (zoe-data)**: `ZOE_KOKORO_SIDECAR_URL=http://127.0.0.1:10201`, `ZOE_KOKORO_VOICE=af_sky`

### Jetson-specific CUDA fixes (both documented in `kokoro_sidecar.py`)

| Problem | Symptom | Fix |
|---|---|---|
| Jetson nvgpu incomplete NVML support | `NVML_SUCCESS == r INTERNAL ASSERT FAILED` at `CUDACachingAllocator.cpp:1131` | `PYTORCH_CUDA_ALLOC_CONF=backend:cudaMallocAsync` set before `torch` import — bypasses NVML memory queries |
| NumPy version mismatch | `RuntimeError: Numpy is not available` during WAV conversion | WAV built with `struct.pack` from `tensor.tolist()` — no `tensor.numpy()` call |

### Verifying the voice is working

```bash
# Check service state
systemctl --user status kokoro-tts.service

# Check health (should show device: cuda)
curl -s http://127.0.0.1:10201/health | python3 -m json.tool

# Live synthesis test (should return a RIFF WAV in ~0.4s warm)
time curl -s -X POST http://127.0.0.1:10201/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text":"This is Zoe. How can I help you?","voice":"af_sky"}' \
  -o /tmp/test.wav && python3 -c "
with open('/tmp/test.wav','rb') as f: h=f.read(4)
print('OK' if h==b'RIFF' else f'BAD header: {h}')
"
```

### If the voice sounds wrong (British accent instead of natural)

The British accent means wyoming-piper is being used instead of the Kokoro sidecar.

```bash
# Restart the sidecar
systemctl --user restart kokoro-tts.service

# Watch for the warmup line in the logs (takes ~15s on cold start)
journalctl --user -u kokoro-tts -f | grep -E "ready|CUDA|error|Error"

# If the service won't start, check the script exists
ls -la ~/assistant/scripts/setup/kokoro_sidecar.py
```

The script **must be committed to git** — it will be lost on cleanup if not tracked.
Confirm with: `git -C ~/assistant log --oneline scripts/setup/kokoro_sidecar.py`
