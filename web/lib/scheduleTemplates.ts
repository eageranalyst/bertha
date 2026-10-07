/** Prompts a schedule can start from. */
export const SCHEDULE_TEMPLATES: { name: string; label: string; prompt: string; cron: string }[] = [
  {
    name: "Backlog groom",
    label: "Backlog groom",
    cron: "0 2 * * *",
    prompt: `Groom this repository's open GitHub issues.

Follow the repository's own grooming rules: the groom-backlog skill in .claude/skills/groom-backlog/SKILL.md if there is one, and the label vocabulary in README's Commit conventions section. Where the skill runs \`gh\` or its scripts, use the github tools instead (list_issues, get_issue, list_labels, update_issue, comment_issue, close_issue, github_get); there is no gh CLI or token here.

Work issue by issue, each in its own worker subagent, so your own context stays small:
1. Read the grooming skill and README's label vocabulary, and call list_issues and list_labels. Don't call get_issue yourself.
2. For each open issue, use the task tool to start the worker subagent with: the issue number, the rules below (including the exact label vocabulary), and "report in three lines: what the issue is about in one sentence, what you changed, and anything you couldn't do".
3. Collect the workers' reports, then look across them for duplicates (a worker only sees its own issue): for any pair, close the newer issue as a duplicate of the older, with a comment saying why.

The rules for each issue (give these to every worker):
- Leave the issue alone if it's already in good shape.
- Flesh out a thin body into the house shape (Why / Proposed changes / Out of scope), with evidence read from the code: cite files and lines, and say what is there today.
- Apply the standard labels: exactly one \`type:\` and at least one \`area:\` label from README, plus \`security\` where it applies. Replace GitHub's stock \`bug\` and \`enhancement\`.
- Stale: if the code shows it's already done, close it as completed with the evidence. If it may no longer be wanted, comment asking rather than closing.
- Put questions the code can't answer in one comment on the issue.

Finish with a short report: for each issue you changed, what changed and why, the duplicates you closed, and anything you couldn't do.`,
  },
];

/** Cron presets for the schedule form, read in the schedule's time zone. */
export const CRON_PRESETS: { label: string; cron: string }[] = [
  { label: "Every night at 02:00", cron: "0 2 * * *" },
  { label: "Weekdays at 09:00", cron: "0 9 * * 1-5" },
  { label: "Mondays at 08:00", cron: "0 8 * * 1" },
  { label: "Every hour", cron: "0 * * * *" },
];
