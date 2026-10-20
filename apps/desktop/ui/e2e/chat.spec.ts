import { expect, test } from "@playwright/test";

const prompt = "Summarize the workspace";

test("streams reasoning, tool progress, and the answer", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("textbox", { name: /Ask OpenBaD/ }).fill(prompt);
  await page.getByRole("button", { name: "Send" }).click();

  const assistant = page.locator("article").filter({ hasText: "assistant" });
  await expect(assistant).toContainText("I read `Cargo.toml`");
  await expect(assistant.getByText("Thinking")).toBeVisible();
  await expect(assistant.getByText("2 tool results")).toBeVisible();
  await expect(page.getByText(/cached \(/)).toBeVisible();
  await expect(assistant).toContainText("Done · 2 steps");
  await expect(page.getByText(prompt, { exact: true }).first()).toBeVisible();
});

test("follows streamed output unless the user scrolls away", async ({ page }) => {
  await page.setViewportSize({ width: 1100, height: 500 });
  await page.goto("/");

  await page.getByRole("textbox", { name: /Ask OpenBaD/ }).fill(prompt);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/cached \(/)).toBeVisible();

  const scroller = page.locator("section .overflow-y-auto");
  await expect
    .poll(async () =>
      scroller.evaluate((element) => {
        const remaining = element.scrollHeight - element.scrollTop - element.clientHeight;
        return remaining;
      }),
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

  await page.getByRole("checkbox", { name: "Thinking" }).uncheck();
  await page.getByRole("combobox", { name: "Route" }).selectOption("deepseek-v4-pro");
  await page.locator('input[type="number"]').fill("32");
  await page.reload();

  await expect(page.getByRole("checkbox", { name: "Thinking" })).not.toBeChecked();
  await expect(page.getByRole("combobox", { name: "Route" })).toHaveValue("deepseek-v4-pro");
  await expect(page.locator('input[type="number"]')).toHaveValue("32");
});

test("searches recent sessions by message text", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("textbox", { name: /Ask OpenBaD/ }).fill(prompt);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/cached \(/)).toBeVisible();

  const sessionButton = page.locator("aside button").filter({ hasText: prompt });
  await expect(sessionButton).toBeVisible();

  await page.getByRole("searchbox", { name: "Search sessions" }).fill("Cargo.toml");
  await expect(sessionButton).toBeVisible();

  await page.getByRole("searchbox", { name: "Search sessions" }).fill("no such session");
  await expect(sessionButton).toHaveCount(0);
  await expect(page.getByText("No sessions yet.")).toBeVisible();
});

test("lists and restores a file checkpoint", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("textbox", { name: /Ask OpenBaD/ }).fill(prompt);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/cached \(/)).toBeVisible();

  await expect(page.getByText("/mnt/disk4/repos/OpenBaD/src/main.rs")).toBeVisible();
  await page.getByRole("button", { name: "Diff" }).click();
  await expect(page.getByText("+new content")).toBeVisible();

  await page.getByRole("button", { name: "Restore" }).click();

  await expect(page.getByText(/Restored /)).toBeVisible();
  await expect(page.getByText("No checkpoints yet.")).toBeVisible();
});
