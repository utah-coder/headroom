// America/Denver time labels (Requirements 7.11, 19.1, 21.2).
// Mirrors sim/timeutil.hhmm_denver: 24-hour "HH:MM", midnight is "00:MM".

const denverHhmm = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'America/Denver',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

/** 24-hour "HH:MM" of an instant (UTC ISO string or Date) in America/Denver. */
export function hhmmDenver(t: string | Date): string {
  const d = typeof t === 'string' ? new Date(t) : t;
  let hour = '';
  let minute = '';
  for (const part of denverHhmm.formatToParts(d)) {
    if (part.type === 'hour') hour = part.value;
    else if (part.type === 'minute') minute = part.value;
  }
  // Guard against engines that render midnight as "24" despite h23.
  if (hour === '24') hour = '00';
  return `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`;
}

const denverWeekday = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Denver', weekday: 'long' });

/** Weekday name ("Tuesday") of an instant in America/Denver. */
export function weekdayDenver(t: string | Date): string {
  return denverWeekday.format(typeof t === 'string' ? new Date(t) : t);
}
