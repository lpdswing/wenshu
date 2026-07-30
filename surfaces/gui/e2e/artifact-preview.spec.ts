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

const WECHAT_ARTICLE_HTML = `<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <title>文枢内容流水线</title>
    <style>
      body { color: #24332d; background: #f7f4ed; }
      article { max-width: 680px; margin: 0 auto; }
      img { display: block; max-width: 100%; }
    </style>
  </head>
  <body>
    <article>
      <img src="assets/cover.png" width="680" height="340" alt="文枢内容流水线封面">
      <h1>文枢内容流水线</h1>
      <p>先审文字，再完成图文排版。</p>
      <section>
        <h2>先确认文章文字</h2>
        <p>文章文字确认后，文枢才会继续生成配图。</p>
        <img src="assets/image-1.png" width="680" height="420" alt="文字审阅流程">
      </section>
      <section>
        <h2>再保存公众号草稿</h2>
        <p>最终批准只会保存到草稿箱，不会正式发布。</p>
        <img src="assets/image-2.png" width="680" height="420" alt="公众号草稿流程">
      </section>
    </article>
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

test("article.html keeps the final WeChat article in the existing artifact iframe", async ({
  page,
}) => {
  setMockArtifacts(page, [
    {
      path: "drafts/article.html",
      name: "article.html",
      kind: "html",
      content: WECHAT_ARTICLE_HTML,
    },
  ]);

  await page.goto("/");
  const artifact = page.locator(".artifact-row").filter({ hasText: "article.html" });
  await expect(artifact).toBeVisible();
  await artifact.click();

  const preview = page.frameLocator("iframe.artifact-frame");
  await expect(
    preview.getByRole("heading", { level: 1, name: "文枢内容流水线" }),
  ).toBeVisible();
  await expect(preview.getByText("先审文字，再完成图文排版。")).toBeVisible();
  await expect(
    preview.getByRole("heading", { level: 2, name: "先确认文章文字" }),
  ).toBeVisible();
  await expect(
    preview.getByRole("heading", { level: 2, name: "再保存公众号草稿" }),
  ).toBeVisible();
  await expect(
    preview.getByText("最终批准只会保存到草稿箱，不会正式发布。"),
  ).toBeVisible();
  await expect(preview.getByRole("img")).toHaveCount(3);
  await expect(
    preview.getByRole("img", { name: "文枢内容流水线封面" }),
  ).toBeVisible();
  await expect(preview.locator("article")).toHaveCount(1);
  await expect(preview.locator("script, iframe")).toHaveCount(0);
});
