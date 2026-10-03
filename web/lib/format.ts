/** Unix seconds to a local date-time string, or a dash when there isn't one. */
export function fmtTime(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}
