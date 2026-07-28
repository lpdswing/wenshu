// Left-nav polish (§20): collapse (⌘B / brand button → reveal button docks it back) and the
// RECENT-header group/filter popover (Group by Persona↔Chronological, Filter by coworker).
import { expect } from "@playwright/test";
import { wenshuTest as test } from "./fixtures";

test("collapse hides the sidebar and reclaims the width; reveal button docks it back", async ({
  page,
}) => {
  await page.goto("/");
  const app = page.locator(".app");
  await expect(page.locator(".sidebar")).toBeVisible();

  // Collapse via the brand button.
  await page.getByRole("button", { name: "收起侧边栏" }).click();
  await expect(app).toHaveClass(/nav-collapsed/);
  // The floating reveal affordance appears; clicking it docks the nav back.
  const reveal = page.getByRole("button", { name: "显示侧边栏" });
  await expect(reveal).toBeVisible();
  await reveal.click();
  await expect(app).not.toHaveClass(/nav-collapsed/);
});

test("⌘B toggles the sidebar collapse", async ({ page }) => {
  await page.goto("/");
  const app = page.locator(".app");
  await expect(page.locator(".sidebar")).toBeVisible();
  await page.keyboard.press("Meta+b");
  await expect(app).toHaveClass(/nav-collapsed/);
  await page.keyboard.press("Meta+b");
  await expect(app).not.toHaveClass(/nav-collapsed/);
});

test("RECENT header group/filter popover: switch grouping + see coworker filters", async ({
  page,
}) => {
  await page.goto("/");
  const header = page.getByTestId("recent-header");
  await expect(header).toContainText("最近");

  await header.getByRole("button", { name: "对会话分组和筛选" }).click();
  const menu = page.getByTestId("group-filter-menu");
  await expect(menu).toContainText("分组方式");
  await expect(menu).toContainText("按角色筛选");

  // Switch to Chronological → the persona accordion collapses into a flat list (the "OpenWorker"
  // persona group header is no longer a row; sessions list directly).
  await menu.getByText("按时间").click();
  await expect(menu.getByText("按时间").locator("xpath=..")).toContainText("✓");

  // Filter-by-coworker checkboxes are present (none checked by default → all shown).
  await expect(menu).toContainText("未选择时显示全部会话。");
});
