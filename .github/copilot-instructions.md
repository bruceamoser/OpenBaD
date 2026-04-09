# OpenBaD — Copilot Agent SOP

## Workflow: Issue → Branch → PR → Merge

### 1. Pick the Next Issue
- Work issues in priority order: `P0` → `P1` → `P2` → `P3`.
- Within the same priority, respect dependency order (check "Depends on" in the issue body).
- Only work one issue at a time.

### 2. Create a Feature Branch
- Branch from `main`.
- Branch naming: `feature/<issue-number>-<short-desc>` (e.g., `feature/12-mqtt-broker-setup`).
- Never commit directly to `main`.

### 3. Implement
- Follow the acceptance criteria in the issue body exactly.
- Every issue must include tests (unit tests at minimum).
- Use `pytest` for testing, `ruff` for linting.
- Keep commits small and focused. Each commit message references the issue: `#12: Add MQTT broker systemd config`.
- Do not modify files outside the scope of the issue unless required as a direct dependency.

### 4. Create a Pull Request
- PR title: `#<issue-number>: <short description>` (e.g., `#12: Select MQTT broker and create systemd service`).
- PR body must include:
  - `Closes #<issue-number>`
  - A summary of changes
  - How to test
- Assign relevant labels (same as the issue).

### 5. Review the PR
- Verify all acceptance criteria from the issue are met.
- Verify tests pass.
- Verify no lint errors (`ruff check`).
- Check for security issues (OWASP Top 10 awareness).

### 6. Merge
- Merge strategy: **Squash and merge**.
- Ensure the squashed commit message includes `Closes #<issue-number>`.
- Delete the feature branch after merge.

### 7. Move to Next Issue
- Pull latest `main`.
- Pick the next issue by priority and dependency order.
- Repeat from step 2.

---

## Project Conventions

### Language & Runtime
- Python ≥ 3.11, Linux, systemd, cgroup v2.
- All dependencies must use permissive open-source licenses (MIT, Apache 2.0, BSD). No BSL, AGPL, or GPL.

### Code Style
- Formatter/linter: `ruff` (format + check).
- Type hints required on all public functions.
- No docstrings on private helpers unless the logic is non-obvious.

### Testing
- Framework: `pytest` with `pytest-asyncio` for async code.
- Tests live in `tests/` mirroring the `src/` structure.
- Minimum: unit tests for all public functions. Integration tests where the issue specifies.
- Use fixtures for MQTT broker connections, mock eBPF probes, etc.

### File Structure
- Source code: `src/openbad/`
- Tests: `tests/`
- Protobuf schemas: `src/openbad/nervous_system/schemas/`
- Config files: `config/`

### Commit Messages
- Format: `#<issue>: <imperative verb> <what changed>` (e.g., `#5: Add protobuf schemas for telemetry messages`).
- Keep the first line under 72 characters.

### Labels
- **Phase:** `phase-1`, `phase-2`, `phase-3`, `phase-4`, `phase-5`
- **Type:** `setup`, `feature`, `test`, `docs`
- **Component:** `nervous-system`, `event-bus`, `ebpf`, `reflex-arc`, `proprioception`, `interoception`
- **Priority:** `P0-critical`, `P1-high`, `P2-medium`, `P3-low`
- **Size:** `size-XS`, `size-S`, `size-M`, `size-L`
