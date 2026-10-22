import { expect, test } from "@playwright/test";

const prompt = "Summarize the workspace";

async function clickMenu(
  page: import("@playwright/test").Page,
  menu: string,
  item: string,
) {
  await page
    .getByRole("menubar")
    .getByRole("menuitem", { name: menu, exact: true })
    .click();
  await page
    .getByRole("menu", { name: `${menu} menu` })
    .getByRole("menuitem", { name: item })
    .click();
}

async function openSettings(page: import("@playwright/test").Page) {
  await clickMenu(page, "File", "Settings…");
  await expect(page.getByRole("dialog", { name: "Settings" })).toBeVisible();
}

async function openContext(
  page: import("@playwright/test").Page,
  tab: "Workspace" | "Memory" | "Checkpoints" | "Diagnostics",
) {
  await clickMenu(page, "View", tab);
  await expect(
    page.getByRole("complementary", { name: "Project workspace" }),
  ).toBeVisible();
}

async function sendPrompt(page: import("@playwright/test").Page, text = prompt) {
  await page.getByRole("textbox", { name: /Ask OpenBaD/ }).fill(text);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/cached \(/)).toBeVisible();
}

test("streams reasoning, tool progress, and the answer", async ({ page }) => {
  await page.goto("/");

  await sendPrompt(page);

  await expect(page.locator(".chat-markdown")).toContainText("I read");
  await expect(page.locator(".chat-markdown")).toContainText("Cargo.toml");
  await expect(page.getByText("Reasoning")).toBeVisible();
  await expect(page.getByText("2 tool events")).toBeVisible();
  await expect(page.getByText("Done · 2 steps")).toBeVisible();
  await expect(
    page.getByTitle("Cumulative tokens and cost for this session"),
  ).toBeVisible();
  await expect(page.getByText(prompt, { exact: true }).first()).toBeVisible();
});

test("requests and grants access outside the workspace", async ({ page }) => {
  await page.goto("/");
  await sendPrompt(page, "I need access to an external folder");

  const dialog = page.getByRole("dialog", { name: "Filesystem access request" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("/home/you/other-project")).toBeVisible();
  await expect(dialog.getByText("Compare the implementation")).toBeVisible();
  await dialog.getByRole("button", { name: "Grant access" }).click();
  await expect(page.getByText(/Access granted to/)).toBeVisible();
  await expect(
    page.getByText(
      "Access granted for /home/you/other-project. Continue with the task.",
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: /Ask OpenBaD/ }),
  ).toHaveValue("");

  await openSettings(page);
  const settings = page.getByRole("dialog", { name: "Settings" });
  await settings.getByRole("button", { name: "Access", exact: true }).click();
  await expect(settings.getByText("/home/you/other-project")).toBeVisible();
  await settings
    .getByRole("button", { name: "Revoke access to /home/you/other-project" })
    .click();
  await expect(settings.getByText("No external filesystem grants.")).toBeVisible();
});

test("renders rich markdown and mermaid diagrams", async ({ page }) => {
  await page.goto("/");
  await sendPrompt(page, "Show markdown and mermaid");

  await expect(page.getByRole("heading", { name: "Markdown features" })).toBeVisible();
  await expect(page.getByRole("table")).toBeVisible();
  await expect(page.getByText("Blockquotes work too.")).toBeVisible();
  await expect(page.getByText("inline code")).toBeVisible();
  await expect(page.getByText("fn main()", { exact: false })).toBeVisible();
  await expect(page.locator(".katex").first()).toBeVisible();
  await expect(page.locator(".mermaid-diagram svg")).toBeVisible({ timeout: 15_000 });
});

test("follows streamed output unless the user scrolls away", async ({ page }) => {
  await page.setViewportSize({ width: 1100, height: 500 });
  await page.goto("/");

  await sendPrompt(page);

  const scroller = page.getByTestId("chat-scroll");
  await expect
    .poll(async () =>
      scroller.evaluate(
        (element) => element.scrollHeight - element.scrollTop - element.clientHeight,
      ),
    )
    .toBeLessThan(8);
});

test("stops an in-flight turn", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("textbox", { name: /Ask OpenBaD/ }).fill(prompt);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("button", { name: "Stop" })).toBeVisible();
  await page.getByRole("button", { name: "Stop" }).click();

  await expect(page.getByText(/agent turn was cancelled/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Stop" })).toHaveCount(0);
});

test("copies the assistant response from the message header", async ({
  page,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/");
  await sendPrompt(page);

  const copy = page.getByRole("button", { name: "Copy response" });
  await expect(copy).toHaveCount(2);
  await expect(copy.first()).toBeVisible();
  await expect(copy.last()).toBeVisible();
  await copy.last().click();
  await expect(page.getByRole("button", { name: "Copied" }).last()).toBeVisible();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toContain("I read");
});

test("copies a user message", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/");
  await sendPrompt(page);

  const copy = page.getByRole("button", { name: "Copy message" });
  await expect(copy).toBeVisible();
  await copy.click();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toBe(prompt);
});

test("steers a running turn", async ({ page }) => {
  await page.goto("/");
  const composer = page.getByRole("textbox", { name: /Ask OpenBaD/ });
  await composer.fill("Summarize the workspace slowly");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("button", { name: "Stop" })).toBeVisible();

  await composer.fill("Focus on the crates, not the app.");
  await page.getByRole("button", { name: "Steer current turn" }).click();

  await expect(page.getByText("Steering", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Focus on the crates, not the app.", { exact: false }),
  ).toBeVisible();
  await expect(composer).toHaveValue("");
});

test("queues a message until the running turn finishes", async ({ page }) => {
  await page.goto("/");
  const composer = page.getByRole("textbox", { name: /Ask OpenBaD/ });
  await composer.fill("Summarize the workspace slowly");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("button", { name: "Stop" })).toBeVisible();

  await composer.fill("Then list the crates.");
  await page.getByRole("button", { name: "Queue message" }).click();
  await expect(page.getByText(/Queued · 1/)).toBeVisible();
  await expect(composer).toHaveValue("");

  await expect(
    page.getByText("Then list the crates.", { exact: true }),
  ).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/Queued · 1/)).toHaveCount(0);
});

test("remembers model preferences", async ({ page }) => {
  await page.goto("/");
  await openSettings(page);

  await page.getByRole("checkbox", { name: "Enable thinking" }).uncheck();
  await page.getByRole("combobox", { name: "Model route" }).selectOption("deepseek-v4-pro");
  await page.getByRole("button", { name: "Close settings" }).click();
  await page.reload();
  await openSettings(page);

  await expect(page.getByRole("checkbox", { name: "Enable thinking" })).not.toBeChecked();
  await expect(page.getByRole("combobox", { name: "Model route" })).toHaveValue(
    "deepseek-v4-pro",
  );
});

test("keeps access mode sticky per session", async ({ page }) => {
  await page.goto("/");
  const access = page.getByRole("combobox", { name: "Access mode" });
  await expect(access).toHaveValue("read");

  await sendPrompt(page);
  await access.selectOption("full");
  await expect(access).toHaveValue("full");

  await page.getByRole("button", { name: "New session" }).click();
  await expect(access).toHaveValue("read");

  await page.locator("aside").getByText(prompt, { exact: true }).click();
  await expect(access).toHaveValue("full");
});

test("searches recent sessions by message text", async ({ page }) => {
  await page.goto("/");
  await sendPrompt(page);

  const sidebar = page.locator("aside");
  await expect(sidebar.getByText(prompt, { exact: true })).toBeVisible();

  await page.getByRole("searchbox", { name: "Search sessions" }).fill("Cargo.toml");
  await expect(sidebar.getByText(prompt, { exact: true })).toBeVisible();

  await page.getByRole("searchbox", { name: "Search sessions" }).fill("no such session");
  await expect(page.getByText("No matches")).toBeVisible();
});

test("renames a session inline", async ({ page }) => {
  await page.goto("/");
  await sendPrompt(page);

  const row = page.locator("aside").getByText(prompt, { exact: true });
  await row.hover();
  await page.getByRole("button", { name: "Rename session" }).click();
  const input = page.getByRole("textbox", { name: "Session title" });
  await input.fill("Workspace summary");
  await input.press("Enter");

  await expect(page.locator("aside").getByText("Workspace summary", { exact: true })).toBeVisible();
});

test("archives and restores a session", async ({ page }) => {
  await page.goto("/");
  await sendPrompt(page);

  await page.locator("aside").getByText(prompt, { exact: true }).hover();
  await page.getByRole("button", { name: "Archive session" }).click();
  await expect(page.locator("aside").getByText("Archived (1)", { exact: true })).toBeVisible();
  await expect(page.locator("aside").getByText(prompt, { exact: true })).toHaveCount(0);

  await page.getByRole("button", { name: "Archived (1)" }).click();
  const archived = page.locator("aside").getByText(prompt, { exact: true });
  await expect(archived).toBeVisible();
  await archived.hover();
  await page.getByRole("button", { name: "Restore session" }).click();
  await expect(page.locator("aside").getByText("Archived (1)", { exact: true })).toHaveCount(0);
});

test("deletes a session", async ({ page }) => {
  await page.goto("/");
  await sendPrompt(page);

  page.once("dialog", (dialog) => dialog.accept());
  await page.locator("aside").getByText(prompt, { exact: true }).hover();
  await page.getByRole("button", { name: "Delete session" }).click();
  await expect(page.locator("aside").getByText(prompt, { exact: true })).toHaveCount(0);
});

test("creates a project workspace from a name", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("button", { name: "New project" }).click();
  const dialog = page.getByRole("dialog", { name: "New project" });
  await dialog.getByRole("textbox", { name: "Project name" }).fill("Second");
  await expect(
    dialog.getByText("/home/you/OpenBaD/workspaces/second"),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "Create project" }).click();

  await expect(page.locator("aside").getByText("Second", { exact: true })).toBeVisible();
});

test("lists and restores a file checkpoint", async ({ page }) => {
  await page.goto("/");
  await sendPrompt(page);
  await openContext(page, "Checkpoints");

  await expect(page.getByText("/mnt/disk4/repos/OpenBaD/src/main.rs")).toBeVisible();
  await page.getByRole("button", { name: "Diff" }).click();
  await expect(page.getByText("+new content")).toBeVisible();

  await page.getByRole("button", { name: "Restore" }).click();

  await expect(page.getByText(/Restored /)).toBeVisible();
  await expect(page.getByText("No checkpoints yet.")).toBeVisible();
});

test("lists and forgets workspace memories", async ({ page }) => {
  await page.goto("/");
  await openContext(page, "Memory");

  await expect(page.getByText("Keep the model-visible prompt prefix immutable.")).toBeVisible();
  await expect(page.getByText(/project · agent/)).toBeVisible();
  await page.getByRole("button", { name: "Forget memory" }).click();
  await expect(page.getByText("No saved memories yet.")).toBeVisible();
});

test("does not expose a fixed step budget", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Max steps")).toHaveCount(0);
});

test("removes a project from settings", async ({ page }) => {
  await page.goto("/");
  page.on("dialog", (dialog) => void dialog.accept());

  await openSettings(page);
  await page
    .getByRole("dialog", { name: "Settings" })
    .getByRole("button", { name: "Project", exact: true })
    .click();
  await page.getByRole("button", { name: "Remove project" }).click();

  await expect(page.getByText("No project")).toBeVisible();
});

test("opens the command menu and navigates", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "New session" }).first().waitFor();
  await page.keyboard.press("Control+k");

  const menu = page.getByRole("dialog", { name: "Command menu" });
  await expect(menu).toBeVisible();
  await menu.getByRole("textbox", { name: "Command search" }).fill("memory");
  await menu.getByRole("button", { name: "Project: Memory" }).click();

  await expect(page.getByText("Keep the model-visible prompt prefix immutable.")).toBeVisible();
});

test("keeps project knowledge and retrieval in the split workspace", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Knowledge" }).first().click();

  await expect(page.getByText("verified", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /^Product thesis note/ }).click();
  await expect(
    page.getByText("OpenBaD is a desktop coding harness."),
  ).toBeVisible();

  await page.getByRole("button", { name: "Retrieval" }).click();
  await page.getByLabel("Retrieval query").fill("desktop");
  await page.getByRole("button", { name: "Search", exact: true }).click();

  await expect(page.getByText("Product thesis")).toBeVisible();
  await expect(page.getByText("apps/desktop/ui/src/App.tsx").first()).toBeVisible();
});

test("downloads knowledge and prepares a printable PDF view", async ({ page }) => {
  await page.addInitScript(() => {
    const state = { printed: 0, text: "" };
    (window as unknown as { __printState: typeof state }).__printState = state;
    window.print = () => {
      state.printed += 1;
      state.text = document.querySelector(".knowledge-print")?.textContent ?? "";
    };
  });
  await page.goto("/");
  await clickMenu(page, "View", "Knowledge");
  await expect(
    page.getByRole("complementary", { name: "Project workspace" }),
  ).toBeVisible();

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download Product thesis" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("product-thesis.md");

  await page.getByRole("button", { name: "Export Product thesis as PDF" }).click();
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as unknown as { __printState: { printed: number } }).__printState
            .printed,
      ),
    )
    .toBe(1);
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as unknown as { __printState: { text: string } }).__printState
            .text,
      ),
    )
    .toContain("Product thesis");
});

test("opens assistant-presented artifacts beside the chat", async ({ page }) => {
  await page.goto("/");
  await page
    .getByPlaceholder("Ask OpenBaD to inspect, explain, or change the workspace…")
    .fill("preview the architecture web page");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(
    page.getByRole("complementary", { name: "Project workspace" }),
  ).toBeVisible();
  await expect(page.getByText("OpenBaD architecture", { exact: true })).toBeVisible();
  await expect(page.getByTitle("OpenBaD architecture")).toBeVisible();
});

test("adds a reference folder with the picker", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "References" }).first().click();
  await page.getByRole("button", { name: "Add reference folder…" }).click();

  await expect(page.getByText("reference-library", { exact: true })).toBeVisible();
  await expect(
    page.getByText("/mnt/disk4/repos/reference-library"),
  ).toBeVisible();

  await page.getByRole("button", { name: "Remove reference-library" }).click();
  await expect(page.getByText("reference-library", { exact: true })).toHaveCount(0);
});

test("opens workspace files in the split pane", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Workspace" }).first().click();
  await page.getByRole("button", { name: "apps/desktop/ui/src/App.tsx" }).click();

  await expect(page.getByText("Mock preview for the browser harness.")).toBeVisible();
});


test("uses the application menu to open project areas and help", async ({
  page,
}) => {
  await page.goto("/");
  await clickMenu(page, "View", "Overview");
  await expect(page.getByText(/2 recipes/)).toBeVisible();

  await clickMenu(page, "View", "Knowledge");

  await expect(
    page.getByRole("complementary", { name: "Project workspace" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /^Product thesis note/ })).toBeVisible();

  await clickMenu(page, "Help", "Keyboard Shortcuts");
  await expect(
    page.getByRole("dialog", { name: "Keyboard shortcuts" }),
  ).toBeVisible();
});


test("refreshes open project areas without switching tabs", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Workspace" }).first().click();
  await expect(
    page.getByRole("button", { name: "apps/desktop/ui/src/App.tsx" }),
  ).toBeVisible();

  await page
    .getByPlaceholder("Ask OpenBaD to inspect, explain, or change the workspace…")
    .fill("add a file to the workspace");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(
    page.getByRole("button", { name: "src/generated.rs" }),
  ).toBeVisible({ timeout: 8_000 });
});

test("shows durable project todos in the split panel", async ({ page }) => {
  await page.goto("/");
  await clickMenu(page, "View", "Todos");

  await expect(
    page.getByText("Verify the project panel refresh behavior"),
  ).toBeVisible();
  await expect(
    page.getByText("Done when: A file added by the assistant appears without switching tabs"),
  ).toBeVisible();
});

test("answers a structured project question", async ({ page }) => {
  await page.goto("/");
  await clickMenu(page, "View", "Questions");

  await expect(
    page.getByText("Should project knowledge be exported to Markdown?"),
  ).toBeVisible();
  await page.getByRole("button", { name: "Yes, git-backed" }).click();
  await expect(page.getByText("Yes, git-backed")).toBeVisible();
  await expect(page.getByText("answered")).toBeVisible();
});

test("approves a draft project plan", async ({ page }) => {
  await page.goto("/");
  await clickMenu(page, "View", "Plan");

  await expect(
    page.getByText("Ship the project intelligence tranche"),
  ).toBeVisible();
  await expect(page.getByText("1/3 steps complete")).toBeVisible();
  await page.getByRole("button", { name: "Approve plan" }).click();
  await expect(page.getByText("approved")).toBeVisible();
});

test("shows counts for project areas without opening them", async ({ page }) => {
  await page.goto("/");

  for (const label of ["Workspace", "Agents", "References", "Knowledge", "Memory", "Todos", "Questions", "Plan"]) {
    await expect(
      page.getByRole("button", { name: new RegExp(`^${label} \\(`) }),
    ).toBeVisible();
  }
});

test("shows a running agent count without opening the panel", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: /^Agents \(0\)/ })).toBeVisible();

  await sendPrompt(page, "Spawn a subagent to inspect the repository");

  await expect(page.getByRole("button", { name: /^Agents \(1\)/ })).toBeVisible();
  await expect(page.getByLabel("Background work running")).toBeVisible();
});

test("shows background agents in the project area", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Agents" }).first().click();

  const panel = page.getByRole("complementary", { name: "Project workspace" });
  await expect(panel).toBeVisible();
  await expect(panel.getByText("No subagents yet")).toBeVisible();
  await expect(panel.getByText(/subagent/).first()).toBeVisible();
});

async function dropDocument(
  page: import("@playwright/test").Page,
  target: import("@playwright/test").Locator,
  name: string,
  content: string,
) {
  const dataTransfer = await page.evaluateHandle(
    ({ name, content }) => {
      const transfer = new DataTransfer();
      transfer.items.add(new File([content], name, { type: "text/markdown" }));
      return transfer;
    },
    { name, content },
  );
  await target.dispatchEvent("dragenter", { dataTransfer });
  await target.dispatchEvent("dragover", { dataTransfer });
  await target.dispatchEvent("drop", { dataTransfer });
}

test("attaches a dropped document to the next chat message", async ({ page }) => {
  await page.goto("/");
  const composer = page.getByRole("textbox", { name: /Ask OpenBaD/ });

  await dropDocument(page, composer, "design.md", "# Design notes");
  await expect(
    page.getByRole("button", { name: "Remove attachment design.md" }),
  ).toBeVisible();

  await composer.fill("Summarize the attachment");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByText(/Attached documents: design.md/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Remove attachment design.md" }),
  ).toHaveCount(0);
});

test("uploads dropped documents to the workspace", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Workspace" }).first().click();
  const panel = page.getByRole("complementary", { name: "Project workspace" });

  await dropDocument(
    page,
    panel.getByRole("button", { name: "Upload documents…" }),
    "release-notes.md",
    "# Release notes",
  );

  await expect(panel.getByText(/Uploaded 1 file/)).toBeVisible();
  await expect(panel.getByRole("button", { name: "release-notes.md" })).toBeVisible();
});

test("uploads dropped documents to references", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "References" }).first().click();
  const panel = page.getByRole("complementary", { name: "Project workspace" });

  await dropDocument(
    page,
    panel.getByRole("button", { name: "Upload documents…" }),
    "handbook.md",
    "# Handbook",
  );

  await expect(panel.getByText("Uploaded documents", { exact: true })).toBeVisible();
  await expect(panel.getByText(/Uploaded 1 file/)).toBeVisible();
});

test("uploads dropped documents to knowledge", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Knowledge" }).first().click();
  const panel = page.getByRole("complementary", { name: "Project workspace" });

  await dropDocument(
    page,
    panel.getByRole("button", { name: "Upload", exact: true }),
    "onboarding.md",
    "# Onboarding",
  );

  await expect(panel.getByText(/Imported 1 document/)).toBeVisible();
  await expect(panel.getByRole("button", { name: /^onboarding/ })).toBeVisible();
});

test("attaches a document with the file picker", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Attach documents" }).click();

  await expect(
    page.getByRole("button", { name: /^Remove attachment upload-\d+\.md$/ }),
  ).toBeVisible();
});

test("uploads a document with the workspace picker", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Workspace" }).first().click();
  const panel = page.getByRole("complementary", { name: "Project workspace" });

  await panel.getByRole("button", { name: "Upload documents…" }).click();

  await expect(panel.getByText(/Uploaded 1 file/)).toBeVisible();
  await expect(panel.getByRole("button", { name: /upload-\d+\.md/ })).toBeVisible();
});

test("typing in the composer does not re-render the transcript", async ({ page }) => {
  await page.goto("/");
  await sendPrompt(page, "Show markdown and mermaid");
  await expect(page.locator(".mermaid-diagram svg")).toBeVisible({ timeout: 15_000 });

  await page.evaluate(() => {
    const transcript = document.querySelector('[data-testid="chat-scroll"]');
    const state = window as unknown as {
      __transcriptMutations: number;
      __transcriptObserver: MutationObserver;
    };
    state.__transcriptMutations = 0;
    state.__transcriptObserver = new MutationObserver((records) => {
      state.__transcriptMutations += records.length;
    });
    state.__transcriptObserver.observe(transcript as Node, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
    });
  });

  await page
    .getByRole("textbox", { name: /Ask OpenBaD/ })
    .pressSequentially("drafting a longer prompt to exercise typing", { delay: 10 });

  const mutations = await page.evaluate(() => {
    const state = window as unknown as {
      __transcriptMutations: number;
      __transcriptObserver: MutationObserver;
    };
    state.__transcriptObserver.disconnect();
    return state.__transcriptMutations;
  });
  expect(mutations).toBe(0);
});

test("attaches documents dropped anywhere in the chat column", async ({ page }) => {
  await page.goto("/");

  await dropDocument(
    page,
    page.getByTestId("chat-scroll"),
    "outside-composer.md",
    "# Dropped outside the composer",
  );

  await expect(
    page.getByRole("button", { name: "Remove attachment outside-composer.md" }),
  ).toBeVisible();
});
