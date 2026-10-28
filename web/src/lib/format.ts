// KPI formatters (Requirement 18.10, design "Formatting and time").
// A fixed en-US locale keeps output independent of the presenter's browser.
// Each formatter rounds first, then formats the absolute value and prepends
// the sign, so a value that rounds to zero (e.g. -0.04) never shows "-0".

const intFormat = new Intl.NumberFormat('en-US', {
  maximumFractionDigits: 0,
  useGrouping: true,
});

/** Thousands-separated digits of a non-negative integer. */
function groupInt(abs: number): string {
  return intFormat.format(abs);
}

/** Sign prefix for an already rounded value; -0 and 0 get none. */
function sign(rounded: number): string {
  return rounded < 0 ? '-' : '';
}

const NOT_A_NUMBER = '–';

/** "1,234 kW" / "-1,234 kW" (Math.round). */
export function formatKw(n: number): string {
  if (!Number.isFinite(n)) return NOT_A_NUMBER;
  const r = Math.round(n);
  return `${sign(r)}${groupInt(Math.abs(r))} kW`;
}

/** "$12,345" / "-$12,345" (whole dollars, Math.round). */
export function formatUsd(n: number): string {
  if (!Number.isFinite(n)) return NOT_A_NUMBER;
  const r = Math.round(n);
  return `${sign(r)}$${groupInt(Math.abs(r))}`;
}

/** "$1,234.56" / "-$0.40" (cents, rounded half away from zero on the cent count). */
export function formatUsdCents(n: number): string {
  if (!Number.isFinite(n)) return NOT_A_NUMBER;
  const cents = Math.round(n * 100);
  const abs = Math.abs(cents);
  return `${sign(cents)}$${groupInt(Math.floor(abs / 100))}.${String(abs % 100).padStart(2, '0')}`;
}

/** "12.3 t" / "-0.4 t" (one decimal). */
export function formatTonnes(n: number): string {
  if (!Number.isFinite(n)) return NOT_A_NUMBER;
  // Round to integer tenths, then split, so the printed digits are exact.
  const tenths = Math.round(n * 10);
  const abs = Math.abs(tenths);
  return `${sign(tenths)}${groupInt(Math.floor(abs / 10))}.${abs % 10} t`;
}

/** "97%" (Math.floor) / "100%". */
export function formatPct(n: number): string {
  if (!Number.isFinite(n)) return NOT_A_NUMBER;
  const r = Math.floor(n);
  return `${sign(r)}${groupInt(Math.abs(r))}%`;
}
