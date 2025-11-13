#!/usr/bin/env python3
"""
Minimal vLLM test - bypasses server, tests core functionality
This isolates whether the issue is in vLLM core or the server wrapper
"""
import os
import sys

# CRITICAL: Apply environment variables BEFORE importing torch/vLLM
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
os.environ["CUDA_LAUNCH_BLOCKING"] = "1"
os.environ["VLLM_USE_V1"] = "0"

print("🔬 Testing Minimal vLLM Configuration")
print("=" * 60)
print("")

# Test 1: Can we import vLLM?
print("1️⃣ Testing vLLM import...")
try:
    from vllm import LLM, SamplingParams
    print("✅ vLLM import successful")
except Exception as e:
    print(f"❌ vLLM import failed: {e}")
    sys.exit(1)

# Test 2: Can we load the model?
print("\n2️⃣ Loading model with minimal configuration...")
try:
    llm = LLM(
        model="/models/llama-3.2-3b-awq",
        quantization="awq",
        dtype="float16",
        gpu_memory_utilization=0.5,  # Very conservative
        max_model_len=512,  # Tiny context (less memory pressure)
        trust_remote_code=True,
        enforce_eager=True,  # CRITICAL for Jetson
        disable_custom_all_reduce=True,  # CRITICAL for Jetson
        tensor_parallel_size=1,
    )
    print("✅ Model loaded successfully!")
except Exception as e:
    print(f"❌ Model loading failed: {e}")
    print("\nStack trace:")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 3: Can we generate text?
print("\n3️⃣ Testing inference...")
try:
    params = SamplingParams(temperature=0.7, max_tokens=10)
    outputs = llm.generate(["Hello"], params)
    
    response = outputs[0].outputs[0].text
    print(f"✅ Inference successful!")
    print(f"   Prompt: 'Hello'")
    print(f"   Response: '{response}'")
    
except Exception as e:
    print(f"❌ Inference failed: {e}")
    print("\nStack trace:")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 60)
print("🎉 ALL MINIMAL TESTS PASSED!")
print("vLLM core functionality is working on this Jetson.")
print("")


