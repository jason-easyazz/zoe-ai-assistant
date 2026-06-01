# Agent Spike Playbook

Use this for Forge, Caveman, Babysitter, and local-model/Spark simulation work.
These are evaluation tracks, not production dependencies.

## Guardrails

- Native Zoe evidence gates come first. Spikes should prove whether an outside
  pattern helps Zoe; they should not replace Zoe's orchestrator.
- Tools before tokens: every packet must record Graphify/opensrc/Greptile/
  validators used before any paid model escalation.
- Overnight mode can be slow. Prefer free/local reliability over latency for
  self-evolution tasks.
- Do not install Forge, Caveman, or Babysitter into production services from a
  spike PR. Keep caches outside the repo and report the exact command/version.

## Candidates

- Forge: evaluate local-model tool-call reliability, retries, and rescue parsing.
- Caveman: evaluate token compression only; it must not become an authority for
  requirements or code review.
- Babysitter: borrow process-as-code, event journal, breakpoint, and resume
  patterns; do not make it Zoe's runtime orchestrator.
- Local model: simulate Spark-fit overnight work and compare pass rate, latency,
  and evidence quality against OpenRouter routes.

## Metrics Packet

Generate a packet before each spike:

```bash
python3 scripts/maintenance/agent_spike_metrics.py forge \
  --task "verify local model tool-call rescue on a small Zoe PR" \
  --output /tmp/zoe-agent-spikes/forge-spike.json
```

The packet records availability, cost policy, required evidence, and suggested
commands. Attach the resulting JSON to the PR or Multica issue summary.

## Pass Criteria

- The spike has a real Zoe task or replay, not a toy prompt.
- The packet includes pass/fail, commands run, cost/locality observation, gotchas,
  and a recommendation.
- If the candidate increases cost, complexity, or dependency gravity without a
  measurable reliability gain, close it as "do not adopt."

