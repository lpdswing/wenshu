import { expect, openAccountPage, test } from "./fixtures";

// Automation runs open as live sessions — which used to look like any other chat with no way
// back (owner report, 2026-07-04). Guards: the run-session banner (task title + automation
// context) and "← Back to runs" returning to the task's detail page.
test("scheduled run session shows the run banner; Back returns to the task detail", async ({
  page,
}) => {
  await openAccountPage(page, "automations");

  // Task list → detail (runs list).
  await page.getByText("Daily AI News").first().click();
  await expect(page.getByRole("button", { name: /Run now/ })).toBeVisible();
  await expect(page.getByText("Each run is a live conversation", { exact: false })).toBeVisible();

  // Open the running run: a normal session view, but with the automation-context banner.
  await page.getByTitle("Open this run's conversation").click();
  const banner = page.getByTestId("run-banner");
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("自动化执行");
  await expect(banner).toContainText("Daily AI News");

  // Back link lands on the SAME task's detail, not the bare list.
  await banner.getByRole("button", { name: "← 返回执行记录" }).click();
  await expect(page.getByRole("button", { name: /Run now/ })).toBeVisible();
  await expect(page.getByText("Daily AI News").first()).toBeVisible();

  // A plain (non-run) session never shows the banner.
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByTestId("run-banner")).toHaveCount(0);
});
