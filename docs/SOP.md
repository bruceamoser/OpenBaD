# OpenBaD project SOP

Applies to all changes in `/home/bmoser/repos/OpenBaD`.

## 1. Intake

1. Turn the request into one or more GitHub issues. One issue per coherent, independently reviewable change.
2. Each issue must include: goal, scope/non-goals, acceptance criteria, affected areas, and a link to the relevant plan section.
3. Create the issues before branching. Do not start code from a verbal description alone.

## 2. Branch

1. Fetch and fast-forward `main` before branching.
2. Create a branch from `main`: `feat/issue-<number>-<short-slug>` or `fix/issue-<number>-<short-slug>`.
3. One issue per branch. If two issues are inseparable, combine them into one issue instead of mixing branches.
4. For parallel work, use separate git worktrees under `workspaces/openbad/wt-issue-<number>` so branches never share a dirty tree.

## 3. Work

1. Read the plan and the relevant code before editing.
2. Keep the diff scoped to the issue. Refactors needed for the issue are acceptable; unrelated cleanups are not.
3. Add or update tests for behavior changes.
4. Run the relevant checks while working:
   - `cargo test -p openbad-tools`
   - `cargo test -p openbad-store`
   - `cargo test -p openbad-desktop`
   - `pnpm --dir apps/desktop/ui typecheck`
   - `pnpm --dir apps/desktop/ui build`
5. Update the plan document as items complete; note partial work explicitly.

## 4. Commit and push

1. Commit focused changes with Conventional Commit messages, e.g. `fix(source-control): open repo screen in preview`.
2. Reference the issue in the commit body (`Refs #123`).
3. Push the branch and set upstream.

## 5. Pull request

1. Open a PR against `main` with:
   - summary of the change;
   - `Closes #<issue>` (or `Refs` if intentionally partial);
   - verification commands and results;
   - risks, follow-ups, and manual test notes.
2. Keep the PR body accurate; never paste shell-expanded command output into it.

## 6. Review

1. Review the diff independently (human or reviewer agent): correctness, security, regressions, tests.
2. Post blocking and non-blocking findings on the PR.
3. Address blocking findings before merge. Re-run checks after changes.
4. Do not merge with failing required checks or unresolved blocking comments.

## 7. Merge and cleanup

1. Merge with the repository's preferred strategy; delete the remote branch.
2. Remove the local branch and worktree.
3. Verify the linked issue closed automatically. If not, close it with a comment.
4. Close stale/duplicate issues and delete stale branches created for this work.
5. Update the plan/progress log.

## 8. Release

1. Decide the semantic version (patch for fixes, minor for features).
2. Bump all version locations: workspace `Cargo.toml`, `Cargo.lock`, `apps/desktop/src-tauri/tauri.conf.json`, `apps/desktop/ui/package.json`.
3. Update release notes/changelog.
4. Build the frontend and release binary to confirm it compiles:
   - `pnpm --dir apps/desktop/ui build`
   - `cd apps/desktop && ./ui/node_modules/.bin/tauri build --no-bundle --config '{"build":{"beforeBuildCommand":""}}'`

   This is a compile smoke check only. **Do not install, copy, or run the built
   binary on the local machine.** A local run of `target/release/openbad-desktop`
   proves nothing about the shipped artifact and skips the path users actually
   take.
5. Verify the release through the updater, not by deploying locally.
   `openbad-update` reads `repos/<repo>/releases/latest` and installs the
   platform asset, so only a published release is testable:
   - Tag `vX.Y.Z` and push the tag. CI builds the Linux, macOS, and Windows assets.
   - Verify the GitHub release exists with all expected assets and `SHA256SUMS`.
   - On a machine running the previous version, use the in-app updater to move
     to `X.Y.Z` and confirm it checks, downloads, installs, and restarts.
6. If the release is broken, delete the release/tag, revert or hotfix on a new branch, and repeat the SOP. Do not silently replace assets.

## Exceptions

- Security incidents may use a private branch and coordinated disclosure, but still get an issue and a review before release.
- If an issue grows beyond one reviewable change, split it before opening the PR; do not merge a half-finished issue just to close it.
