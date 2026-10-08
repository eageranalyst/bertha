/** Cron presets for the schedule form, read in the schedule's time zone. */
export const CRON_PRESETS: { label: string; cron: string }[] = [
  { label: "Every night at 02:00", cron: "0 2 * * *" },
  { label: "Weekdays at 09:00", cron: "0 9 * * 1-5" },
  { label: "Mondays at 08:00", cron: "0 8 * * 1" },
  { label: "Every hour", cron: "0 * * * *" },
];
