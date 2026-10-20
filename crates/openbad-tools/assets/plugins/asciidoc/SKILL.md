---
name: asciidoc
description: Render AsciiDoc with the bundled asciidoc plugin.
---

# AsciiDoc plugin

Use `plugin_call` with `plugin: "asciidoc"`.

- `action: "check"` reports whether `asciidoctor` and `asciidoctor-pdf` exist.
- `action: "render"` takes `arguments: {"path": "docs/guide.adoc"}` plus
  optional `output`, `backend` (`html5`, `docbook`, `pdf`), and `attributes`.
- Paths are workspace-relative and cannot escape the workspace.
- If the tool is missing, give the human the install command; do not pretend
  the render worked.
