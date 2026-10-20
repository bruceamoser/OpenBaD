# Contributing to OpenBaD

## Development Workflow

OpenBaD uses a strict issue-driven workflow:

```
GitHub Issue → Feature Branch → Pull Request → Review → Squash Merge
```

## Getting Started

### Prerequisites

| Tool | Version | Purpose |
| --- | --- | --- |
| Rust | 1.85+ | Core runtime and Tauri backend |
| Node.js | 20+ | Desktop UI |
| pnpm | 9+ | UI package manager |
| SQLite | 3.45+ | Bundled by the app; CLI useful for debugging |
| DeepSeek API key | — | Model access |

Linux also needs the Tauri system dependencies (WebKitGTK, GTK, librsvg, and
build tools). The setup script and CI document the exact package list.

### Setup

```bash
git clone https://github.com/bruceamoser/OpenBaD.git
cd OpenBaD

# Rust workspace
cargo check --workspace

# Desktop UI
cd apps/desktop/ui
pnpm install
pnpm build

# Run the app
cd ..
./ui/node_modules/.bin/tauri dev
```

Set your DeepSeek key in the app settings. Keys are stored in the OS keychain,
never in `.env` files committed to the repository.

### Checks

```bash
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace

cd apps/desktop/ui
pnpm lint
pnpm typecheck
pnpm test
```

## Working on an Issue

1. **Pick an issue** from the [issue tracker](https://github.com/bruceamoser/OpenBaD/issues).
   Check priority labels (`P0` → `P3`) and dependency notes.
2. **Create a branch:**
   ```bash
   git checkout main && git pull
   git checkout -b feature/<issue-number>-<short-desc>
   ```
3. **Implement** the change, following the acceptance criteria.
4. **Write tests.** Every issue requires tests.
5. **Commit** with the issue reference:
   ```bash
   git commit -m "#<issue>: <imperative verb> <description>"
   ```
6. **Push and open a PR:**
   ```bash
   git push -u origin feature/<issue-number>-<short-desc>
   ```
   - PR title: `#<issue-number>: <short description>`
   - PR body must include `Closes #<issue-number>`
7. **After review, squash and merge.** Delete the branch.

## Code Conventions

- **Rust 1.85+, edition 2024.**
- **Formatter/linter:** `cargo fmt` and `cargo clippy -D warnings`.
- **TypeScript:** strict mode; `pnpm lint` and `pnpm typecheck`.
- **Errors:** `thiserror` in domain crates, `anyhow` at the application edge.
- **Public APIs:** doc comments. Private helpers: comments only when non-obvious.
- **No `unwrap()`/`expect()` in library code.**
- **Licenses:** MIT, Apache-2.0, or BSD only. No BSL, AGPL, or GPL.

## Architecture Rules

Read the [architecture docs](../docs/architecture/) before changing core code.

- The **session event log** is the source of truth; model-visible content must be logged.
- The **model-visible prefix** is immutable and append-only; volatile context goes last.
- The **UI never touches SQLite or the filesystem** directly.
- **Capability seams** (trait + provider + consumer) are the extension model.
- **Tools never write to the session log** directly.
- **Irreversible actions** require approval and an audit record.
- **Secrets** live in the OS keychain and are redacted from logs and prompts.
- **DeepSeek-first:** use `deepseek-flash` by default, `deepseek-v4-pro` for hard
  reasoning, and keep the request series cache-stable.

## Cache Invariants

Harness changes must preserve:

1. No volatile content in stable prompt sections.
2. Sorted, canonical tool schemas.
3. A stable tool schema set within a series.
4. Append-only history.
5. Volatile context at the tail.
6. Byte-identical retries.
7. Stable model, API format, thinking mode, and reasoning effort within a series.
8. Workspace context snapshots; file changes append tail messages.
9. Rare, boundary-aligned compaction with a recorded reset.
10. Spill at creation, never rewrite old tool results.
11. Recorded cache hit/miss.
12. Verbatim `reasoning_content` passback when tools are present.

## Testing

- Rust unit tests live beside the code or in `crates/*/tests`.
- Integration tests use a temporary SQLite database and a mock provider.
- UI tests use Vitest + Testing Library; E2E uses Playwright + Tauri.
- Cache/cost benchmarks use fixtures in `tests/fixtures/cache/`.
- Real DeepSeek calls are opt-in nightly jobs with a capped budget.

## Labels

| Category | Labels |
|:---|:---|
| Phase | `phase-0` through `phase-8` |
| Type | `setup`, `feature`, `test`, `docs`, `performance`, `security` |
| Component | `harness`, `store`, `providers`, `tools`, `workspace`, `memory`, `knowledge`, `autonomic`, `governance`, `desktop`, `mcp` |
| Priority | `P0-critical`, `P1-high`, `P2-medium`, `P3-low` |
| Size | `size-XS`, `size-S`, `size-M`, `size-L` |

## Project Architecture

See [docs/architecture/README.md](../docs/architecture/README.md) and the
[implementation plan](../docs/plans/0001-implementation-plan.md).
