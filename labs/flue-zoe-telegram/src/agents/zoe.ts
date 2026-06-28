/**
 * The Zoe agent — runs on Zoe's OWN local Gemma brain (registered as the `zoe`
 * provider in app.ts, pointing at llama-server :11434), NOT a cloud model.
 *
 * This is increment 1 of "Zoe on Flue" (option B): a conversational Zoe over
 * Telegram with durable per-chat memory. It does NOT yet have Zoe's domain
 * capabilities (lists, calendar, timers, long-term memory) — those get wired in
 * as Flue tools / via Zoe's MCP bus in later increments. Conversation only, for now.
 *
 * LAB ONLY.
 */
import { defineAgent } from '@flue/runtime';
import { chatIdFromKey, postMessage } from '../telegram.ts';

const PERSONA = [
  'You are Zoe, a warm, concise personal assistant talking to your owner over',
  'Telegram text. Keep replies short, natural, and useful — text-message length,',
  'not essays. Be direct; skip filler.',
  '',
  'You ALWAYS reply by calling the `post_telegram_message` tool with your answer.',
  'Do not output a reply any other way — if you do not call the tool, the user',
  'sees nothing.',
  '',
  'You do not yet have access to live tools (lists, calendar, timers, memory).',
  'If asked to do something that needs them, say briefly that that ability is not',
  'wired up yet — do not pretend you did it.',
].join('\n');

export default defineAgent(({ id }) => ({
  // llama-server ignores the model name (serves the loaded gguf); any string works.
  model: process.env.ZOE_MODEL ?? 'zoe/local',
  instructions: PERSONA,
  tools: [postMessage(chatIdFromKey(id))],
}));
