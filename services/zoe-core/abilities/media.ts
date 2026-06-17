/**
 * media (music) + home (smart-home).
 * Intents (verified in intent_router):
 *   media: music_play {query} | music_control {command} | music_volume {level} | music_setup {} | set_volume {direction,level?}
 *   home:  smart_home {action: turn_on|turn_off|dim|brighten, entity:"light", room?}
 * Note: smart_home is lights-only server-side; set_volume = system/TTS volume, not the music player.
 */
import { Type } from "typebox";
import type { AbilityContext, CapabilityEntry } from "./types";
import { dispatchIntent } from "./_dispatch";

function clampLevel(v: unknown): number {
  const n = Math.round(Number(v));
  return Number.isFinite(n) ? Math.max(0, Math.min(100, n)) : 50;
}

const media: CapabilityEntry = {
  id: "media",
  name: "media",
  domain: "media",
  description:
    "Control music/audio playback on the home media players. action=play (start a track/artist/genre/" +
    "playlist), control (pause/resume/stop/next/previous/shuffle/mute/unmute/now_playing), " +
    "set_music_volume (0-100 on the player), system_volume (Zoe's OWN speaking/TTS volume, not the player), " +
    "setup (connect Spotify/etc.). NOT for smart-home lights.",
  parameters: Type.Object({
    action: Type.String({ description: "one of: play | control | set_music_volume | system_volume | setup" }),
    query: Type.Optional(Type.String({ description: "for play: track/artist/genre/playlist" })),
    command: Type.Optional(Type.String({ description: "for control: pause|stop|resume|next|previous|shuffle|mute|unmute|now_playing" })),
    level: Type.Optional(Type.Integer({ minimum: 0, maximum: 100, description: "volume 0-100 (set_music_volume; or system_volume when setting absolute)" })),
    direction: Type.Optional(Type.String({ description: "system_volume only: set | up | down" })),
  }),
  examples: ["play some jazz", "put on Taylor Swift", "pause the music", "skip this song", "what's playing right now", "set the music volume to 30", "connect Spotify"],
  negativeExamples: ["turn off the living room lights", "dim the bedroom lights", "what's on my shopping list"],
  permissions: ["home-device:action", "user-data:write"],
  tier: "on-demand",
  triggers: [
    /\b(play|put on)\b/i,
    /\b(pause|resume|unpause|stop|skip|next|previous|shuffle|mute|unmute)\b/i,
    /\bwhat(?:'s| is)(?: currently)? playing\b/i,
    /\b(set|turn)\b.*\bvolume\b/i, /\b(louder|quieter|turn it (up|down))\b/i,
    /\b(set ?up|configure|connect)\b.*\b(music|spotify|youtube music|apple music|streaming)\b/i,
  ],
  async execute(params, ctx: AbilityContext): Promise<string> {
    const action = String(params.action ?? "").toLowerCase();
    switch (action) {
      case "play": {
        const query = String(params.query ?? "").trim();
        return dispatchIntent(ctx, "music_play", { query: query || "music" });
      }
      case "control": {
        const command = String(params.command ?? "").trim();
        if (!command) return "What would you like — pause, resume, skip, or what's playing?";
        return dispatchIntent(ctx, "music_control", { command });
      }
      case "set_music_volume": {
        if (params.level == null) return "What volume would you like (0-100)?";
        return dispatchIntent(ctx, "music_volume", { level: clampLevel(params.level) });
      }
      case "system_volume": {
        const direction = String(params.direction ?? "up").toLowerCase();
        if (direction === "set") {
          if (params.level == null) return "What level should I set my speaking volume to (0-100)?";
          return dispatchIntent(ctx, "set_volume", { direction: "set", level: clampLevel(params.level) });
        }
        return dispatchIntent(ctx, "set_volume", { direction: direction === "down" ? "down" : "up", level: null });
      }
      case "setup":
        return dispatchIntent(ctx, "music_setup", {});
      default:
        return `Unknown media action: ${action}`;
    }
  },
};

const home: CapabilityEntry = {
  id: "home",
  name: "home",
  domain: "home",
  description:
    "Control smart-home lights via Home Assistant. action=on|off|toggle|dim|brighten, optionally scoped to a " +
    "room (kitchen/bedroom/living room). NOTE: lights only today. NOT for music/media volume.",
  parameters: Type.Object({
    action: Type.String({ description: "one of: on | off | toggle | dim | brighten" }),
    room: Type.Optional(Type.String({ description: "optional room, e.g. 'kitchen', 'bedroom'; omit for all lights" })),
  }),
  examples: ["turn on the kitchen lights", "turn off the lights", "dim the bedroom lights", "brighten the living room"],
  negativeExamples: ["turn the music up", "set the volume to 40", "play some music"],
  permissions: ["home-device:action"],
  tier: "on-demand",
  triggers: [/\b(turn|switch|flip)\s+(on|off)\b.*\blights?\b/i, /\blights?\s+(on|off)\b/i, /\bdim\b.*\blights?\b/i, /\bbrighten\b.*\blights?\b/i],
  async execute(params, ctx: AbilityContext): Promise<string> {
    const raw = String(params.action ?? "").toLowerCase();
    const actionMap: Record<string, string> = { on: "turn_on", off: "turn_off", toggle: "turn_on", dim: "dim", brighten: "brighten" };
    const action = actionMap[raw];
    if (!action) return `Unknown home action: ${raw}`;
    const roomRaw = String(params.room ?? "").trim();
    const room = roomRaw ? roomRaw.toLowerCase().replace(/\s+/g, "_") : null;
    return dispatchIntent(ctx, "smart_home", { action, entity: "light", room });
  },
};

export default [media, home];
