# AsciiDoc plugin

Optional OpenBaD plugin that renders AsciiDoc documents with `asciidoctor`.

```bash
# Install with the plugin_install_builtin tool, then smoke-test with
# plugin_call(plugin="asciidoc", action="check").
printf '{"action":"render","workspace":"'$PWD'","arguments":{"path":"docs/guide.adoc"}}' \
  | python3 main.py
```

Requires `asciidoctor` on `PATH` (`gem install asciidoctor`). PDF output also
requires `asciidoctor-pdf`.
