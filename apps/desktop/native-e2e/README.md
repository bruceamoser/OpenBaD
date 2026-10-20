# Native Tauri end-to-end tests

These tests drive the real desktop binary through the W3C WebDriver protocol.
They use a temporary SQLite database, a temporary workspace, and a local mock
DeepSeek SSE endpoint, so they do not touch your normal OpenBaD data or call a
provider.

## Prerequisites

- `tauri-driver` on `PATH` (`cargo install tauri-driver --locked`)
- `WebKitWebDriver` on `PATH` (`apt install webkitgtk-webdriver`, or set
  `WEBKIT_WEBDRIVER_BIN` to an absolute path)
- A display; use `xvfb-run -a` on headless machines
- A built app binary:

```bash
cd apps/desktop
./ui/node_modules/.bin/tauri build --no-bundle --debug
```

If the binary is not at `target/debug/openbad-desktop`, set
`OPENBAD_APP_BINARY`.

## Run

```bash
cd apps/desktop/native-e2e
npm test
```

The suite checks that project counts render, the Agents area opens, a chat turn
completes against the mock provider, and copy controls exist at the top and
bottom of assistant responses plus on user messages.
