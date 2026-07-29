import { expect } from "@playwright/test";
import { setMockArtifacts, wenshuTest as test } from "./fixtures";

const REVIEW_HTML = `<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <title>文枢内容流水线</title>
  </head>
  <body>
    <main>
      <article>
        <h1>文枢内容流水线</h1>
        <section aria-labelledby="review-summary">
          <h2 id="review-summary">摘要</h2>
          <p>先审文字，再生成配图。</p>
        </section>
        <section aria-labelledby="review-body">
          <h2 id="review-body">正文</h2>
          <p>文枢先整理完整文章草稿，再生成一份纯文字审阅页。</p>
          <p>只有文字内容确认后，才会请求一次性批准并生成封面与正文配图。</p>
        </section>
      </article>
    </main>
  </body>
</html>`;

test("review.html opens in the existing artifact iframe as a complete text-only review", async ({
  page,
}) => {
  setMockArtifacts(page, [
    {
      path: "review.html",
      kind: "html",
      content: REVIEW_HTML,
    },
  ]);

  await page.goto("/");
  const artifact = page.locator(".artifact-row").filter({ hasText: "review.html" });
  await expect(artifact).toBeVisible();
  await artifact.click();

  const preview = page.frameLocator("iframe.artifact-frame");
  await expect(preview.getByRole("heading", { level: 1, name: "文枢内容流水线" })).toBeVisible();
  await expect(preview.getByRole("heading", { level: 2, name: "摘要" })).toBeVisible();
  await expect(preview.getByText("先审文字，再生成配图。")).toBeVisible();
  await expect(preview.getByRole("heading", { level: 2, name: "正文" })).toBeVisible();
  await expect(preview.getByText("文枢先整理完整文章草稿，再生成一份纯文字审阅页。")).toBeVisible();
  await expect(
    preview.getByText("只有文字内容确认后，才会请求一次性批准并生成封面与正文配图。"),
  ).toBeVisible();
  await expect(preview.locator("img, script, link")).toHaveCount(0);
});
