# Memory Optimization Status

## Current Situation
- **Total RAM**: 16GB
- **Available RAM**: ~2.3GB (heavily constrained)
- **Swap Usage**: 7.6GB / 7.6GB (100% used)
- **Models Loaded**: 2 models simultaneously (~6.2GB total)

## Model Status
- **gemma3n-e2b-gpu-fixed**: 5.6GB (full precision, NOT quantized)
- **phi3:mini**: 2.2GB (full precision)
- **llama3.2:3b**: 2.0GB (full precision)

## Gemma DevDay Optimizations
The [Google Gemma DevDay repository](https://github.com/asierarranz/Google_Gemma_DevDay/tree/main/Gemma3) uses **Quantization-Aware Training (QAT)** to reduce memory:
- Gemma 3 12B: 24GB → 6.6GB (int4 quantization)
- Can achieve 17.6 tokens/sec on 16GB RAM systems
- Reference: [Gemma 3 QAT Models](https://simonwillison.net/2025/Apr/19/gemma-3-qat-models/)

## Optimizations Applied
1. ✅ Reduced `keep_alive` from 30m → 5m (frees memory faster)
2. ✅ Changed default model to lightweight `phi3:mini` (2.2GB)
3. ✅ Updated fallback chain to prefer lightweight models

## Recommendations
1. **Use Quantized Models**: Check if quantized Gemma3 models are available (q4, q8 quantization)
2. **Single Model Loading**: Only keep one model loaded at a time
3. **Model Unloading**: Implement explicit model unloading after requests
4. **Memory Monitoring**: Add memory usage alerts

## Next Steps
- [ ] Check for quantized Gemma3 model variants
- [ ] Implement model unloading after requests complete
- [ ] Add memory monitoring and alerts
- [ ] Consider using smaller context windows (num_ctx) to reduce memory per request

