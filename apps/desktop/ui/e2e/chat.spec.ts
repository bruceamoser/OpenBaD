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

  await expect(page.getByText("I read `Cargo.toml`", { exact: false })).toBeVisible();
  await expect(page.getByText("Reasoning")).toBeVisible();
  await expect(page.getByText("2 tool events")).toBeVisible();
  await expect(page.getByText("Done · 2 steps")).toBeVisible();
  await expect(
    page.getByTitle("Cumulative tokens and cost for this session"),
  ).toBeVisible();
  await expect(page.getByText(prompt, { exact: true }).first()).toBeVisible();
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
