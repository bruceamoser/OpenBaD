# ADR-005 — Capability Seams and Plugin Strategy

- **Status:** Accepted
- **Date:** 2026-10-19

## Context

The harness needs swappable providers, tools, storage, embeddings, LSP, shell,
MCP, and UI surfaces. DSH demonstrates the value of an "everything is a
capability seam" architecture, but its Cordis runtime is TypeScript-specific.

Options:

1. A monolithic Rust binary with compile-time modules.
2. Trait-based capability seams with compile-time providers.
3. Dynamic plugins via WASM.
4. Sidecar plugins via stdio/HTTP.

## Decision

Use **trait-based capability seams** for v1:

- Every capability has a **Service Definition** (trait), a **Provider**
  (implementation), and a **Consumer** (tool/UI/harness).
- Providers are selected at startup from config.
- Built-in providers are compiled in; optional providers are feature-gated.
- Dynamic extension is supported through **MCP** (tools/resources/prompts) and
  **skills** (markdown + scripts), not arbitrary in-process code.
- WASM plugins are a later option for untrusted extensions.

## Consequences

- Clean dependency direction and easy testing with fakes.
- No runtime code loading in v1; smaller attack surface.
- Adding an in-process provider requires a rebuild.
- MCP and skills cover most extension needs without a plugin ABI.
- A future WASM ABI can be added behind a new seam without breaking consumers.

## Alternatives considered

- **Monolith:** rejected; poor testability and provider coupling.
- **WASM in v1:** rejected for complexity and ABI immaturity in this codebase.
- **Sidecar plugins:** retained for MCP; not a general plugin model.

## References

- [Rust Workspace and Crates](../architecture/02-rust-workspace-and-crates.md)
- [Agent Runtime and Tools](../architecture/05-agent-runtime-and-tools.md)
