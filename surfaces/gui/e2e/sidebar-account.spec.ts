// The sidebar bottom is exactly ONE row — the account anchor (UX-DECISIONS §26).
// Contract under test: no "Settings & more", no standalone Inbox/Connectors rows; the
// inbox chip is state-driven (accent + count when pending) and clicks STRAIGHT to Inbox
// while the rest of the row opens the account menu, which always lists Inbox + Connectors.
import { expect } from "@playwright/test";
import { accountMenuItem, test } from "./fixtures";

test("the bottom is one account row — the old rows are gone", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("account-row")).toBeVisible();
  await expect(page.getByRole("button", { name: /设置与更多/i })).toHaveCount(0);
  // No standalone sidebar Inbox row: outside the menu, "收件箱" exists only as the chip.
  await expect(page.locator(".sidebar").getByRole("button", { name: "收件箱", exact: true })).toHaveCount(0);
});

test("pending items: the chip carries the count and goes straight to Inbox — no menu", async ({
  page,
}) => {
  await page.goto("/");
  const chip = page.getByTestId("inbox-chip");
  await expect(chip).toContainText(/\d/); // fixtures seed pending attention → accent count
  await chip.click();
  await expect(page.getByTestId("account-menu")).toHaveCount(0); // the chip never opens the menu
  await expect(page.getByText("Approve: run_shell")).toBeVisible(); // Inbox opened directly
});

test("the account menu: Inbox + Connectors always listed; Settings carries the shortcut hint", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await expect(accountMenuItem(page, "inbox")).toBeVisible();
  await expect(accountMenuItem(page, "connectors")).toBeVisible();
  await expect(accountMenuItem(page, "settings")).toContainText("⌘");
  await expect(accountMenuItem(page, "automations")).toBeVisible();
  await expect(accountMenuItem(page, "activity")).toBeVisible();
});

test("Activity in the menu is the audit log; Unrouted lives under Inbox ▸ Configure", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await accountMenuItem(page, "activity").click();
  await expect(page.getByRole("heading", { name: "Activity" })).toBeVisible();

  // §28: Messaging routing left the Connectors sub-nav entirely (Connectors · MCP only)…
  await page.getByTestId("account-row").click();
  await accountMenuItem(page, "connectors").click();
  await expect(page.getByRole("button", { name: "MCP servers" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Messaging routing/ })).toHaveCount(0);
  // The old fourth sub-nav tab is gone — exactly one page is named Activity now.
  await expect(page.getByRole("button", { name: "Activity", exact: true })).toHaveCount(0);

  // …and Unrouted rides the Inbox's Configure tab.
  await page.getByTestId("account-row").click();
  await accountMenuItem(page, "inbox").click();
  await page.getByTestId("inbox-tab-configure").click();
  await expect(page.getByTestId("unrouted-section")).toBeVisible();
});
