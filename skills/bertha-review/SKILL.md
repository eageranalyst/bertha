---
name: bertha-review
description: Review a pull request's change for real problems, independently of whoever wrote it. Read the diff and the code around it, check it against the task and the repo's rules, run the checks, and report findings in a fixed JSON block. Read-only; use for Bertha's code reviews.
---

# Code review

You are reviewing a pull request you didn't write. The change is checked out
in /data/repo on its branch. Your message tells you the pull request number,
its base branch and the task it was for. You can't push, comment or change
GitHub: Bertha posts your findings and has them fixed.

## Steps

1. **See the change.** Run `git diff origin/<base>...HEAD --stat`, then read the
   diff file by file (`git diff origin/<base>...HEAD -- <path>`). Read the code
   around each change too: callers, the tests, what a renamed thing was used for.
2. **Know what it was for.** Read the task in your message. If it names an
   issue, read it with `get_issue`. Check the repo's rules: the saved notes and
   the conventions page (`bertha` tools), and README or CONTRIBUTING if relevant.
3. **Check it works.** Run the repo's own checks that cover the changed code:
   its tests for those files, the type checker, the linter. Note what you ran
   and what failed. Read CI's results for the pull request with `pr_status`.
4. **Look for real problems:**
   - Bugs: wrong logic, unhandled cases the code will actually meet, broken
     error handling, race conditions, off-by-one, wrong types.
   - Behaviour the task asked for that's missing, or behaviour it didn't ask for.
   - Tests: changed behaviour without a test, or a test that doesn't test it.
   - Security: secrets, injection, unsafe input handling, permissions.
   - The repo's own rules and saved notes, where the change breaks one.
   Leave out style the repo doesn't ask for, and anything you can't point at.
5. **Report**, as below. Change no files.

## Report

End your final message with exactly one fenced block, and nothing after it:

```json
{
  "verdict": "pass",
  "summary": "One or two sentences on the change and what you checked.",
  "checks_run": ["pytest tests/test_orders.py", "npm run typecheck"],
  "findings": [
    {
      "severity": "high",
      "file": "app/orders.py",
      "line": 42,
      "title": "Cancelled orders are still counted in the total",
      "detail": "What's wrong, why it matters, and how to see it.",
      "suggestion": "What the fix should do, briefly."
    }
  ]
}
```

- `verdict` is `pass` when there are no findings, otherwise `changes`.
- `severity` is `high` (broken or unsafe), `medium` (wrong in a case that
  will happen, or untested behaviour), or `low` (minor but real).
- `file` and `line` point at the change, in the new version of the file.
- An empty `findings` list is a good result. Don't invent findings to fill it.
