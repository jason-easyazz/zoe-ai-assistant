# Model-Specific GPU Settings

## Problem Solved
Different model architectures perform best with different GPU allocation strategies:
- **Gemma**: Works best with `num_gpu=99` (all layers to GPU)
- **Hermes-3**: Works best with `num_gpu=-1` (auto-detect, efficient allocation)
- **Qwen**: Works best with explicit layer counts (e.g., 43 layers for 7B model)

## Solution: Locked Per-Model GPU Settings

### Implementation

#### 1. ModelConfig Dataclass (`model_config.py`)
```python
@dataclass
class ModelConfig:
    name: str
    # ... other fields ...
    num_gpu: Optional[int] = None  # GPU layers:
                                    # -1 = auto-detect (recommended for Hermes)
                                    # 0 = CPU only
                                    # 1-99 = specific layer count
                                    # 99 = all layers (recommended for Gemma)
```

#### 2. Per-Model GPU Configuration

**Hermes-3** (Current Primary):
```python
"hermes3:8b-llama3.1-q4_K_M": ModelConfig(
    num_gpu=-1,  # Auto-detect - uses all available efficiently
    description="BEST function calling - 95% tool call accuracy"
)
```

**Gemma** (Multimodal/Vision):
```python
"gemma3n-e2b-gpu-fixed": ModelConfig(
    num_gpu=99,  # All layers to GPU - Gemma architecture optimized
    description="Multimodal model - vision, fast responses"
)
```

**Qwen 2.5** (Alternative):
```python
"qwen2.5:7b": ModelConfig(
    num_gpu=43,  # ~43 layers for 7B model
    description="Excellent function calling - 90% accuracy"
)
```

#### 3. Dynamic GPU Allocation in Chat Router

```python
# chat.py - Uses model-specific GPU settings
"num_gpu": model_config.num_gpu if model_config.num_gpu is not None else 1
```

## Benefits

1. **Optimal Performance Per Model**: Each model uses its ideal GPU configuration
2. **Easy Switching**: Change `self.current_model` in `model_config.py` - GPU settings follow automatically
3. **Memory Safety**: Can configure models to use less GPU if OOM occurs
4. **Architecture-Aware**: Respects different model architectures' GPU utilization patterns

## Hardware Optimization (Jetson Orin NX 16GB)

### Current Setup:
- **Hermes-3**: 4.9GB with `num_gpu=-1` (auto)
  - ✅ Leaves 11GB free
  - ✅ Fast inference
  - ✅ 95% tool calling accuracy

### Alternative (if switching):
- **Gemma3n**: 5.6GB with `num_gpu=99` (all layers)
  - ✅ Multimodal (vision)
  - ⚠️ Slower tool calling (needs auto-inject)
  - ✅ Good for image understanding

### Can Run Together?
**NO** - 4.9GB + 5.6GB = 10.5GB models + overhead = too close to 16GB limit

**Solution**: Keep ONE model loaded at a time with `keep_alive="30m"`

## Testing Different Models

```bash
# Test Hermes-3 (current)
curl -X POST "http://localhost:8000/api/chat?stream=false" \
  -H "X-Session-ID: dev-localhost" \
  -d '{"message": "add bread to shopping", "user_id": "test"}'
# Expected: Native tool call generation with num_gpu=-1

# Switch to Gemma in model_config.py:
# self.current_model = "gemma3n-e2b-gpu-fixed"

# Restart and test
docker restart zoe-core && sleep 40
curl -X POST ... # Same test
# Expected: Tool calls via auto-injection with num_gpu=99
```

## Future: Multi-GPU Support

For systems with multiple GPUs, can add:
```python
@dataclass
class ModelConfig:
    num_gpu: Optional[int] = None
    gpu_id: Optional[int] = None  # Which GPU to use (0, 1, etc.)
```

Then set `CUDA_VISIBLE_DEVICES={gpu_id}` per model instance.

## Summary

- **Hermes-3**: `num_gpu=-1` (auto) - Best for function calling
- **Gemma**: `num_gpu=99` (all) - Best for vision + multimodal
- **Qwen**: `num_gpu=43` (explicit) - Balanced alternative
- Settings locked in `MODEL_CONFIGS` dict
- Automatically applied when model is selected
- Easy to test different models - just change `self.current_model`!

