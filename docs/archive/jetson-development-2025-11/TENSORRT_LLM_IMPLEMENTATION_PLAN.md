# TensorRT-LLM Implementation Plan for Jetson Orin NX
**Goal**: Achieve 0.2-0.5s first token latency (5-7x faster than current 10s)
**Timeline**: 2-3 days
**Current Status**: JetPack 6.2 (R36.4.3) ✅

---

## 📋 **Phase 1: Prerequisites** (2-3 hours)

### 1.1 Install CUDA Toolkit
```bash
sudo apt-get update
sudo apt-get install -y cuda-toolkit-12-6
export PATH=/usr/local/cuda/bin:$PATH
export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH
```

### 1.2 Install Build Dependencies
```bash
sudo apt-get install -y \
    build-essential \
    cmake \
    git \
    python3-dev \
    python3-pip \
    libopenblas-dev \
    ninja-build
```

### 1.3 Install Python Dependencies
```bash
pip3 install --upgrade pip
pip3 install \
    torch \
    transformers \
    accelerate \
    tensorrt \
    polygraphy
```

**Status**: ⏳ PENDING
**Estimated Time**: 2-3 hours (downloads + compilation)

---

## 📋 **Phase 2: TensorRT-LLM Installation** (3-4 hours)

### 2.1 Clone TensorRT-LLM Repository
```bash
cd /home/zoe
git clone https://github.com/NVIDIA/TensorRT-LLM.git
cd TensorRT-LLM
git checkout v0.12.0-jetson  # Jetson-specific branch
```

### 2.2 Build TensorRT-LLM
```bash
# Create build directory
mkdir -p build && cd build

# Configure for Jetson Orin (SM87)
cmake .. \
    -DCMAKE_BUILD_TYPE=Release \
    -DTRT_LIB_DIR=/usr/lib/aarch64-linux-gnu \
    -DCUDA_ARCHITECTURES="87" \
    -DBUILD_PYT=ON \
    -DBUILD_PYBIND=ON

# Build (will take 2-3 hours)
make -j$(nproc)

# Install Python package
cd ..
pip3 install -e .
```

**Status**: ⏳ PENDING
**Estimated Time**: 3-4 hours (compilation intensive)

---

## 📋 **Phase 3: Model Conversion** (1-2 hours)

### 3.1 Download/Prepare Hermes-3 Model
```bash
# Hermes-3 is already in Ollama
# Need to export to HuggingFace format first
cd /home/zoe/models
ollama show hermes3:8b-llama3.1-q4_K_M --modelfile > hermes3.modelfile

# Or download from HuggingFace
git lfs install
git clone https://huggingface.co/NousResearch/Hermes-3-Llama-3.1-8B
```

### 3.2 Convert to TensorRT-LLM Format
```bash
cd /home/zoe/TensorRT-LLM/examples/llama

# Convert checkpoint
python3 convert_checkpoint.py \
    --model_dir /home/zoe/models/Hermes-3-Llama-3.1-8B \
    --output_dir /home/zoe/models/hermes3-trt/checkpoint \
    --dtype float16 \
    --tp_size 1

# Build TensorRT engine
trtllm-build \
    --checkpoint_dir /home/zoe/models/hermes3-trt/checkpoint \
    --output_dir /home/zoe/models/hermes3-trt/engine \
    --gemm_plugin float16 \
    --gpt_attention_plugin float16 \
    --max_batch_size 1 \
    --max_input_len 4096 \
    --max_output_len 512 \
    --max_beam_width 1
```

**Status**: ⏳ PENDING
**Estimated Time**: 1-2 hours (conversion + engine build)

---

## 📋 **Phase 4: Inference Server Setup** (2-3 hours)

### Option A: Direct TensorRT-LLM (Simpler)
```python
# /home/zoe/assistant/services/zoe-tensorrt/inference.py
from tensorrt_llm import LLM
from tensorrt_llm.hlapi import PromptFormats

class TensorRTInference:
    def __init__(self):
        self.llm = LLM(
            model="/home/zoe/models/hermes3-trt/engine",
            temperature=0.7,
            top_p=0.9,
            max_tokens=512
        )
    
    async def generate(self, prompt: str):
        outputs = self.llm.generate([prompt])
        return outputs[0].text
```

### Option B: Triton Inference Server (Production)
```bash
# Pull Triton container for Jetson
docker pull nvcr.io/nvidia/tritonserver:24.06-py3-jetson

# Configure model repository
mkdir -p /home/zoe/triton-models/hermes3/1
cp /home/zoe/models/hermes3-trt/engine/* /home/zoe/triton-models/hermes3/1/

# Create config.pbtxt
cat > /home/zoe/triton-models/hermes3/config.pbtxt << 'EOF'
name: "hermes3"
backend: "tensorrtllm"
max_batch_size: 4
...
EOF

# Start Triton
docker run --gpus all --rm \
    -p 8001:8001 -p 8000:8000 -p 8002:8002 \
    -v /home/zoe/triton-models:/models \
    nvcr.io/nvidia/tritonserver:24.06-py3-jetson \
    tritonserver --model-repository=/models
```

**Status**: ⏳ PENDING
**Estimated Time**: 2-3 hours (setup + testing)

---

## 📋 **Phase 5: Zoe Integration** (3-4 hours)

### 5.1 Create TensorRT Service
```bash
cd /home/zoe/assistant/services
mkdir zoe-tensorrt
```

### 5.2 Build FastAPI Wrapper
```python
# /home/zoe/assistant/services/zoe-tensorrt/main.py
from fastapi import FastAPI
from tensorrt_inference import TensorRTInference

app = FastAPI()
inference = TensorRTInference()

@app.post("/generate")
async def generate(request: dict):
    prompt = request["prompt"]
    response = await inference.generate(prompt)
    return {"response": response}

@app.post("/generate_stream")
async def generate_stream(request: dict):
    # Implement streaming
    ...
```

### 5.3 Update Zoe Chat Router
```python
# /home/zoe/assistant/services/zoe-core/routers/chat.py

# Add TensorRT client
TENSORRT_URL = os.getenv("TENSORRT_URL", "http://zoe-tensorrt:8011")

async def call_tensorrt_streaming(model: str, messages: List[Dict]):
    """Call TensorRT-LLM for ultra-fast inference"""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{TENSORRT_URL}/generate_stream",
            json={"messages": messages},
            timeout=30.0
        )
        # Stream response
        ...
```

### 5.4 Add to docker-compose.yml
```yaml
zoe-tensorrt:
  build: ./services/zoe-tensorrt
  container_name: zoe-tensorrt
  ports:
  - 8011:8011
  volumes:
  - /home/zoe/models:/models
  runtime: nvidia
  environment:
  - MODEL_PATH=/models/hermes3-trt/engine
  networks:
  - zoe-network
```

**Status**: ⏳ PENDING
**Estimated Time**: 3-4 hours (coding + testing)

---

## 📋 **Phase 6: Testing & Optimization** (2-3 hours)

### 6.1 Benchmark Tests
```python
# Test script
import time, asyncio

async def benchmark():
    tests = [
        "Hi",
        "How are you?",
        "Add bread to shopping list"
    ]
    
    for prompt in tests:
        start = time.time()
        response = await call_tensorrt_streaming(prompt)
        latency = time.time() - start
        print(f"{prompt}: {latency:.3f}s")
```

### 6.2 Expected Performance
| Test | Current (CPU) | Target (TensorRT) | Improvement |
|------|---------------|-------------------|-------------|
| Greeting | 10.0s | 0.2-0.5s | **20-50x** |
| Action | 1.8s | 0.3-0.6s | **3-6x** |
| Conversation | 5.0s | 0.4-0.8s | **6-12x** |

### 6.3 Verify Tool Calling
- Test all 105 prompts from test suite
- Ensure 95%+ tool call accuracy maintained
- Validate action execution

**Status**: ⏳ PENDING
**Estimated Time**: 2-3 hours (comprehensive testing)

---

## 📋 **Phase 7: Production Deployment** (1-2 hours)

### 7.1 Update Model Config
```python
# model_config.py
INFERENCE_BACKEND = "tensorrt"  # vs "ollama"
```

### 7.2 Monitoring Setup
```python
# Add latency monitoring
@app.middleware("http")
async def track_tensorrt_latency(request, call_next):
    start = time.time()
    response = await call_next(request)
    latency = time.time() - start
    logger.info(f"TensorRT latency: {latency:.3f}s")
    return response
```

### 7.3 Fallback Strategy
```python
# If TensorRT fails, fall back to Ollama
try:
    response = await call_tensorrt_streaming(...)
except Exception as e:
    logger.warning(f"TensorRT failed, using Ollama fallback")
    response = await call_ollama_streaming(...)
```

**Status**: ⏳ PENDING
**Estimated Time**: 1-2 hours (deployment + validation)

---

## 📊 **Total Timeline**

| Phase | Task | Time | Status |
|-------|------|------|--------|
| 1 | Prerequisites | 2-3h | ⏳ Pending |
| 2 | TensorRT-LLM Build | 3-4h | ⏳ Pending |
| 3 | Model Conversion | 1-2h | ⏳ Pending |
| 4 | Inference Setup | 2-3h | ⏳ Pending |
| 5 | Zoe Integration | 3-4h | ⏳ Pending |
| 6 | Testing | 2-3h | ⏳ Pending |
| 7 | Production | 1-2h | ⏳ Pending |
| **TOTAL** | **14-21 hours** | **2-3 days** | **0% Complete** |

---

## 🎯 **Success Criteria**

✅ First token < 0.5s (from 10s)
✅ Complete response < 2s (from 10s)
✅ Tool calling accuracy ≥ 95%
✅ Action execution 100% success
✅ Stable under load
✅ GPU utilization > 80%

---

## ⚠️ **Risks & Mitigation**

### Risk 1: Build Failures
- **Mitigation**: Use pre-built wheels if available
- **Fallback**: Docker containers from NVIDIA

### Risk 2: Model Compatibility
- **Mitigation**: Test with smaller models first (Llama 3.2 3B)
- **Fallback**: Keep Ollama as backup

### Risk 3: Memory Issues
- **Mitigation**: Use FP8 quantization
- **Fallback**: Reduce batch size / context window

---

## 📚 **References**

- [Jetson AI Lab - TensorRT-LLM](https://www.jetson-ai-lab.com/tensorrt_llm)
- [NVIDIA TensorRT-LLM GitHub](https://github.com/NVIDIA/TensorRT-LLM)
- [Hackster.io - TensorRT-LLM on Jetson](https://dev.hackster.io/shahizat/running-llms-with-tensorrt-llm-on-nvidia-jetson-agx-orin-34372f)

---

**Ready to start? Let's begin with Phase 1: Prerequisites!** 🚀

