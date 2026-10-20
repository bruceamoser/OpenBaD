---
name: asciidoc-rendering
description: Render AsciiDoc with the optional asciidoc plugin and report missing dependencies clearly.
---

# AsciiDoc rendering

AsciiDoc support ships as the optional `asciidoc` plugin, not as a core tool.

1. Check whether it is installed with `plugin_list`. If absent, call
   `plugin_install_builtin` with `name: "asciidoc"`.
2. Call `plugin_call` with `plugin: "asciidoc"`, `action: "check"` to verify
   that `asciidoctor` is on `PATH`.
3. Render with `action: "render"`, `arguments` containing a workspace-relative
   `path` and optional `output` and `backend` (`html5` or `docbook`).
4. Read the generated file with `read_file` or present it with
   `present_artifact`.

If `asciidoctor` is missing, tell the human the exact install command for their
platform. Do not substitute a different Markdown renderer or claim the render
succeeded. PDF output additionally needs `asciidoctor-pdf`; check for it
explicitly.
