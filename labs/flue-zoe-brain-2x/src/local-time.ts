/**
 * The household clock for `get_time`: ZOE_TIMEZONE (default Australia/Perth, the same default
 * as services/zoe-data `time_utils.zoe_timezone`), NOT the sidecar host's own locale/zone.
 * Read per call; an unknown zone falls back to the default rather than throwing mid-turn.
 * Dependency-free so the offline tests can import it without the Flue runtime.
 */
export const DEFAULT_ZOE_TIMEZONE = 'Australia/Perth';

export function zoeTimeZone(): string {
  const name = (process.env.ZOE_TIMEZONE ?? '').trim() || DEFAULT_ZOE_TIMEZONE;
  try {
    new Intl.DateTimeFormat('en-AU', { timeZone: name });
    return name;
  } catch {
    return DEFAULT_ZOE_TIMEZONE;
  }
}

/** "It's 7:05 am on Monday, 12 October 2026." in the household timezone. */
export function describeNow(now: Date = new Date(), locale?: string): string {
  const timeZone = zoeTimeZone();
  const time = now.toLocaleTimeString(locale ? [locale] : [], { hour: 'numeric', minute: '2-digit', timeZone });
  const date = now.toLocaleDateString(locale, {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    timeZone,
  });
  return `It's ${time} on ${date}.`;
}
