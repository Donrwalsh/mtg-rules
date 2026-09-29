export function shortDate(iso: string, locale?: string): string {
  return new Date(iso).toLocaleDateString(locale, { month: 'short', day: 'numeric' });
}

// Scryfall and rules dates are calendar days ("2023-06-16"). Format them in
// UTC so a western time zone doesn't show the day before.
export function calendarDate(date: string, locale?: string): string {
  return new Date(`${date}T00:00:00Z`).toLocaleDateString(locale, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC'
  });
}

// Quotas and the budget reset at UTC midnight; show it in local time.
export function resetTime(now = new Date(), locale?: string, timeZone?: string): string {
  const next = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1));
  return next.toLocaleTimeString(locale, { hour: 'numeric', minute: '2-digit', timeZone });
}
