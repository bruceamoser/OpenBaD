---
name: code-review
description: Review a diff for correctness, regressions, and unintended scope, and report only findings you can defend.
---

# Code review

A review answers one question: does this change do what it claims without
breaking something else? Findings you cannot substantiate are noise.

## Get the diff

- `git_diff` for unstaged work, `git_status` to see what is staged or untracked.
  For a pull request, read the change through `github` (pull request files), not
  a guess at what it contains.
- Read the surrounding code of every hunk you intend to comment on. A hunk alone
  hides the invariant it breaks.
- If the change claims a behaviour, find where that behaviour is enforced — a
  test, a gate, a call site. If nothing enforces it, that is the finding.

## What to look for, in order

1. **Correctness.** Off-by-one, inverted conditions, swallowed errors, wrong
   default, missing case in a match, a value used after it is replaced.
2. **Regressions.** Callers of a changed signature or semantic; serialized or
   persisted data whose shape changed; behaviour that callers relied on.
3. **Concurrency and lifecycle.** A listener, task, or handle created in a hot
   path and never disposed; shared state mutated from two places.
4. **Scope.** Unrelated fixes, reformatting, or refactors mixed into a focused
   change. Name them; they make the real change unreviewable.
5. **Silent failure.** An error path that now returns a value where it used to
   fail, or a catch that used to propagate.
6. **Tests.** Does the change come with a test that fails without it? Is the test
   asserting the behaviour rather than the implementation?

## Reporting

- Give file, line, and the concrete failure: inputs, or the sequence that
  reaches the bad state. "This looks wrong" is not a finding.
- Rank by severity, and say when something is a question rather than a defect.
- State what you did not check (a platform, a provider, a manual step) instead
  of implying the whole change was verified.
- If the change is fine, say so plainly and stop. Inventing a nitpick to seem
  useful wastes the author's time.
