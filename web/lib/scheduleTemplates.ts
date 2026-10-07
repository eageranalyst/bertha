/** Prompts a schedule can start from. */
export const SCHEDULE_TEMPLATES: { name: string; label: string; prompt: string; cron: string }[] = [
  {
    name: "Backlog groom",
    label: "Backlog groom",
    cron: "0 2 * * *",
    prompt: `Groom this repository's open GitHub issues.

Follow the repository's own grooming rules: the groom-backlog skill in .claude/skills/groom-backlog/SKILL.md if there is one, and the label vocabulary in README's Commit conventions section. Where the skill runs \`gh\` or its scripts, use the github tools instead (list_issues, get_issue, list_labels, update_issue, comment_issue, close_issue, github_get); there is no gh CLI or token here.

Do only the work each issue needs. Most issues are already groomed; rewriting a good issue adds nothing and churns it.

1. Read the grooming skill and README's label vocabulary, then call list_issues and list_labels. list_issues gives each issue a suggested triage; use it:
   - unchanged_since_groom: nothing has happened since the last groom. Skip it entirely.
   - well_formed: has the house shape. Check it yourself, without a worker and without get_issue unless needed: fix its labels if they're wrong; if its last comment is a human's reply to a question the bot asked, it needs the answer folded in (treat it as thin); if it has citations, it needs a citation check (step 2).
   - thin: needs fleshing out (step 2).
2. Give each issue that needs real work (thin, an answer to fold in, or a citation check) to its own worker subagent with the task tool. Pass: the issue number, which job it is, the rules below with the exact label vocabulary, and "report in three lines: what the issue is about in one sentence, what you changed, anything you couldn't do".
3. Look across all the issues' titles and the workers' reports for duplicates (a worker only sees its own issue): close the newer as a duplicate of the older, with a comment saying why.

Rules for workers:
- Thin issue: flesh out the body into the house shape (Why / Proposed changes / Out of scope) with evidence read from the code: cite files and lines, and say what is there today. Ask what the code can't answer in one comment.
- Answer to fold in: update the body with the decision (the skill's "fold decisions back in" step). Don't re-ask anything that's been answered.
- Citation check: read only the cited files and symbols, not the wider code. Fix a citation that has drifted. If the code shows the work is already done, close the issue as completed with the evidence. Otherwise change nothing.
- Never rewrite a body that's already in good shape just to restate it. Change only what's wrong or missing.
- Never repeat a question already asked in an earlier comment. Comment only with a new question, or not at all.
- Labels: exactly one \`type:\` and at least one \`area:\` label from README, plus \`security\` where it applies. Replace GitHub's stock \`bug\` and \`enhancement\`.

Finish with a short report: how many issues you skipped, checked and worked on; for each issue you changed, what changed and why; the duplicates you closed; anything you couldn't do.`,
  },
];

/** Cron presets for the schedule form, read in the schedule's time zone. */
export const CRON_PRESETS: { label: string; cron: string }[] = [
  { label: "Every night at 02:00", cron: "0 2 * * *" },
  { label: "Weekdays at 09:00", cron: "0 9 * * 1-5" },
  { label: "Mondays at 08:00", cron: "0 8 * * 1" },
  { label: "Every hour", cron: "0 * * * *" },
];
