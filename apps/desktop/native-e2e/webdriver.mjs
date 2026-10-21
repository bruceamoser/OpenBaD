import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer as createHttpServer } from "node:http";
import { createServer as createNetServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { delimiter as pathDelimiter } from "node:path";
import process from "node:process";

const ELEMENT_KEY = "element-6066-11e4-a52e-4f735466cecf";

export async function freePort() {
  return new Promise((resolve, reject) => {
    const server = createNetServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => resolve(address.port));
    });
  });
}

export function resolveAppBinary() {
  const candidates = [
    process.env.OPENBAD_APP_BINARY,
    join(process.cwd(), "..", "..", "..", "target", "release", "openbad-desktop"),
    join(process.cwd(), "..", "..", "..", "target", "debug", "openbad-desktop"),
  ].filter(Boolean);
  const found = candidates.find((candidate) => existsSync(candidate));
  if (!found) {
    throw new Error(
      "OpenBaD app binary not found. Build it with `cd apps/desktop && ./ui/node_modules/.bin/tauri build --no-bundle` or set OPENBAD_APP_BINARY.",
    );
  }
  return found;
}

export function resolveDriverBinary() {
  if (process.env.TAURI_DRIVER_BIN) return process.env.TAURI_DRIVER_BIN;
  return "tauri-driver";
}

export function resolveWebKitDriver() {
  const configured = process.env.WEBKIT_WEBDRIVER_BIN;
  if (configured) {
    if (existsSync(configured)) return configured;
    throw new Error(`WEBKIT_WEBDRIVER_BIN does not exist: ${configured}`);
  }
  for (const directory of (process.env.PATH ?? "").split(pathDelimiter)) {
    const candidate = join(directory, "WebKitWebDriver");
    if (existsSync(candidate)) return candidate;
  }
  return undefined;
}

export async function startMockDeepSeek() {
  const content = "Hello from native E2E.";
  const server = createHttpServer((request, response) => {
    if (request.method !== "POST" || !request.url?.endsWith("/chat/completions")) {
      response.writeHead(404);
      response.end();
      return;
    }
    let body = "";
    request.on("data", (chunk) => {
      body += chunk;
    });
    request.on("end", () => {
      let payload = {};
      try {
        payload = JSON.parse(body);
      } catch {
        // The provider request is JSON; an empty mock payload is enough here.
      }
      if (payload.stream) {
        response.writeHead(200, {
          "content-type": "text/event-stream",
          "cache-control": "no-cache",
          connection: "keep-alive",
        });
        response.write(
          `data: ${JSON.stringify({
            id: "e2e",
            object: "chat.completion.chunk",
            model: payload.model ?? "deepseek-flash",
            choices: [
              {
                index: 0,
                delta: { role: "assistant", content },
                finish_reason: null,
              },
            ],
          })}\n\n`,
        );
        response.write(
          `data: ${JSON.stringify({
            id: "e2e",
            choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
            usage: { prompt_tokens: 12, completion_tokens: 5, total_tokens: 17 },
          })}\n\n`,
        );
        response.write("data: [DONE]\n\n");
        response.end();
        return;
      }
      response.writeHead(200, { "content-type": "application/json" });
      response.end(
        JSON.stringify({
          id: "e2e",
          model: payload.model ?? "deepseek-flash",
          choices: [
            { index: 0, message: { role: "assistant", content }, finish_reason: "stop" },
          ],
          usage: { prompt_tokens: 12, completion_tokens: 5, total_tokens: 17 },
        }),
      );
    });
  });
  const port = await freePort();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", resolve);
  });
  return {
    baseUrl: `http://127.0.0.1:${port}`,
    close: () =>
      new Promise((resolve) => {
        server.close(resolve);
        server.closeAllConnections?.();
      }),
  };
}

export async function makeFixture() {
  const root = await mkdtemp(join(tmpdir(), "openbad-native-"));
  const workspace = join(root, "workspace");
  await mkdir(workspace, { recursive: true });
  await writeFile(join(workspace, "Cargo.toml"), "[workspace]\nmembers = []\n");
  await writeFile(join(workspace, "README.md"), "# Native E2E fixture\n");
  return {
    root,
    workspace,
    dbPath: join(root, "openbad.db"),
    cleanup: () => rm(root, { recursive: true, force: true }),
  };
}

export class WebDriverClient {
  constructor(baseUrl, sessionId) {
    this.baseUrl = baseUrl;
    this.sessionId = sessionId;
  }

  static async create(driverUrl, application) {
    const response = await fetch(`${driverUrl}/session`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        capabilities: {
          alwaysMatch: { "tauri:options": { application } },
        },
      }),
      signal: AbortSignal.timeout(30_000),
    });
    const payload = await response.json();
    if (!response.ok || payload.value?.error) {
      throw new Error(`could not create WebDriver session: ${JSON.stringify(payload)}`);
    }
    return new WebDriverClient(driverUrl, payload.value.sessionId);
  }

  async command(path, method = "GET", body) {
    const response = await fetch(`${this.baseUrl}/session/${this.sessionId}${path}`, {
      method,
      headers: body === undefined ? {} : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(30_000),
    });
    const text = await response.text();
    const payload = text.length > 0 ? JSON.parse(text) : {};
    if (!response.ok || payload.value?.error) {
      throw new Error(`${method} ${path} failed: ${JSON.stringify(payload)}`);
    }
    return payload.value;
  }

  async find(using, value) {
    const element = await this.command("/element", "POST", { using, value });
    return element[ELEMENT_KEY];
  }

  async findAll(using, value) {
    const elements = await this.command("/elements", "POST", { using, value });
    return elements.map((element) => element[ELEMENT_KEY]);
  }

  async click(elementId) {
    await this.command(`/element/${elementId}/click`, "POST", {});
  }

  async text(elementId) {
    return this.command(`/element/${elementId}/text`);
  }

  async type(elementId, text) {
    await this.command(`/element/${elementId}/value`, "POST", {
      text,
      value: [...text],
    });
  }

  async execute(script, args = []) {
    return this.command("/execute/sync", "POST", { script, args });
  }

  async quit() {
    await fetch(`${this.baseUrl}/session/${this.sessionId}`, {
    method: "DELETE",
    signal: AbortSignal.timeout(10_000),
  }).catch(() => undefined);
  }
}

function cleanDesktopEnv(overrides) {
  const env = { ...process.env };
  for (const key of Object.keys(env)) {
    if (
      key.startsWith("SNAP") ||
      key.startsWith("GTK") ||
      key.startsWith("GDK_") ||
      key.startsWith("GIO_") ||
      key === "LOCPATH" ||
      key === "XDG_DATA_DIRS"
    ) {
      delete env[key];
    }
  }
  return {
    ...env,
    GDK_BACKEND: "x11",
    XDG_DATA_DIRS: "/usr/share",
    WEBKIT_DISABLE_COMPOSITING_MODE: "1",
    ...overrides,
  };
}

export async function startTauriDriver({ port, nativePort, nativeDriver, env = {}, cwd }) {
  const args = ["--port", String(port)];
  if (nativePort) {
    args.push("--native-port", String(nativePort));
  }
  if (nativeDriver) {
    args.push("--native-driver", nativeDriver);
  }
  const child = spawn(resolveDriverBinary(), args, {
    stdio: ["ignore", "pipe", "pipe"],
    cwd,
    env: cleanDesktopEnv(env),
    detached: true,
  });
  let output = "";
  child.stdout.on("data", (chunk) => {
    output += chunk;
  });
  child.stderr.on("data", (chunk) => {
    output += chunk;
  });
  const driverUrl = `http://127.0.0.1:${port}`;
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`tauri-driver exited early:\n${output}`);
    }
    try {
      const response = await fetch(`${driverUrl}/status`);
      if (response.ok) {
        return {
          child,
          driverUrl,
          output: () => output,
          stop: () => {
            if (child.exitCode === null && child.pid) {
              try {
                process.kill(-child.pid, "SIGKILL");
              } catch {
                child.kill();
              }
            }
          },
        };
      }
    } catch {
      // Driver is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  if (child.pid) {
    try {
      process.kill(-child.pid, "SIGKILL");
    } catch {
      child.kill();
    }
  }
  throw new Error(`tauri-driver did not start:\n${output}`);
}

export async function waitFor(check, { timeout = 20_000, interval = 200, label = "condition" } = {}) {
  const deadline = Date.now() + timeout;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const value = await check();
      if (value) return value;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
  throw new Error(`timed out waiting for ${label}${lastError ? `: ${lastError}` : ""}`);
}
