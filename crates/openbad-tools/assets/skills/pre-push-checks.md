---
name: pre-push-checks
description: Choose and run the smallest set of checks that proves a change before pushing it, and report the exact command with its result.
---

# Pre-push checks

A change is unverified until the command that covers it has run. Pick the
narrowest command that exercises what you touched; widen only when the narrow
result is ambiguous or the change crosses packages.

## Find the project's own commands

Read the manifest before inventing a command:

- `list_recipes` — declared recipes in the project manifest.
- `list_gates` — declared verification gates with pass/fail criteria.
- `run_task` — named tasks (a `package.json` script, a `Makefile` target, a
  workspace task). Prefer this over re-typing the command.
- `run_tests` — project test runner already wired to the manifest.

If `.openbad/permissions.yaml` denies a command class, treat that as a project
rule: report it and ask the human to change the policy instead of finding a
loophole.

## Order that fails fast

1. Formatter in write mode, so later steps see final text.
2. Compile or type-check the touched package only.
3. Lint the touched files, not the tree.
4. Unit tests for the touched package, filtered to the affected area when the
   runner supports it.
5. The project's declared gate (`run_gate`) or invariant check
   (`check_invariants`) if one exists.

Stop at the first failure and fix the cause. A retry that changes nothing but
the clock is not a fix.

## What to report

For every check you ran, state the command and its outcome — passed, failed, or
not run and why. Never describe a command as passing when only a subset ran, and
never list a command you did not execute. If something could not be run
(missing tool, no network, needs a device), say so plainly in the same list.

## Do not

- Do not bypass hooks (`--no-verify` or equivalent) to get a push through.
- Do not disable or downgrade a check to make it pass.
- Do not reformat files you did not otherwise touch; it hides the real diff.
- Do not run the whole suite "just in case" when the change is one function in
  one package — escalate only if the targeted run cannot prove the change.
