import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { after, before, test } from "node:test";

import {
  WebDriverClient,
  freePort,
  makeFixture,
  resolveAppBinary,
  resolveWebKitDriver,
  startMockDeepSeek,
  startTauriDriver,
  waitFor,
} from "./webdriver.mjs";

const labelCount = [
  "Workspace",
  "Agents",
  "References",
  "Knowledge",
  "Retrieval",
  "Memory",
];

let fixture;
let mock;
let driver;
let client;
let driverOutput = "";

before(async () => {
  fixture = await makeFixture();
  mock = await startMockDeepSeek();
  const port = await freePort();
  const nativePort = await freePort();
  driver = await startTauriDriver({
    port,
    nativePort,
    nativeDriver: resolveWebKitDriver(),
    cwd: fixture.workspace,
    env: {
      OPENBAD_DB_PATH: fixture.dbPath,
      OPENBAD_WORKSPACES_DIR: `${fixture.root}/workspaces`,
      OPENBAD_DEEPSEEK_BASE_URL: mock.baseUrl,
      DEEPSEEK_API_KEY: "native-e2e-key",
      OPENBAD_SUBAGENT_RETENTION_SECONDS: "1",
    },
  });
  driverOutput = driver.output;
  try {
    client = await WebDriverClient.create(driver.driverUrl, resolveAppBinary());
  } catch (error) {
    throw new Error(`${error.message}\n${driverOutput()}`);
  }
});

after(async () => {
  await client?.quit().catch(() => undefined);
  driver?.stop();
  await mock?.close();
  await fixture?.cleanup();
});

test("native app shows counts, agents, chat, and copy controls", { timeout: 60_000 }, async () => {
  await waitFor(
    () => client.find("css selector", "textarea[placeholder^='Ask OpenBaD']"),
    { label: "chat composer" },
  );

  for (const label of labelCount) {
    const button = await client.find(
      "xpath",
      `//button[starts-with(normalize-space(.), '${label} (')]`,
    );
    assert.ok(button, `${label} count should be visible`);
  }

  const agents = await client.find(
    "xpath",
    "//button[starts-with(normalize-space(.), 'Agents (')]",
  );
  await waitFor(() => Promise.resolve(existsSync(fixture.dbPath)), {
    label: "isolated database",
  });
  const database = new DatabaseSync(fixture.dbPath);
  const workspace = database.prepare("SELECT id FROM workspaces LIMIT 1").get();
  assert.ok(workspace?.id, "seeded workspace");
  const now = Date.now();
  database
    .prepare(
      `INSERT INTO subagents
        (id, workspace_id, parent_session_id, title, task, model, status, log, result,
         error, steps, created_at, updated_at, finished_at)
       VALUES (?, ?, NULL, ?, ?, ?, 'running', '', '', NULL, 1, ?, ?, NULL)`,
    )
    .run(
      "native-seeded-agent",
      workspace.id,
      "Native seeded worker",
      "Verify the native Agents panel",
      "deepseek-flash",
      now,
      now,
    );

  await waitFor(
    () => client.find("xpath", "//button[starts-with(normalize-space(.), 'Agents (1)')]"),
    { label: "running agent count" },
  );
  await client.click(agents);
  await waitFor(() => client.find("css selector", "aside[aria-label='Project workspace']"), {
    label: "project panel",
  });
  await waitFor(
    () => client.find("xpath", "//*[contains(., 'Native seeded worker')]"),
    { label: "seeded agent row" },
  );

  database
    .prepare(
      `UPDATE subagents
       SET status = 'completed', result = 'done', finished_at = ?, updated_at = ?
       WHERE id = ?`,
    )
    .run(now - 5_000, now - 5_000, "native-seeded-agent");
  database.close();

  await waitFor(
    () => client.find("xpath", "//button[starts-with(normalize-space(.), 'Agents (0)')]"),
    { label: "cleaned-up agent count", timeout: 15_000 },
  );
  await waitFor(
    () => client.find("xpath", "//*[contains(., 'No subagents yet')]"),
    { label: "cleaned-up Agents panel", timeout: 15_000 },
  );

  const composer = await client.find(
    "css selector",
    "textarea[placeholder^='Ask OpenBaD']",
  );
  await client.type(composer, "Hello from the native automation");
  const send = await client.find("xpath", "//button[normalize-space(.)='Send']");
  await client.click(send);

  await waitFor(
    () => client.find("xpath", "//*[contains(., 'Hello from native E2E.')]"),
    { label: "assistant response", timeout: 30_000 },
  );

  const responseCopies = await waitFor(
    async () => {
      const buttons = await client.findAll(
        "xpath",
        "//button[@aria-label='Copy response']",
      );
      return buttons.length === 2 ? buttons : null;
    },
    { label: "top and bottom copy controls" },
  );
  const userCopies = await client.findAll(
    "xpath",
    "//button[@aria-label='Copy message']",
  );
  assert.equal(userCopies.length, 1, "user message copy control");

  await client.click(responseCopies[1]);
  await waitFor(
    () => client.find("xpath", "//button[@aria-label='Copied']"),
    { label: "copied state" },
  );
});
