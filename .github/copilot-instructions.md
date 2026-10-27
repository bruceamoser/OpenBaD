# OpenBaD — Copilot Agent SOP

## Workflow: Issue → Branch → PR → Merge

### 1. Pick the Next Issue
- Work issues in priority order: `P0` → `P1` → `P2` → `P3`.
- Within the same priority, respect dependency order (check "Depends on" in the issue body).
- Only work one issue at a time.

### 2. Create a Feature Branch
- Branch from `main`.
- Branch naming: `feature/<issue-number>-<short-desc>` (e.g., `feature/12-deepseek-adapter`).
- Never commit directly to `main`.

### 3. Implement
- Follow the acceptance criteria in the issue body exactly.
- Every issue must include tests (unit tests at minimum).
- Keep commits small and focused. Each commit message references the issue:
  `#12: Add DeepSeek streaming adapter`.
- Do not modify files outside the scope of the issue unless required as a direct dependency.

### 4. Create a Pull Request
- PR title: `#<issue-number>: <short description>`.
- PR body must include:
  - `Closes #<issue-number>`
  - A summary of changes
  - How to test
- Assign relevant labels (same as the issue).

### 5. Review the PR
- Verify all acceptance criteria from the issue are met.
- Verify Rust and UI tests pass.
- Verify no lint errors (`cargo clippy -D warnings`, `pnpm lint`).
- Verify cache invariants for harness changes (see below).
- Check for security issues (see `docs/architecture/09-security-and-threat-model.md`).

### 6. Merge
- Merge strategy: **Squash and merge**.
- Ensure the squashed commit message includes `Closes #<issue-number>`.
- Delete the feature branch after merge (both remote and local).

### 7. Branch Cleanup
- After merging, delete the remote branch: `git push origin --delete feature/<branch>`.
- Delete the local branch: `git branch -d feature/<branch>`.
- Prune stale remote-tracking refs: `git remote prune origin`.
- Periodically verify no stale branches remain: `git branch -r --list 'origin/feature/*'`.

### 8. Move to Next Issue
- Pull latest `main`.
- Pick the next issue by priority and dependency order.
- **Continue working through issues automatically** — do not stop to ask for permission between issues.
- Only pause if you encounter a design decision, ambiguity, or question that requires human input.
- Repeat from step 2.

### 9. Build the Release Binary
- After finishing a user-facing change, always rebuild the desktop release binary.
- Build the frontend first, then the release shell (from `apps/desktop`):

  ```bash
  (cd ui && pnpm build)
  ./ui/node_modules/.bin/tauri build --no-bundle --config '{"build":{"beforeBuildCommand":""}}'
  ```

- The release artifact is the Cargo workspace target:
  `target/release/openbad-desktop`. Do not use `--debug`.
- Verify the artifact is fresh before reporting completion:
  `stat -c '%y %s' target/release/openbad-desktop`.
- Bundles/installers are not produced by default. Do not tag, publish, or attach
  the binary to a GitHub release unless the human explicitly asks.

---

## Project Conventions

### Language & Runtime
- Rust 1.85+ (edition 2024) for the core and Tauri backend.
- TypeScript + React 19 + Vite for the desktop UI.
- SQLite 3.45+ with FTS5 and `sqlite-vec`.
- DeepSeek is the primary model provider (`deepseek-flash`, `deepseek-v4-pro`).
- All dependencies must use permissive open-source licenses (MIT, Apache 2.0, BSD).
  No BSL, AGPL, or GPL. `cargo deny` and `pnpm audit` enforce this.

### Code Style
- Rust: `cargo fmt --all`, `cargo clippy --workspace --all-targets -- -D warnings`.
- TypeScript: `pnpm lint`, `pnpm typecheck`, `pnpm format`.
- Public Rust APIs have doc comments; private helpers do not need them.
- No `unwrap()`/`expect()` in library code; return typed errors.
- No `unsafe` unless isolated, documented, and tested.

### Architecture Rules
- **The session event log is the source of truth.** Model-visible content must be logged.
- **The model-visible prefix is immutable and append-only.** Volatile context goes at the tail.
- **No database access from the UI.** Use Tauri commands and events.
- **Domain crates do not depend on infrastructure crates.** Use capability traits.
- **Tools never write to the session log directly.** The agent loop appends results.
- **Every irreversible action is approval-gated.**
- **Secrets live in the OS keychain**, never in the DB, logs, prompts, or fixtures.

### Cache Invariants (must be tested for harness changes)
1. Stable sections contain no timestamps, random IDs, or runtime state.
2. Tool schemas are sorted and canonicalized.
3. The tool schema set is stable within a request series.
4. History is append-only.
5. Volatile context is the last message.
6. Retries reuse the exact frozen request bytes.
7. Model, API format, thinking mode, and reasoning effort are epoch-level.
8. Workspace context is snapshotted; changes append tail messages.
9. Compaction is rare, boundary-aligned, and records a cache reset.
10. Tool output is spilled at creation, not rewritten later.
11. Cache hit/miss is recorded and surfaced.
12. `reasoning_content` is passed back verbatim when tools are present.

### Testing
- Rust unit tests: `#[cfg(test)]` or `crates/*/tests`.
- Integration tests: `tests/integration/` with a temp SQLite DB and mock provider.
- UI checks: TypeScript typecheck + Vite build; native E2E in
  `apps/desktop/native-e2e/` with Tauri + WebDriver.
- Benchmarks: `benches/` with Criterion; cache/cost benchmarks in `tests/fixtures/cache/`.
- Minimum: unit tests for all public functions; integration tests where the issue specifies.
- Real DeepSeek calls only in opt-in nightly jobs with a capped budget.

### File Structure
- Rust crates: `crates/openbad-*`
- Desktop app: `apps/desktop/src-tauri` and `apps/desktop/ui`
- SQL migrations: `migrations/`
- Cross-crate tests: `tests/`
- Architecture docs: `docs/architecture/`
- Plans: `docs/plans/`
- ADRs: `docs/adr/`
- Archived v0: `archive/openbad-v0/`
- Release binary: `target/release/openbad-desktop`

### Commit Messages
- Format: `#<issue>: <imperative verb> <what changed>`.
- Keep the first line under 72 characters.

### Labels
- **Phase:** `phase-0` … `phase-8`
- **Type:** `setup`, `feature`, `test`, `docs`, `performance`, `security`
- **Component:** `harness`, `store`, `providers`, `tools`, `workspace`, `memory`,
  `knowledge`, `autonomic`, `governance`, `desktop`, `mcp`
- **Priority:** `P0-critical`, `P1-high`, `P2-medium`, `P3-low`
- **Size:** `size-XS`, `size-S`, `size-M`, `size-L`

<!-- mermaid-ai-skills:start -->
## Mermaid Diagrams

When the user asks to create, edit, or visualize a diagram, follow the
instructions in `.github/instructions/mermaid.instructions.md`.
<!-- mermaid-ai-skills:end -->
