/**
 * Brick 4 — the capability contract.
 *
 * Each domain tool lives in its own `abilities/<domain>.ts` file and default-
 * exports a `CapabilityEntry[]`. `extensions/abilities.ts` auto-discovers them,
 * registers them with Pi, enforces the permission envelope, and does
 * progressive disclosure (only surfaces relevant tools per turn). Adding a
 * domain = adding ONE file; no manifest or shared-file edits.
 */
import type { TSchema } from "@sinclair/typebox";

/** Permission envelope (validated before every tool execution). */
export type Permission =
  | "read-only"
  | "user-data:read"
  | "user-data:write"
  | "fs:write"
  | "network"
  | "browser"
  | "home-device:action"
  | "code:mutate"
  | "credential:access";

/** Ambient context handed to every ability's execute(). */
export interface AbilityContext {
  /** Base URL of zoe-data (the capability backend). */
  zoeDataUrl: string;
  /** Internal service token for zoe-data calls (X-Internal-Token). */
  internalToken: string;
  /** The Zoe user whose data this turn operates on. */
  userId: string;
}

export interface CapabilityEntry {
  /** Stable id, e.g. "calendar.create_event". */
  id: string;
  /** Tool name the model calls (snake_case, one per domain), e.g. "calendar". */
  name: string;
  /** Routing domain, e.g. "calendar". */
  domain: string;
  /** One line — WHAT it does and WHEN to use it (the model's routing signal). */
  description: string;
  /** typebox schema for the tool's parameters (validated before execute). */
  parameters: TSchema;
  /** Phrasings that SHOULD trigger this tool (few-shot + relevance gating). */
  examples: string[];
  /** Phrasings that should NOT trigger it (reduces false fires on a small model). */
  negativeExamples?: string[];
  /** Permission scopes this tool needs (enforced before execute). */
  permissions: Permission[];
  /** "core" = always in the prompt; "on-demand" = surfaced only when relevant. */
  tier: "core" | "on-demand";
  /** Keyword triggers for lightweight relevance gating (pre-embedding Tool-RAG). */
  triggers?: RegExp[];
  /** Runtime eligibility (peer health, device presence, time-of-day). */
  gate?: (ctx: AbilityContext) => boolean;
  /** Do the thing. Return a short natural-language result for the model. */
  execute: (params: Record<string, unknown>, ctx: AbilityContext) => Promise<string>;
}

/** A domain file default-exports CapabilityEntry[] (or exports `abilities`). */
export type AbilityModule =
  | { default: CapabilityEntry[] }
  | { abilities: CapabilityEntry[] };
