# Security Policy for Skills

> **This document previously described controls that do not exist.** It listed
> six "mandatory rules" — `api_only: true` rejected at load time, an
> `allowed_endpoints` executor whitelist, internal-hosts-only blocking, and
> `skills.lock` SHA-256 integrity deactivation — as though they were enforced.
> **None of them are implemented anywhere in this repository.** There is no skill
> loader, no skill executor, and no `skills.lock` is written, read, or verified.
>
> Believing those controls were live would be actively unsafe: it would justify
> installing a skill on the assumption that Zoe sandboxes it. She does not.
> This document now states the real posture.

## What a skill actually is

A skill is a `SKILL.md` file whose **description** is parsed by
`services/zoe-data/skill_discovery.py` and advertised to the brain (via the
`list_openclaw_skills` tool) so it knows a capability exists.

Zoe **parses descriptions**. She does not load, sandbox, gate, or execute skills.
Discovery reads exactly two directories — `~/.openclaw/workspace/skills/` and
`~/.hermes/skills/` — and produces A2A `AgentSkill` dicts. Nothing else happens.

See [../architecture/EXTENSIBILITY.md](../architecture/EXTENSIBILITY.md) and
[../guides/CREATING_SKILLS.md](../guides/CREATING_SKILLS.md).

## Real threat model

Because there is no enforcement layer, the trust boundary sits **outside** Zoe:

1. **A skill is untrusted input to the model.** Its description text reaches the
   brain's context. A hostile description is a prompt-injection and
   capability-confusion vector — it can misrepresent what a capability does and
   induce the model to reach for it.

2. **Execution happens in the peer agent, at the peer's privileges.** When a
   skill's capability is actually invoked, OpenClaw or Hermes runs it, with
   whatever access that agent has. Zoe's process provides no confinement, so a
   skill is exactly as dangerous as the agent that owns it.

3. **Write access to `~/.openclaw/workspace/skills/` or `~/.hermes/skills/` is
   privileged.** Anything that can drop a file there can change what Zoe believes
   she can do. Treat those directories as sensitive.

## Actual controls

These exist and are the ones to rely on:

- **Scan before installing.** Root `AGENTS.md` → "Skill & extension safety":
  `skillspector scan <dir|file|git-url>` (at `~/.local/bin/skillspector`) before
  installing any third-party skill or extension, and before promoting a
  self-authored skill from the lab to a live agent. The static stage is
  deliberately conservative — pair it with human judgement, and record the
  outcome or a deliberate waiver.
- **Do not egress internal skill content** to an external LLM provider for
  scanning without operator consent; prefer static scans or a local provider.
- **Cache reload is admin-gated.** `POST /api/agent/peers/{name}/skills/reload`
  requires admin (`require_admin`).
- **Human review.** Installing a skill is a privileged operator action, not an
  automated one.

## If enforcement is wanted

The controls the old policy described are reasonable and could be built — but they
need a loader to hang off, which does not exist yet. That is the same
build-vs-remove decision tracked in
[../architecture/EXTENSIBILITY.md](../architecture/EXTENSIBILITY.md#open-decision-the-repo-skills-directory).
Until it is built, do not document it as though it is.

## Reporting issues

If you find a security issue with the skills system, report it by creating a
private issue or contacting the project maintainers.
