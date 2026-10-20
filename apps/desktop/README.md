# OpenBaD Desktop App

Tauri v2 shell and React UI for OpenBaD Desktop.

## Layout

```text
apps/desktop/
├── src-tauri/          # Rust Tauri backend
│   ├── src/lib.rs      # AppState, chat/session/store commands, builder
│   ├── src/main.rs     # Desktop entry point
│   ├── capabilities/   # Tauri v2 permissions
│   └── tauri.conf.json
└── ui/                 # React + TypeScript + Vite + Tailwind
    ├── src/
    └── package.json
```

## Prerequisites

- Rust 1.85+ with `rustfmt` and `clippy`
- Node.js 20+ and pnpm 9+
- Linux: `libwebkit2gtk-4.1-dev`, `libappindicator3-dev`, `librsvg2-dev`,
  `libgtk-3-dev`, `libdbus-1-dev`, `pkg-config`, `patchelf`
- A Secret Service provider for the keychain (GNOME Keyring or KWallet)

## Run

```bash
cd apps/desktop
pnpm --dir ui install
pnpm --dir ui build
./ui/node_modules/.bin/tauri dev
```

The Tauri CLI is installed with the UI package. Running it from `apps/desktop`
lets it find `src-tauri/tauri.conf.json`. The `beforeDevCommand` runs
`pnpm --dir ui dev` from the project root.

## Build

```bash
cd apps/desktop
pnpm --dir ui build
./ui/node_modules/.bin/tauri build
```

The current scaffold uses `icons/icon.png` from the project logo. Add platform
icons and signing before shipping installers.

## UI development without Tauri

`pnpm --dir ui dev` serves the React UI at `http://localhost:5173`. When the page
is opened in a normal browser, `ui/src/backend.ts` switches to a mock backend that
streams reasoning, content, tool calls, and usage. This is useful for layout,
scroll, and streaming tests without rebuilding the native app.

`pnpm --dir ui test:e2e` runs the Playwright smoke tests against that mock
backend. They cover streamed reasoning and tool cards, auto-scroll,
cancellation, projects and sessions, checkpoints, memory, project knowledge and
retrieval, reference folders, assistant artifacts, and the command menu.

## Interface

The workspace is organised around projects. Create one by entering a name;
OpenBaD creates a single workspace under `~/OpenBaD/workspaces` (override with
`OPENBAD_WORKSPACES_DIR`). Existing project directories remain supported. Each
project groups its sessions beneath it, and sessions can be renamed inline.

A top application menu (File, Edit, View, Project, Help) owns project-wide
actions, including new session, settings, every project area, index refresh, and
keyboard shortcuts. Each project also exposes Overview, Workspace, References,
Knowledge, Retrieval, and Memory beneath its name. These open in a resizable
split panel beside the chat.
Reference folders are selected with the native folder picker and are read-only.
Knowledge notes and imported UTF-8 files are chunked and searchable through the
same retrieval surface as indexed code and durable memory. Assistant-presented artifacts (web pages,
images, Markdown, diffs, and files) open in the same panel.

The composer carries the access mode: **Read only** (default each launch),
**Can edit**, and **Full access**. Edits and diagnostics remain available from
the project panel without permanent chrome. Settings holds model, access,
secrets, project, and about tabs. Press `Ctrl/Cmd+K` for the command menu,
`Ctrl/Cmd+N` for a new session, and `Ctrl/Cmd+,` for settings. Agent turns have
no fixed step budget; use Stop to end a run.

## Commands

| Command | Purpose |
| --- | --- |
| `health` | Returns the store status and SQLite schema version |
| `foundation` | Returns schema/integrity/event metrics, audit chain state, providers, and model routes |
| `list_projects` / `add_project` / `rename_project` / `remove_project` | Manage workspace projects |
| `rename_session` | Rename a session |
| `project_overview` / `project_files` / `project_file` | Project counts, indexed files, previews |
| `workspaces_directory` / `create_project` | Resolve the default workspace root and create a named project workspace |
| `project_repos` / `add_project_repo` / `remove_project_repo` | Manage read-only reference folders |
| `knowledge_documents` / `knowledge_document` / `add_knowledge_note` | Read and add project knowledge |
| `import_knowledge_file` / `delete_knowledge_document` | Import or remove knowledge |
| `search_project` | Fuse knowledge, code, and memory with locators |
| `sessions` / `search_sessions` / `session_view` | List, search, and load sessions |
| `save_api_key` / `clear_api_key` / `api_key_status` | Manage the DeepSeek key in the OS keychain |
| `chat` | Runs the DeepSeek agent loop with workspace tools and persists the turn |

The desktop build enables the `keyring` feature by default. Set
`DEEPSEEK_API_KEY` as a fallback when no key is saved. Set
`OPENBAD_DEEPSEEK_BASE_URL` to point the adapter at a mock or proxy.

When the UI is opened in a browser without Tauri, invoke calls fail gracefully
and the panels report that the backend is unavailable.

## Version alignment

The Rust `tauri` crate, `@tauri-apps/api`, and `@tauri-apps/cli` must share the
same major/minor line. The scaffold pins npm packages to the 2.11 line to match
the resolved Rust crate. Bump them together.
