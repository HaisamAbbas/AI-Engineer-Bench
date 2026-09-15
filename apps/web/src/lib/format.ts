/** Shared display formatting so every page renders dates/numbers/percentages
 * consistently (spec section 4/35): UTC with a local-time tooltip, full
 * units, percentages kept visually distinct from percentage-point
 * differences, and nulls rendered as an explained "Unknown"/"Not
 * applicable" rather than a blank cell or a fabricated zero. */

export function formatUtc(iso: string): { display: string; localTitle: string } {
  const date = new Date(iso);
  const display = date.toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC");
  const localTitle = date.toLocaleString(undefined, { timeZoneName: "short" });
  return { display, localTitle };
}

export function formatRate(value: number | null | undefined, unavailableReason = "Unknown"): string {
  if (value === null || value === undefined) return unavailableReason;
  return `${(value * 100).toFixed(1)}%`;
}

export function formatPercentagePointDifference(value: number | null | undefined): string {
  if (value === null || value === undefined) return "Unknown";
  const points = value * 100;
  const sign = points > 0 ? "+" : "";
  return `${sign}${points.toFixed(1)} pp`;
}

export function formatCount(n: number | null | undefined): string {
  if (n === null || n === undefined) return "Unknown";
  return n.toLocaleString();
}

export function formatUsd(value: number | string | null | undefined): string {
  if (value === null || value === undefined) return "Unavailable";
  const n = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(n)) return "Unavailable";
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function formatSeconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return "Unknown";
  if (value < 60) return `${value}s`;
  const minutes = Math.floor(value / 60);
  const seconds = value % 60;
  return `${minutes}m ${seconds}s`;
}
