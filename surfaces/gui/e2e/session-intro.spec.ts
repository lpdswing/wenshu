import { expect } from "@playwright/test";
import { test } from "./fixtures";

const CONTENT_SUGGESTIONS = [
  "整理这些资料，先生成一版文章草稿。",
  "审阅文章后，为它规划封面和正文配图。",
  "把确认后的文章整理成公众号草稿。",
];

test("fresh cowork session recommends the three Wenshu content workflows", async ({ page }) => {
  await page.goto("/");
  const contentRecommendations = page.locator(".intro-content-recommendations");

  await expect(contentRecommendations.locator(".task-card")).toHaveCount(3);
  for (const suggestion of CONTENT_SUGGESTIONS) {
    await expect(contentRecommendations.getByText(suggestion, { exact: true })).toBeVisible();
  }
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

test("secondary setup preserves the add-folder prerequisite flow", async ({ page }) => {
  await page.goto("/");

  await page.getByTestId("intro-task-folder").click();
  const path = page.getByPlaceholder("Choose or paste a folder path…");
  await expect(path).toBeVisible();
  await path.fill("/Users/me/Reports");
  await page.getByRole("button", { name: "Add", exact: true }).click();

  await expect(page.getByPlaceholder(/Ask the coworker/)).toHaveValue(
    /Analyze the files in this folder/,
  );
});

test("secondary disconnected source action still opens session access", async ({ page }) => {
  await page.goto("/");

  const hubspot = page.getByTestId("intro-task-hubspot");
  await expect(hubspot).toContainText("Configure ›");
  await hubspot.click();

  await expect(page.getByRole("region", { name: "Session access" })).toBeVisible();
  await expect(page.getByPlaceholder(/Ask the coworker/)).toHaveValue("");
});
