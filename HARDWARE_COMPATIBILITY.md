# Hardware Compatibility Guide

## Supported Platforms

Zoe is developed to run on **both** platforms simultaneously:

### 1. NVIDIA Jetson Orin NX 16GB (Production)
- **GPU**: NVIDIA Orin (CUDA 12.6, compute 8.7)
- **VRAM**: 15.3 GiB total, ~8-9 GiB available
- **Capabilities**: 
  - TensorRT-LLM acceleration
  - GPU-accelerated models (Hermes-3, Gemma, Qwen)
  - Hardware video encoding/decoding
  - CUDA-accelerated inference

### 2. Raspberry Pi 5 16GB (Development/Edge)
- **CPU**: ARM Cortex-A76 (4 cores @ 2.4GHz)
- **RAM**: 16GB LPDDR4X
- **Capabilities**:
  - CPU-only inference
  - Optimized quantized models (Q4, Q5)
  - Lower power consumption
  - Edge deployment

## Hardware Detection

**Environment Variable**: `HARDWARE_PLATFORM`
- Values: `jetson` | `pi5` | `auto`
- Location: `.env` or docker-compose.yml

**Auto-detection** (if not set):
```python
import platform
import os

def detect_hardware():
    # Check for Jetson
    if os.path.exists('/etc/nv_tegra_release'):
        return 'jetson'
    
    # Check for Raspberry Pi
    if os.path.exists('/proc/device-tree/model'):
        with open('/proc/device-tree/model', 'r') as f:
            if 'Raspberry Pi 5' in f.read():
                return 'pi5'
    
    # Check architecture
    arch = platform.machine()
    if 'aarch64' in arch:
        # Default to Pi if ARM but not Jetson
        return 'pi5'
    
    return 'unknown'
```

## Hardware-Specific Configurations

### Model Selection

**Jetson Orin NX**:
```python
PRIMARY_MODELS = {
    "action": "hermes3:8b-llama3.1-q4_K_M",  # 4.9GB, GPU
    "chat": "gemma3n-e2b-gpu-fixed",          # 5.6GB, GPU
    "vision": "gemma3n-e2b-gpu-fixed",        # Multimodal
    "memory": "qwen2.5:7b",                   # 4.7GB, GPU
}

GPU_CONFIG = {
    "num_gpu": 99,  # All layers on GPU
    "use_tensorrt": True,
    "cuda_visible_devices": "0"
}
```

**Raspberry Pi 5**:
```python
PRIMARY_MODELS = {
    "action": "phi3:mini",           # 2.2GB, CPU-optimized
    "chat": "phi3:mini",             # Fast on CPU
    "vision": "llava:7b-v1.6-q4",   # CPU-compatible vision
    "memory": "qwen2.5:3b",         # 2.0GB, smaller model
}

CPU_CONFIG = {
    "num_gpu": 0,  # CPU only
    "num_threads": 4,  # Match CPU cores
    "use_mmap": True,  # Memory-mapped models
}
```

### Docker Configuration

**docker-compose.yml** should detect hardware:

```yaml
services:
  zoe-ollama:
    image: ${OLLAMA_IMAGE:-ollama/ollama:latest}
    # Jetson: dustynv/ollama:r36.2.0
    # Pi5: ollama/ollama:latest
    
    deploy:
      resources:
        reservations:
          devices:
            # Only for Jetson
            - driver: ${GPU_DRIVER:-none}
              count: all
              capabilities: [gpu]
```

### Performance Targets

| Metric | Jetson Orin NX | Raspberry Pi 5 |
|--------|---------------|----------------|
| First Token | <2s (TensorRT) | <5s (CPU) |
| Tokens/sec | 50+ (GPU) | 8-12 (CPU) |
| Max Model Size | 8GB | 4GB |
| Concurrent Requests | 5+ | 2-3 |

## Hardware-Specific Features

### Jetson-Only Features
- ✅ TensorRT-LLM acceleration
- ✅ Multi-model GPU concurrent loading
- ✅ Hardware video encode/decode
- ✅ CUDA-accelerated embeddings
- ✅ GPU memory pooling

### Pi5-Only Optimizations
- ✅ CPU thread pinning
- ✅ Memory-mapped model loading
- ✅ Aggressive model unloading
- ✅ Lower memory footprint
- ✅ Power management modes

## Code Patterns

### Hardware-Aware Model Loading

```python
import os

HARDWARE = os.getenv('HARDWARE_PLATFORM', 'auto')

if HARDWARE == 'jetson':
    model_config = {
        "num_gpu": 99,
        "num_predict": 256,
        "use_tensorrt": True
    }
elif HARDWARE == 'pi5':
    model_config = {
        "num_gpu": 0,
        "num_threads": 4,
        "num_predict": 128,
        "use_mmap": True
    }
```

### Feature Flags

```python
FEATURES = {
    'tensorrt': HARDWARE == 'jetson',
    'gpu_acceleration': HARDWARE == 'jetson',
    'multi_model_concurrent': HARDWARE == 'jetson',
    'aggressive_unload': HARDWARE == 'pi5',
    'cpu_optimized': HARDWARE == 'pi5'
}
```

## Testing Requirements

**Must test on BOTH platforms:**
- ✅ Model loading and inference
- ✅ Memory usage under load
- ✅ Concurrent request handling
- ✅ Docker deployment
- ✅ Service health checks

## Deployment Differences

### Jetson Deployment
```bash
# Use Jetson-optimized images
docker-compose -f docker-compose.jetson.yml up -d

# Enable Super Mode for performance
sudo /usr/bin/jetson_clocks
```

### Pi5 Deployment
```bash
# Use standard ARM images
docker-compose -f docker-compose.pi5.yml up -d

# Optional: CPU governor for performance
echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor
```

## Current Status

**What's Aligned**:
- ✅ Docker configurations support both
- ✅ Model configs have hardware-specific settings
- ✅ Service health checks are platform-agnostic

**What Needs Work**:
- ⚠️ Auto-detection not fully implemented
- ⚠️ TensorRT integration is Jetson-specific only
- ⚠️ Performance benchmarks needed for Pi5
- ⚠️ Separate docker-compose files needed

## Migration Notes

When deploying to different hardware:
1. Set `HARDWARE_PLATFORM` environment variable
2. Use appropriate docker-compose file
3. Adjust `keep_alive` times based on available memory
4. Test with platform-specific test suite
5. Monitor resource usage and adjust accordingly
