/** Prompts a schedule can start from. */
export const SCHEDULE_TEMPLATES: { name: string; label: string; prompt: string; cron: string }[] = [
  {
    name: "Backlog groom",
    label: "Backlog groom",
    cron: "0 2 * * *",
    prompt: `Groom this repository's open GitHub issues.

Follow the repository's own grooming rules: the groom-backlog skill in .claude/skills/groom-backlog/SKILL.md if there is one, and the label vocabulary in README's Commit conventions section. Where the skill runs \`gh\` or its scripts, use the github tools instead (list_issues, get_issue, list_labels, update_issue, comment_issue, close_issue, github_get); there is no gh CLI or token here.

For each open issue that needs it:
- Flesh out thin bodies into the house shape (Why / Proposed changes / Out of scope), with evidence read from the code: cite files and lines, and say what is there today.
- Apply the standard labels: exactly one \`type:\` and at least one \`area:\` label from README, plus \`security\` where it applies. Replace GitHub's stock \`bug\` and \`enhancement\`.
- Flag duplicates: close the newer issue as a duplicate of the older one, with a comment saying why.
- Flag stale issues: if the code shows it's already done, close it as completed with the evidence. If it may no longer be wanted, comment asking rather than closing.
- Put questions the code can't answer in one comment on the issue.

Leave issues that are already in good shape alone. Finish with a short report: for each issue you changed, what you changed and why, plus anything you couldn't do.`,
  },
];

/** Cron presets for the schedule form, read in the schedule's time zone. */
export const CRON_PRESETS: { label: string; cron: string }[] = [
  { label: "Every night at 02:00", cron: "0 2 * * *" },
  { label: "Weekdays at 09:00", cron: "0 9 * * 1-5" },
  { label: "Mondays at 08:00", cron: "0 8 * * 1" },
  { label: "Every hour", cron: "0 * * * *" },
];
