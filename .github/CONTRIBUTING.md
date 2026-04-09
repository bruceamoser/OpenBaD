# Contributing to OpenBaD

## Development Workflow

OpenBaD uses a strict issue-driven workflow. Every change goes through:

```
GitHub Issue → Feature Branch → Pull Request → Review → Squash Merge
```

### Getting Started

1. **Clone the repo:**
   ```bash
   git clone https://github.com/bruceamoser/OpenBaD.git
   cd OpenBaD
   ```

2. **Set up the dev environment:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -e ".[dev]"
   ```

3. **Run tests:**
   ```bash
   pytest
   ```

4. **Run linter:**
   ```bash
   ruff check src/ tests/
   ruff format --check src/ tests/
   ```

### Working on an Issue

1. **Pick an issue** from the [issue tracker](https://github.com/bruceamoser/OpenBaD/issues). Check priority labels (`P0` → `P3`) and dependency notes.

2. **Create a branch:**
   ```bash
   git checkout main && git pull
   git checkout -b feature/<issue-number>-<short-desc>
   ```

3. **Implement** the change, following the acceptance criteria in the issue body.

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

### Code Conventions

- **Python ≥ 3.11** — type hints on all public functions.
- **Linter/Formatter:** `ruff` (both format and check).
- **Tests:** `pytest` + `pytest-asyncio`.
- **Licenses:** All dependencies must be permissive (MIT, Apache 2.0, BSD). No BSL, AGPL, or GPL.

### Labels

Issues and PRs use these label categories:

| Category | Labels |
|:---|:---|
| Phase | `phase-1` through `phase-5` |
| Type | `setup`, `feature`, `test`, `docs` |
| Component | `nervous-system`, `event-bus`, `ebpf`, `reflex-arc`, `proprioception`, `interoception` |
| Priority | `P0-critical`, `P1-high`, `P2-medium`, `P3-low` |
| Size | `size-XS`, `size-S`, `size-M`, `size-L` |

### Project Architecture

See the [README](../README.md) for the phase document index and dependency graph.
