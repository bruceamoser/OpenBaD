import { expect, test } from "@playwright/test";

const prompt = "Summarize the workspace";

async function openSettings(page: import("@playwright/test").Page) {
  await page.getByRole("button", { name: "Settings" }).first().click();
  await expect(page.getByRole("dialog", { name: "Settings" })).toBeVisible();
}

async function openContext(
  page: import("@playwright/test").Page,
  tab: "Index" | "Memory" | "Checkpoints" | "Diagnostics",
) {
  await page.getByRole("button", { name: "Context" }).click();
  await page.getByRole("button", { name: tab }).click();
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

test("adds a project", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("button", { name: "Add project" }).click();
  const dialog = page.getByRole("dialog", { name: "Add project" });
  await dialog.getByRole("textbox", { name: "Project directory" }).fill("/tmp/second-project");
  await dialog.getByRole("textbox", { name: "Project name" }).fill("Second");
  await dialog.getByRole("button", { name: "Add project" }).click();

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
  await page.getByRole("button", { name: "Project", exact: true }).click();
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
  await menu.getByRole("button", { name: "Context: Memory" }).click();

  await expect(page.getByText("Keep the model-visible prompt prefix immutable.")).toBeVisible();
});
