# Platform-Specific Docker Compose Overrides

This repository supports **two platforms** with the same codebase:

- **Jetson Orin NX** (NVIDIA GPU, ARM64, 16GB RAM)
- **Raspberry Pi 5** (CPU-only, ARM64, 16GB RAM)

## Quick Start

### Jetson Orin NX (GPU)

```bash
cd /home/zoe/assistant
docker-compose -f docker-compose.yml -f docker-compose.jetson.yml up -d
```

### Raspberry Pi 5 (CPU)

```bash
cd /home/zoe/assistant
docker-compose -f docker-compose.yml -f docker-compose.pi.yml up -d
```

**Note:** Both platforms use identical path structure (`/home/zoe/assistant`) after adding `zoe` user to Pi.

## How It Works

The base `docker-compose.yml` contains **all shared configuration**:
- Service definitions
- Ports, networks, volumes
- Base environment variables
- Health checks
- Dependencies

Platform-specific overrides add:
- **Jetson**: GPU runtime settings (`runtime: nvidia`)
- **Pi**: CPU optimizations (`OLLAMA_NUM_THREADS`, `WHISPER_DEVICE=cpu`)

## File Structure

```
docker-compose.yml              # Base config (works on both)
docker-compose.jetson.yml       # GPU overrides (Jetson only)
docker-compose.pi.yml           # CPU overrides (Pi only)
```

## What's Different?

### Jetson (GPU)
- `zoe-ollama`: Uses NVIDIA GPU runtime
- `zoe-whisper`: Uses CUDA for transcription
- `zoe-tts`: Uses GPU for text-to-speech
- `zoe-litellm`: Uses GPU for LLM proxy

### Pi (CPU)
- `zoe-ollama`: Uses CPU threads (4 threads optimized)
- `zoe-whisper`: Uses CPU for transcription
- `zoe-tts`: Uses CPU for text-to-speech
- `zoe-litellm`: Uses CPU for LLM proxy

## Model Selection

Models are automatically selected based on platform:

- **Jetson**: Prefers GPU models (`gemma3n-e2b-gpu-fixed`)
- **Pi**: Uses CPU-optimized models (`llama3.2:1b`, `qwen2.5:7b`)

See `services/zoe-core/model_config.py` for model configurations.

## Troubleshooting

### Jetson: GPU not detected

```bash
# Check NVIDIA runtime
docker run --rm --runtime=nvidia nvidia/cuda:11.0-base nvidia-smi

# Verify GPU access
docker exec zoe-ollama nvidia-smi
```

### Pi: Slow performance

```bash
# Check CPU threads
docker exec zoe-ollama env | grep OLLAMA_NUM_THREADS

# Should show: OLLAMA_NUM_THREADS=4
```

### Wrong platform detected

If models aren't selecting correctly, check:
1. Correct override file is being used
2. Environment variables are set correctly
3. Model config detects platform properly

## Path Structure

**Unified paths:** Both platforms use `/home/zoe/assistant`

- Jetson: `/home/zoe/assistant` ✅
- Pi: `/home/zoe/assistant` ✅ (after adding `zoe` user)

The `PROJECT_ROOT` environment variable in docker-compose.yml is set to `/home/zoe/assistant` on both platforms.

## See Also

- `docs/PLATFORM_COMPARISON_REPORT.md` - Detailed comparison
- `docker-compose.jetson.yml` - GPU override details
- `docker-compose.pi.yml` - CPU override details

