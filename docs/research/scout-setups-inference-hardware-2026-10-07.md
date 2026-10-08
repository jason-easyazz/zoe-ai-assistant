# Scout: setups, inference and hardware that would lift the ceiling (2026-10-07)

Slice: SETUPS, INFERENCE, HARDWARE. Read first: `docs/research/inference-speech-stack-2026-10-03.md` and
`docs/knowledge/engineering-off-box.md` (plus `agent-sessions-off-box-2026-10-04.md`,
`bakeoff-ram-latency-optimisation-2026-10-06.md`, `mac-virtual-panel.md` for context).
Evidence: **[fetched]** = page read 2026-10-07, URL given; **[box]** = measured read-only on this box or the Pi
2026-10-07; **[derived]** = my arithmetic; **[unverified]** = secondary or projection. Nothing was installed,
restarted or bought; no inference was sent to the live brain.

## 0. What the two docs already settle (not repeated)

- Decode is not where the field is ahead: the NVIDIA forum's Gemma 4 E4B Q4_K_M MTP on Orin NX is ~13 -> ~18 tok/s
  (posted 2026-06-06, https://forums.developer.nvidia.com/t/llama-cpp-mtp-lifted-llm-performance-of-jetson-orin/372493);
  Zoe's live 25-35 tok/s with MTP + `--swa-full` + q8_0 KV + 81 % prompt reuse is already above every published Orin NX figure.
  KV-cache quantisation, prompt caching across turns and the MTP depth grid are done ("Already right", doc 10-03 sec 4).
- Zero-copy/unified memory in ggml-cuda is disabled upstream (`integrated = false`); a 2.1 GiB block-weight copy is paid.
- Agent sessions off the box (the 1 GB/session RAM item) is decided (record 10-04); the Pi 5 verdict for *sessions* was "no".
- The bake-off pushed the Hindsight stack to ~463 MB steady / 479 MB burst (2026-10-06), still over the 2 GB voice floor.

## 1. What is NEW today (the delta this scout adds)

1. **The whole hardware market repriced up in 2026 (memory shortage).** Jetson +50-100 % effective 2026-07-22; Orin NX 16 GB
   module $599 -> $999; AGX Orin 64 GB $1,599 -> $2,999; AGX Orin 32 GB $899 -> $1,799; Thor dev kit $3,499 -> $5,499;
   T4000 module $1,999 -> $2,999 (CNX Software, https://www.cnx-software.com/2026/07/22/nvidia-increases-the-price-of-jetson-modules-and-devkits-by-up-to-101/).
   DGX Spark $3,999 -> $4,699 (Feb 2026; https://intuitionlabs.ai/articles/nvidia-dgx-spark-review). Pi 5 16 GB ~ $220 vs $120 MSRP
   (secondary aggregator results, [unverified] exact). Mac mini M4 base went $599 -> $699 (June) -> new M6 base $899
   (https://www.macworld.com/article/3220063/apple-launches-new-m6-mac-mini-with-another-price-hike.html). **Consequence: a bigger
   Jetson is now a bad buy; Apple silicon is the best RAM-per-dollar and bandwidth-per-dollar left.**
2. **JetPack 7.2 (Jetson Linux R39.2, 2026-06) now supports the Orin family including Orin NX 16 GB**: Ubuntu 24.04, kernel 6.8.12,
   CUDA 13.2, display-driver branch 595.58 (we run R36.4.3 / driver 540.4 / CUDA 12.6)
   (https://forums.developer.nvidia.com/t/does-jetpack-7-2-support-orin-nx-16gb-and-what-display-driver-version-does-it-ship/377277).
   Seeed ships a 7.2 BSP for the J401/J501 carriers (our `mfi_recomputer-super-orin-nx-16g-j401` image is that family)
   (https://forum.seeedstudio.com/t/nvidia-has-officially-announced-jetpack-7-2-june-1-2026-any-plans-for-j401-agx-orin-32gb-support/295471);
   it notes an SSH-key bug in the backup/restore scripts. Seeed measured on **AGX Orin 32 GB with llama.cpp + a 27B model**:
   memory after load **24.6 -> 14.7 GB (-40 %)**, prompt processing +42 % (18.2 -> 25.8 tok/s), generation +28 % (4.3 -> 5.5 tok/s),
   GPU 930 MHz -> 1.36 GHz in the high-performance mode (https://wiki.seeedstudio.com/jetpack72_deep_dive/); Seeed itself labels it
   "workload-specific, not a guaranteed reduction" (https://wiki.seeedstudio.com/jetpack_7_2_memory_optimization/).
   The drop is the size of a weights-held-twice effect, so it looks like the CPU-side file copy of the weights stopped being held
   alongside the GPU copy [inferred, not stated by Seeed]; that is exactly our 2.03 GB RssFile / 1.95 GB VmLck. **Unmeasured on Orin NX
   with Gemma 4 E4B.** This touches doc 10-03's "no unified-memory experiments" (that was a source reading of b11194 on JP 6.2).
3. **TensorRT Edge-LLM 0.11.0 (2026-09-29) lists Gemma 4 E2B/E4B/12B with paired `gemma4_assistant` MTP**, but only on JetPack 7.2 + CUDA 13.2,
   and on Orin = FP16/INT8/INT4 only (no FP8/FP4) (support matrix: https://nvidia.github.io/TensorRT-Edge-LLM/latest/user_guide/getting_started/support-matrix.html;
   models: .../getting_started/supported-models.html). **No published Orin NX Gemma 4 number**; the Gemma 4 `llm_bench` crashes on missing PLE bindings
   (Issue #230, opened 2026-10-01 on Thor, no maintainer reply: https://github.com/NVIDIA/TensorRT-Edge-LLM/issues/230). The docs' MTP example
   is for 12B, with no measured speedup.
4. **Mac mini line replaced 2026-09-22**: M6 (12c CPU/12c GPU, up to 32 GB, 170 GB/s, from $899 for 16 GB; a 32 GB/1 TB config is sold) and
   M5 Pro (up to 64 GB, 307 GB/s, from $1,699 for 24 GB; +$600 to 48 GB, +$400 more to 64 GB -> ~$2,699 [derived from AppleInsider/Macworld])
   (https://www.apple.com/newsroom/2026/09/the-new-mac-mini-and-mac-studio-are-available-today/,
   https://appleinsider.com/articles/26/09/22/m5-pro-mac-mini-vs-m4-pro-mac-mini-compared-specs-hardware-price). Reference: Orin NX 16 GB is 102.4 GB/s
   (https://dnhkng.github.io/posts/jetson-orin-nx-vram-tuning/, same Seeed J4012 module). The 32 GB M6 price was not on any page I could read [unverified].
5. **The Pi panel has 6.6 GB MemAvailable right now [box: `ssh zoe-pi free -m` 14:21, 8,063 total, 6,651 available, 4 cores, load 1.0, 66 C, SD card 52 % full].**
   The Orin shows 1.67 GB available (MemTotal 16.0 GB, already headless, zram off, `vm.swappiness=10`). The cheapest unused RAM Zoe owns is on the Pi.

## 2. Verdicts per item (ADOPT-TRIAL / BORROW / SKIP)

### (1) Inference engines on Orin

| Item | Evidence | Verdict | Cost | Unlocks for Samantha |
|---|---|---|---|---|
| **TensorRT Edge-LLM** (NVIDIA, Gemma 4 E4B + MTP) | Needs JP 7.2 + a device-side engine build; Orin INT4/FP16/INT8 only; Gemma 4 bench crash open; no Orin NX E4B number; Orin published numbers are Qwen3 (AGX Orin 64: Qwen3-4B INT4 55.3 tok/s vanilla; Orin NX 16: Qwen3-1.7B 59.0 tok/s; https://nvidia.github.io/TensorRT-Edge-LLM/latest/user_guide/performance/performance-benchmarks.html). JetSpec tree speculation (9.6x) is H100/Qwen3-8B | **BORROW** (watch; re-scout in 4 weeks) | 0 to watch; a trial is a day plus the 7.2 flash | Only if it beats 25-35 tok/s with MTP AND holds Gemma 4's PLE table cheaper than llama.cpp. Not proven |
| **vLLM on Jetson** (`ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-orin`) | NVIDIA's tutorial: "vLLM tends to deliver better serving performance", no numbers (https://www.jetson-ai-lab.com/tutorials/gemma4-on-jetson/). Orin Nano LFM 1.2B: vLLM ~40 tok/s, 17 concurrent users, 6.4 GB vs llama.cpp 24-35 tok/s, 4 users, 4.2 GB (https://learnopencv.com/deployment-on-edge-vllm-on-jetson/, 2026-01-06) | **BORROW for the nightly digest only**: continuous batching in a brain-stop window; never the voice lane (+2 GB RAM, loses our MTP/cache-ram path) | 1 container pull + one window | Faster overnight reflection/digest over many days of turns. Needs a measured digest wall-time first (not recorded in the docs I read) |
| **MLC-LLM / NanoLLM / exllama** | NanoLLM/jetson-containers are superseded by the above; no Gemma 4 + MTP path found; exllama is x86 CUDA | **SKIP** | - | - |
| **Probabilistic MTP, lazy PLE, `-ub` sweep** | already actions 3/5/9 in doc 10-03 | (not new) | - | - |
| **KV q4 / prompt cache / continuous batching in llama.cpp** | KV is already small (24 KV-owning layers, 2 KV heads); `--parallel >1` + draft-mtp has cross-slot contamination upstream (#28286, per doc 10-03) | **SKIP** | - | - |
| **JetPack 7.2 itself (the real lever here)** | item 1.2 above | **ADOPT-TRIAL** (ranked #1) | software + a spare NVMe/USB-NVMe | RAM headroom, faster prompt processing, access to TRT Edge-LLM and vLLM >= 0.22 (needs CUDA 13) |

### (2) RAM levers on Tegra and by moving things

| Lever | What it frees / costs | Verdict |
|---|---|---|
| Headless / services off | NVIDIA blog: ~700 MB (desktop) + 68 MB display carveouts + 33 MB camera carveouts, 865 MB "system services" (2026-04-20, updated 07-13; https://developer.nvidia.com/blog/maximizing-memory-efficiency-to-run-bigger-models-on-nvidia-jetson/); tools: `NVIDIA-AI-IOT/jetson-device-skills` (`jetson-memory-audit`, `jetson-headless-mode`, `jetson-inference-mem-tune`). **Box is already `multi-user.target`, no display manager [box]** | **SKIP** the headless part (done); **BORROW `jetson-memory-audit`** as a free read-only instrument |
| Carveout removal | 101 MB total, needs a BSP rebuild + reflash; Seeed rates platform memory reclamation "high" risk | **SKIP** |
| zram/zswap | zram already off (record 10-04) and `MemorySwapMax=0` guards the voice stack; NVIDIA's blog is silent on it | **SKIP** (nothing new) |
| `drop_caches` before a model load | forum workaround on JP < 7.2 for failed loads; not a steady-state saving | **BORROW** into the brain-restart runbook only |
| NvMap fragmentation fix | `GGML_CUDA_ENABLE_UNIFIED_MEMORY=1` + `--fit off` stopped contiguous-alloc failures (Orin Nano, JP 7.2/7.2.1; https://forums.developer.nvidia.com/t/nvmap-allocator-crash-on-jetson-orin-nano-running-llama-cpp-vlm-gemma-3-4b-root-cause-found-jetpack-7-2-7-2-1/380334). We already run `--fit off`. The managed-memory flag is untested on E4B+MTP | **BORROW** as a one-flag brain-window test only if the CUDA-OOM crash-loop signature returns |
| **Move the memory engine (Hindsight + Postgres + shim, ~463 MB) to the Pi** | frees ~0.46 GB on the Orin only if Hindsight were adopted (run 2 verdict `KEEP_Z0`, so today it frees nothing); Pi has 6.6 GB spare. Costs: Postgres on a microSD (wear), Pi CPU already ~1 core busy and 66 C, panel becomes a memory SPOF; engine still calls the Orin brain for extraction | **SKIP as primary**; fallback if no second box is bought (Postgres on a USB SSD first) |
| **Move Kokoro to the Pi** (`zreecespieces/kokoro-pi`, MIT, fused int8 NEON) | frees ~2.1 GB on the Orin but Pi 5 gives RTF 0.51-0.65 and first audio **0.72-0.93 s** vs the Orin's ~0.3 s; int8 log-spectral distance 2.17 dB vs fp32; ~900 MB resident on the Pi (https://github.com/zreecespieces/kokoro-pi; 22 commits, 0 stars) | **SKIP**: costs ~0.4-0.6 s of TTFA, the core metric; also changes the rock's numerics |
| **Pi AI HAT+ 2** (Hailo-10H, 8 GB, $130-200) | 1.5B models at ~6-11 tok/s (https://www.jeffgeerling.com/blog/2026/raspberry-pi-ai-hat-2/ , Tom's Hardware review) | **SKIP** (no rock fits its model list) |
| **A second Orin / N100 / Pi 5 16 GB as the 2nd box** | N150 16 GB mini PC ~$350-380 (ameriDroid/eBay); second Orin NX 16 = $999 module + carrier; Pi 5 16 GB ~$220 | **SKIP** vs the Mac mini: N100/Pi have no GPU for Kokoro at 0.3 s; a second Orin repeats the same RAM ceiling at today's price |

### (3) Two-box topologies seen in the field

- Home Assistant's design is already the split Zoe has: Wyoming (TCP) lets STT / TTS / wake / LLM each live on a different machine, with the satellite doing wake on-device
  (https://github.com/rhasspy/wyoming-satellite; a warm GPU pipeline of ~0.33 s compute, Whisper-small 99 ms + 4B brain 144 ms + Piper 93 ms,
  https://botmonster.com/smart-home/build-private-local-ai-voice-assistant-2026/ [unverified, blog]). Zoe's panel HTTP lane is the same shape; nothing to adopt except
  letting `zoe-data` reach each organ by URL (already true for Kokoro and llama-server).
- On-box Jetson Orin Nano voice stacks (Gemma 4 E2B audio-native + Pocket TTS, 0.37-0.56 s TTFT, 1.5 s to first audio on GPU; https://github.com/dwain-barnes/jetson-voice-assistant)
  and `ShayneP/local-voice-ai` (LiveKit + llama.cpp + Kokoro + Nemotron STT, hardware-profile auto-selection, MIT; https://github.com/ShayneP/local-voice-ai) are **behind Zoe** (our brain TTFT is ~386 ms
  on a bigger model). **BORROW one idea** from the second: named hardware profiles with a per-unit RAM budget (a `profiles/orin-nx-16.env` the W3 gate reads).
- The only genuinely new topology is **brain-on-Orin, speech + memory + zoe-data on a Mac mini** (ranked #2).

### (4) Hardware upgrades

| Option | Price (2026-10) | RAM / bandwidth | Fixed rocks run? | Verdict |
|---|---|---|---|---|
| Orin NX 16 (today) | $999 module (was $599) | 16 GB / 102 GB/s | yes (live) | keep |
| AGX Orin 64 GB | module $2,999 (+carrier); kit price not found | 64 GB / ~205 GB/s [spec from memory, not re-fetched] | yes, same CUDA 12.6 stack, no port | **SKIP**: $3k for a same-generation GPU; a Mac mini M5 Pro 64 GB is ~$2.7k with 307 GB/s |
| Jetson Thor T4000 module | $2,999 | 64 GB / 273 GB/s, Blackwell | needs JP 7, SM110 rebuilds (torch/kokoro/llama.cpp); Gemma 4 on TRT Edge-LLM is hitting bugs there (#230) | **SKIP** |
| Thor dev kit | $5,499 | 128 GB / 273 GB/s | same as above | **SKIP** |
| DGX Spark | $4,699 | 128 GB / 273 GB/s | CUDA/aarch64 so Kokoro-torch is plausible; Gemma 4 26B-A4B vLLM FP8 + MTP: 40.9 -> 108.8 tok/s single stream (https://ai-muninn.com/en/blog/dgx-spark-gemma4-mtp-108-toks) | **SKIP** at this price for a household (but the only box where the MTP gain is *proven* for the Gemma-4 family at 26B) |
| **Mac mini M6 32 GB** | from $899 (16 GB); 32 GB config sold, price [unverified] | 32 GB / 170 GB/s | Moonshine: official macOS support; v2 Medium 258 ms on an M3 (arXiv 2602.12241, per doc 10-03). Kokoro: CoreML build 25x real-time on an M4 mini (https://github.com/Jon-Schneider/kokoro-coreml-ane; only `af_heart` shipped) or MLX build with 54 voices incl. `af_sky` (https://github.com/gabrimatic/kokoro-mlx, no benchmarks published). Gemma 4 E4B: llama.cpp Metal Q8_0 39 tok/s on an M4 Pro, **no MTP** | **ADOPT-TRIAL** (ranked #2) |
| **Mac mini M5 Pro 48/64 GB** | ~$2,299 / ~$2,699 [derived] | 307 GB/s | Gemma 4 E4B 92 tok/s MLX Q4 (aggregator, [unverified], https://siliconscore.com/models/gemma-4-e4b/); 26B-A4B Q4_K_M on an M4 Pro 48 GB: 15-20 tok/s via llama.cpp, 28 tok/s MLX on M4 Pro 24 GB (DEV Community + SudoAll, secondary) | the "bigger brain" option; **decide after #2's measured trial** |

Critical Mac caveat: **llama.cpp's MTP is a net loss on Metal** (M1 Max: baseline 25.3 tok/s, every speculative setting -11 to -24 %, kernel-dispatch overhead),
while MLX tools reach 1.6-2.6x (MTPLX: Qwen 3.6 27B 7 -> 18.3 tok/s on M4 Pro) and LiteRT-LM Metal does E4B 44.6 -> 82.8 tok/s on an M4 Pro
(https://modelfit.io/blog/speculative-decoding-mac-llm/, https://github.com/iprajax/gemma4-mtp). So on a Mac the brain would be a *different runtime*
than the rock's llama.cpp MTP lane and the replay gate would have to be re-recorded. Also, the M6/M5 Pro token rates on third-party pages are projections ("pending retail units",
https://nordicsilicon.io/blog/mac-mini-m6-m5-pro-llm-benchmarks), so every Mac number here is a bound, not a measurement.

What a Mac would mean (all [derived] from the doc's RAM table): moving Kokoro 2.1 + zoe-data/Moonshine 1.4 = **~3.5 GB freed** on the Orin
(MemAvailable ~1.7 -> ~5.2 GB; corrected 2026-10-09: the 0.46 GB Hindsight stack is NOT counted as freed, because Hindsight is not adopted - bake-off run 2 verdict `KEEP_Z0`, so it is not resident on the Orin today and its 0.46 GB is a hypothetical post-adoption term, not RAM that moving it would release), leaving the Orin as brain + router only; the Mac then hosts Hindsight + MemPalace + reflection resident with 10+ GB to spare. A bigger brain
(Gemma 4 12B, or the 26B-A4B MoE, same family) would fit a 32 GB Mac at Q4 (~16 GB for 26B-A4B) but is a *rock change* and needs the owner's explicit OK.

### (5) "Brain in a box" setups to adopt

None is better than Zoe's. What is worth borrowing:
- NVIDIA's `jetson-device-skills` repo as a read-only memory audit (`jetson-memory-audit`) - safe, free (https://github.com/NVIDIA-AI-IOT/jetson-device-skills).
- **Jetson A/B rootfs + UEFI auto-rollback** as a safety net for the JetPack 7.2 trial: it needs rootfs redundancy provisioned at flash time, R36.4/36.5 tightened rollback counters (firmware only moves forward), and custom carriers have caveats (https://proventusnova.com/blog/jetson-ab-ota-custom-carrier-board/). Simpler: **flash 7.2 onto a separate NVMe/USB-NVMe and swap drives to roll back** [unverified that the J401's QSPI firmware stays R36-compatible after a 7.2 flash; test the drive swap before relying on it].
- `ShayneP/local-voice-ai` hardware-profile idea (above). Telemetry: MIT, env-file config; I did not audit it for telemetry.

## 3. RANKED TOP 5

1. **JetPack 7.2 trial on a separate NVMe, brain-stop window (ADOPT-TRIAL, ~$0-100).** Expected: Seeed's -40 % post-load memory and +28-42 % speed, if the double-held weights go away. Gate: same GGUF, same flags, `RssAnon`/`RssFile`/MemAvailable and the replay corpus; kill criterion = Kokoro torch / CUDA 13 wheel pain or no RAM gain. Also unlocks TRT Edge-LLM and vLLM >= 0.22.
2. **Mac mini M6 32 GB as the speech + memory + zoe-data box, Orin keeps brain + router (ADOPT-TRIAL; ~$1.2-1.4k [unverified]; the $899 16 GB would also fit speech + memory).** Free Step 0: run Moonshine + kokoro-mlx (`af_sky`) + the Hindsight stack on the *existing* Mac for 2 days over the VPN, replay the corpus remotely, prove TTFA and the 4 GB claim. Unlocks the "all of Hindsight + MemPalace + reflection resident" ceiling and probably lowers TTFA (CoreML Kokoro: 28 s of audio in ~1.1 s).
3. **Overnight digest on vLLM or llama.cpp `--parallel` in a nightly brain-stop window (BORROW).** Only after measuring the digest's current wall time.
4. **TensorRT Edge-LLM re-scout (BORROW/watch).** Revisit when #230 is fixed and an Orin NX Gemma 4 E4B + MTP number exists; needs #1 first.
5. **`jetson-memory-audit` + named hardware-profile RAM budgets (BORROW, free).** Turns the W3 gate into a per-unit budget instead of one 2 GB floor.

Not recommended: AGX Orin 64, Thor, DGX Spark (price), Kokoro-on-Pi (TTFA), Pi AI HAT+ 2, carveout BSP rebuild, memory engine on the Pi SD card.

## 4. The one hardware recommendation

**Buy one always-on Mac mini (M6, 32 GB) as Zoe's second box - after the free Step-0 trial on the existing Mac passes - and keep the Orin as brain-only.** It is the only option that adds RAM (~3.5 GB freed on the Orin, not counting the hypothetical 0.46 GB Hindsight term; 10+ GB spare on the Mac), keeps all three rocks as the same models (Gemma 4 E4B + MTP, Moonshine v2 Medium, Kokoro) but **changes Kokoro's runtime**: `kokoro-mlx` on Apple silicon replaces the canonical PyTorch / CUDA sidecar that `docs/CANONICAL.md` lists as load-bearing, so adopting it is a deliberate, owner-approved edit to that row (CANONICAL plus its lock-in test), not a free move, costs about what the Orin module alone costs now, and leaves the door open to an M5 Pro 64 GB later if the owner ever approves a 12B / 26B-A4B brain.

## 5. Open items I could not close (honest limits)

- No published Gemma 4 E4B + MTP number on Orin NX other than the 13 -> 18 tok/s forum fork; no TRT Edge-LLM Gemma 4 Orin NX figure at all.
- Seeed's 7.2 memory result is AGX Orin 32 GB + a 27B model; the mechanism is inferred; I did not read the Seeed flashing wiki (the Seeed blog returned 403).
- Mac mini M6 32 GB exact price; M6/M5 Pro token rates are projections on most pages; the Metal-MTP loss rests on one M1 Max report plus the iprajax repo; whether `kokoro-mlx` matches PyTorch output for `af_sky` was not tested.
- Whether the owner's existing Mac is Apple silicon (docs say "Mac on the VPN"/laptop, spec unknown), and whether the Pi has a non-SD disk option.
