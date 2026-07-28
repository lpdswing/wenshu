import { expect } from "@playwright/test";
import { test } from "./fixtures";

const CONTENT_SUGGESTIONS = [
  "整理这些资料，先生成一版文章草稿。",
  "审阅文章后，为它规划封面和正文配图。",
  "把确认后的文章整理成公众号草稿。",
];

test("fresh cowork session recommends the three Wenshu content workflows", async ({ page }) => {
  await page.goto("/");
  const intro = page.locator(".intro");

  await expect(intro.locator(".task-card")).toHaveCount(3);
  for (const suggestion of CONTENT_SUGGESTIONS) {
    await expect(intro.getByText(suggestion, { exact: true })).toBeVisible();
  }

  await expect(intro.getByText(/HubSpot|GitHub|Slack/)).toHaveCount(0);
  await expect(intro.getByText(/Configure/)).toHaveCount(0);
});

test("content recommendations prefill natural-language requests without claiming success", async ({
  page,
}) => {
  await page.goto("/");
  const composer = page.getByPlaceholder(/Ask the coworker/);

  for (const suggestion of CONTENT_SUGGESTIONS) {
    await page.getByText(suggestion, { exact: true }).click();
    await expect(composer).toHaveValue(suggestion);
  }

  await expect(page.getByText(/created|published|success/i)).toHaveCount(0);
});
