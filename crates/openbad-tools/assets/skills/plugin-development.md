---
name: plugin-development
description: Create, test, and call OpenBaD plugins for capabilities outside the built-in tool surface.
---

# Plugin development

Plugins extend OpenBaD without changing the core binary. A plugin is a
directory under `.openbad/plugins/<name>/` with a `plugin.yaml` manifest and an
executable entrypoint. Use plugins for format converters (AsciiDoc, Pandoc),
language-specific analyzers, or integrations that should be optional.

## Create a plugin

1. Call `write_plugin` with `name`, `description`, and `runtime`
   (`python`, `node`, or `shell`). This writes `plugin.yaml`, an entrypoint,
   and `SKILL.md`.
2. Edit the entrypoint. It receives one JSON object on stdin:

   ```json
   {"plugin": "example", "action": "run", "arguments": {"path": "doc.adoc"}, "workspace": "/path/to/project"}
   ```

   It must print one JSON object on stdout:

   ```json
   {"ok": true, "content": "human-readable result", "metadata": {"key": "value"}}
   ```

   `content`, `metadata`, and `is_error` are optional; non-JSON stdout is
   treated as text. Diagnostics belong on stderr.
3. At the end of a failing run, return `ok: false` with an actionable message
   and a non-zero exit code.
4. Declare each supported action in `plugin.yaml` with a description and JSON
   input schema. If `actions` is omitted, any action is passed through.

## Test it

1. `plugin_test` runs the manifest's `test_command` and records evidence under
   `.openbad/plugin-runs/`.
2. `plugin_call` runs one action with `arguments`. Use it for both smoke tests
   and real work.
3. A plugin that cannot run because a dependency is missing must say which
   dependency and how to install it. Never silently succeed.

## Rules

- Keep plugin output bounded; large artifacts go to files in the workspace.
- Do not read credentials or environment secrets. Read configuration from
  explicit arguments or `AGENTS.md`-documented project files.
- Require `confirm: true`-style explicit arguments for destructive actions.
- Plugin commands run with Full access only and are subject to
  `.openbad/permissions.yaml`.
- Ship a `SKILL.md` that tells the assistant when and how to use the plugin.
- `plugin_install_builtin` installs the bundled `asciidoc` plugin; use it as a
  reference implementation.
