/**
 * Durable session storage. File-backed SQLite on the Jetson so Zoe's per-chat
 * Telegram conversation context survives a process restart. Single-host is the
 * right fit here (one box owns the state). See Flue's guide/database.
 *
 * LAB ONLY.
 */
import { sqlite } from '@flue/runtime/node';

export default sqlite('./data/zoe.db');
