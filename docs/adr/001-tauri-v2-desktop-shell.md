# ADR-001 — Tauri v2 + React Desktop Shell

- **Status:** Accepted
- **Date:** 2026-10-19

## Context

OpenBaD Desktop needs a native desktop shell with:

- A Rust core that owns the harness, tools, SQLite, and subprocesses.
- A rich UI for chat, diffs, editors, memory graphs, and knowledge views.
- Small installers, OS integration, and signed updates.
- A mature ecosystem and a path to Linux, macOS, and Windows.

Candidate approaches:

1. Tauri v2 (Rust backend + web UI).
2. Electron (Node backend + web UI).
3. Pure Rust UI (egui, iced, Slint, Dioxus).
4. A local web server opened in a browser.

## Decision

Use **Tauri v2** with a **React 19 + TypeScript + Vite + Tailwind CSS 4**
frontend.

Reasons:

- Rust core with a small, native shell.
- Small installers and low idle memory compared with Electron.
- Native tray, menus, notifications, file dialogs, keychain, and updater.
- Web UI ecosystem for the editor, diff, graph, and chat surfaces.
- Tauri v2 has stable mobile/desktop APIs and a signed updater.

## Consequences

- The UI is a thin client over typed Tauri commands/events.
- The Rust core must expose a stable command/event contract.
- The build pipeline includes Rust and Node toolchains.
- Linux requires WebKitGTK system packages; document them.
- The UI cannot access SQLite or the filesystem directly.

## Alternatives considered

- **Electron:** rejected for installer size, memory, and a second runtime.
- **egui/iced/Slint:** rejected for UI richness and the cost of building chat,
  diff, and editor surfaces from scratch.
- **Dioxus:** viable, but the React ecosystem is more mature for the planned UI.
- **Local web server:** rejected for lifecycle, security, and UX complexity.

## References

- [Desktop Application](../architecture/08-desktop-application.md)
- [Rust Workspace and Crates](../architecture/02-rust-workspace-and-crates.md)
