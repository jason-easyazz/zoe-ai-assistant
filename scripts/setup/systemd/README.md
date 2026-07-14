# Host-native systemd units

Zoe runs as a split stack: the database, auth, UI and Home Assistant run in
Docker (`docker-compose.yml`), while the latency-sensitive services run as
**user** systemd units directly on the host.

These are **templates**. They use `%h` (your home directory) so they work
without editing on most setups, but paths marked platform-specific
(llama-server binary, GGUF model, CUDA libs) must be adjusted for your machine.
Secrets are never inlined — they are read from `.env` files.

| Unit | Port | Purpose |
|------|------|---------|
| `llama-server.service`     | 11434 | Local LLM (Gemma 4 E4B-QAT+MTP via llama.cpp) — **platform-specific paths** |
| `kokoro-tts.service`       | 10201 | Local neural TTS sidecar |
| `zoe-data.service`         | 8000  | Primary backend API |
| `functiongemma-router.service` | 11436 | Two-stage router stage-2 decoder (FunctionGemma-270M r2, CPU) — **platform-specific paths**; optional |
| `flue-zoe-brain.service`   | 3578  | Flue Zoe-brain sidecar (optional, operator opt-in) |

## Install

```bash
mkdir -p ~/.config/systemd/user
cp scripts/setup/systemd/*.service ~/.config/systemd/user/

# Edit llama-server.service for your binary + model path first:
#   ${EDITOR:-nano} ~/.config/systemd/user/llama-server.service

systemctl --user daemon-reload
systemctl --user enable --now llama-server zoe-data kokoro-tts
```

`flue-zoe-brain.service` is deliberately NOT in that enable line: it supervises
the sidecar behind zoe-data's default-OFF `ZOE_BRAIN_BACKEND=flue` seam.
Enable it only when running the Flue brain — build + env steps are in
[labs/flue-zoe-brain/README.md](../../../labs/flue-zoe-brain/README.md).

Start order matters — see [OPERATOR_RUNBOOK.md](../../../docs/guides/OPERATOR_RUNBOOK.md).

## Verify

```bash
systemctl --user status zoe-data
curl -f http://localhost:8000/health
journalctl --user -u zoe-data -f   # tail logs
```
