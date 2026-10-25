import { expect, test } from "@playwright/test";

const mockWorkspace = "/mnt/disk4/repos/OpenBaD";
const mockReference = "/mnt/disk4/repos/vscode";

async function clickView(page: import("@playwright/test").Page, item: string) {
  await page
    .getByRole("menubar")
    .getByRole("menuitem", { name: "View", exact: true })
    .click();
  await page
    .getByRole("menu", { name: "View menu" })
    .getByRole("menuitem", { name: item })
    .click();
}

test("lists source control in the left tree and opens a repo in the preview", async ({
  page,
}) => {
  await page.goto("/");
  const sidebar = page.locator("aside").first();
  const toggle = sidebar.getByRole("button", { name: /^Source Control/ });
  await expect(toggle).toHaveAttribute("aria-expanded", "true");

  const workspaceRow = sidebar.locator(`button[title="${mockWorkspace}"]`);
  await expect(workspaceRow).toBeVisible();
  await expect(workspaceRow).toContainText("workspace");
  await expect(workspaceRow).toContainText("main");
  await expect(workspaceRow).toContainText("clean");

  const referenceRow = sidebar.locator(`button[title="${mockReference}"]`);
  await expect(referenceRow).toBeVisible();
  await expect(referenceRow).toContainText("vscode");
  await expect(referenceRow).toContainText("reference");

  await referenceRow.click();
  const preview = page.getByTestId("preview-pane");
  await expect(preview).toHaveAttribute("data-preview-kind", "git");
  await expect(preview).toContainText("vscode");
  await expect(preview).toContainText(`source control · ${mockReference}`);
  await expect(
    page.getByRole("complementary", { name: "Project workspace" }),
  ).toHaveCount(0);

  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(referenceRow).toHaveCount(0);
});

test("removes source control from the right project panel", async ({ page }) => {
  await page.goto("/");
  await page
    .getByRole("menubar")
    .getByRole("menuitem", { name: "View", exact: true })
    .click();
  const viewMenu = page.getByRole("menu", { name: "View menu" });
  await expect(viewMenu.getByRole("menuitem", { name: "Memory" })).toBeVisible();
  await expect(
    viewMenu.getByRole("menuitem", { name: "Source Control" }),
  ).toHaveCount(0);
  await viewMenu.getByRole("menuitem", { name: "Overview" }).click();

  const tabs = page.getByRole("tablist", { name: "Project areas" });
  await expect(tabs.getByRole("tab", { name: "Memory" })).toBeVisible();
  await expect(tabs.getByRole("tab", { name: "Source Control" })).toHaveCount(0);
  await expect(tabs.getByRole("tab", { name: "Diag" })).toBeVisible();
});

test("keeps the tab-strip scrollbar clear of labels at narrow widths", async ({
  page,
}) => {
  await page.setViewportSize({ width: 900, height: 700 });
  await page.goto("/");
  await clickView(page, "Workspace");

  // Drag the panel down to its 320px minimum width, where the strip must
  // scroll horizontally and the scrollbar used to sit on the tab labels.
  const panel = page.getByRole("complementary", { name: "Project workspace" });
  const handle = page.getByRole("separator", { name: "Resize project panel" });
  const box = await handle.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + 40);
  await page.mouse.down();
  await page.mouse.move(box!.x + box!.width / 2 + 200, box!.y + 40, { steps: 5 });
  await page.mouse.up();
  const panelWidth = await panel.evaluate(
    (element) => element.getBoundingClientRect().width,
  );
  expect(panelWidth).toBeGreaterThanOrEqual(320);
  expect(panelWidth).toBeLessThanOrEqual(420);

  const strip = page.locator(".panel-tab-strip");
  await expect(strip).toBeVisible();
  const metrics = await strip.evaluate((element) => {
    const style = window.getComputedStyle(element);
    const row = element.querySelector(".panel-tab-row");
    if (row === null) {
      return null;
    }
    const stripRect = element.getBoundingClientRect();
    const rowRect = row.getBoundingClientRect();
    const borderTop = Number.parseFloat(style.borderTopWidth) || 0;
    return {
      paddingBottom: Number.parseFloat(style.paddingBottom) || 0,
      labelGap: element.clientHeight - (rowRect.bottom - stripRect.top - borderTop),
      overflows: element.scrollWidth > element.clientWidth,
    };
  });
  expect(metrics).not.toBeNull();
  expect(metrics?.overflows).toBe(true);
  expect(metrics?.paddingBottom).toBeGreaterThanOrEqual(18);
  // Classic scrollbars consume part of the bottom padding; overlay scrollbars
  // do not. Either way the labels keep a visible gap above the scrollbar.
  expect(metrics?.labelGap).toBeGreaterThanOrEqual(8);

  const tabs = page.getByRole("tablist", { name: "Project areas" });
  await tabs.getByRole("tab", { name: "Diag" }).click();
  await expect(
    page.getByRole("complementary", { name: "Project workspace" }),
  ).toContainText("Diagnostics");
});
