---
name: bertha-review
description: Review a pull request's change for real problems, independently of whoever wrote it. Trace what every changed and removed branch does now, on failure paths as well as success, confirm the task's own scenario is fixed, prove suspicions with throwaway tests in .qc/, and report findings in a fixed JSON block. Use for Bertha's code reviews.
---

# Code review

You are reviewing a pull request you didn't write. The change is checked out
in /data/repo on its branch. Your message tells you the pull request number,
its base branch and the task it was for. You can't push, comment or change
GitHub: Bertha posts your findings and has them fixed.

The bugs that matter hide in what the change stopped doing and in the paths
nobody tried: a failure branch that was deleted, a case that now falls
through, a lookup that no longer matches. Reading the new code and checking
it has tests is not enough. Work through the steps below in order.

## Steps

1. **See the change.** Run `git diff origin/<base>...HEAD --stat`, then read the
   diff of each file (`git diff origin/<base>...HEAD -- <path>`). Open each
   changed function in full, not just the hunk: the whole function is what runs.

2. **Know what it was for.** Read the task in your message. If it names an
   issue, read it with `get_issue`. Note the exact scenario it describes (the
   input, the URL, the steps): you'll check that scenario in step 5. Check the
   repo's rules: saved notes and the conventions page (`bertha` tools).

3. **Trace every changed function, hunk by hunk.** For each one, write down
   for yourself (not in the report) the cases it handles and what happens in
   each, before and after the change:
   - **Removed or replaced lines.** For every deleted or rewritten branch,
     condition, early return or error handler: which case did it handle, and
     what happens in that case now? A case that used to be handled and now
     falls through to other code is a finding.
   - **Failure paths.** For each call that can fail (network, file, database,
     parsing, a missing row): follow what happens when it does. Does the error
     get recorded, retried, or silently treated as success?
   - **Both sides of every new condition.** New and existing rows, empty and
     non-empty input, first run and re-run, the match and the near-miss.
   - **Matching and lookups.** When the change looks something up, does it use
     the same key and normalisation as the code around it? Different keys for
     the same thing (an exact URL here, a normalised one there) miss matches.
   - **State left behind.** After each path, is everything that should be
     updated updated (error fields cleared, derived data refreshed), and
     nothing stale or duplicated?
   - **Repetition and cost.** Does anything now run on every request or every
     run, retry forever, or call out to the network in a hot path?

4. **Prove what you suspect.** For anything you think is broken, show it:
   write a throwaway test in `/data/repo/.qc/` (git ignores it, and it can
   import the repo's code) that exercises the path, and run it, for example
   `python -m pytest .qc/test_failed_download.py`. Or quote the exact lines
   that show it, with the input that triggers it. A suspicion you couldn't
   prove either way still goes in the report, as `low`, saying what you
   couldn't confirm. Then run the repo's own checks for the changed code (its
   tests, type checker, linter) and read CI's results with `pr_status`. Note
   everything you ran.

5. **Check the task is done.** Walk the scenario from step 2 through the new
   code. Does it actually work for that input? If the fix only works for a
   neater version of the input, that's a finding.

6. **Then the rest:** missing or wrong tests for changed behaviour, security
   (secrets, injection, unsafe input), and the repo's own rules. Leave out
   style the repo doesn't ask for and anything you can't point at.

7. **Report**, as below. Change no files in the repo outside `.qc/`.

## Report

End your final message with exactly one fenced block, and nothing after it:

```json
{
  "verdict": "changes",
  "summary": "One or two sentences on the change and what you checked.",
  "checks_run": ["pytest tests/test_orders.py", "python -m pytest .qc/test_failed_download.py"],
  "findings": [
    {
      "severity": "high",
      "file": "app/orders.py",
      "line": 42,
      "title": "A failed download is now stored as a good image",
      "detail": "Before this change, a failed fetch inserted an error row and stopped. That branch was replaced, so a failed fetch now falls through and is saved with a path. .qc/test_failed_download.py shows it.",
      "suggestion": "Restore the failure branch for new rows."
    }
  ]
}
```

- `verdict` is `pass` when there are no findings, otherwise `changes`.
- `severity` is `high` (broken, loses data, or unsafe), `medium` (wrong in a
  case that will happen, or untested changed behaviour), or `low` (minor but real).
- `file` and `line` point at the change, in the new version of the file.
- Order findings most severe first. Say in `detail` how you showed it.
- An empty `findings` list is a good result. Don't invent findings to fill it.
