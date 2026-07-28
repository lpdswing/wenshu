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
  const composer = page.getByPlaceholder("告诉文枢你想完成什么...");

  for (const suggestion of CONTENT_SUGGESTIONS) {
    await page.getByText(suggestion, { exact: true }).click();
    await expect(composer).toHaveValue(suggestion);
  }

  await expect(page.getByText(/created|published|success/i)).toHaveCount(0);
});

test("secondary setup preserves the add-folder prerequisite flow", async ({ page }) => {
  await page.goto("/");

  await page.getByTestId("intro-task-folder").click();
  const path = page.getByPlaceholder("选择或粘贴文件夹路径…");
  await expect(path).toBeVisible();
  await path.fill("/Users/me/Reports");
  await page.getByRole("button", { name: "添加", exact: true }).click();

  await expect(page.getByPlaceholder("告诉文枢你想完成什么...")).toHaveValue(
    "分析这个文件夹中的文件并总结重点。",
  );
});

test("secondary disconnected source action still opens session access", async ({ page }) => {
  await page.goto("/");

  const hubspot = page.getByTestId("intro-task-hubspot");
  await expect(hubspot).toContainText("配置 ›");
  await hubspot.click();

  await expect(page.getByRole("region", { name: "会话访问范围" })).toBeVisible();
  await expect(page.getByPlaceholder("告诉文枢你想完成什么...")).toHaveValue("");
});
