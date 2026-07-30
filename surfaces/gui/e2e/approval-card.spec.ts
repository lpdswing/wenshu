// §35 (UX-018): approval cards speak the transcript's language. Routine workspace writes
// are a compact ROW (humanized title, inline args-preview, short "Always allow" with the
// full rule on hover); everything else is a full card — shell titles with the model's
// description, external actions wear the leaves-this-Mac note. No "PERMISSION REQUIRED"
// kicker, no raw args dump, no solid-fill buttons.
import { expect } from "@playwright/test";
import { wenshuTest as test } from "./fixtures";

test("article image generation → structured external approval with one-time controls", async ({
  page,
}) => {
  await page.goto("/");
  const box = page.getByPlaceholder("告诉文枢你想完成什么...");
  await box.fill("生成文章配图");
  await page.getByRole("button", { name: "发送" }).click();

  const card = page.locator(".approval").filter({ hasText: "文枢内容流水线" }).last();
  await expect(card).toBeVisible();
  await expect(card).toContainText("OpenAI · gpt-image-2");
  await expect(card).toContainText("预计生成 4 张图片");
  await expect(card).toContainText("将调用外部图片生成服务");
  await expect(card).not.toContainText(/reviewed_hash|cover_request|illustration_plan|raw (?:cover|illustration) prompt/);
  await expect(card.getByRole("button", { name: "批准并生成", exact: true })).toHaveCount(1);
  await expect(card.getByRole("button", { name: "拒绝", exact: true })).toHaveCount(1);
  await expect(card.getByRole("button", { name: "本次会话始终允许", exact: true })).toHaveCount(0);
  await expect(card.getByRole("button", { name: "此自动化始终允许", exact: true })).toHaveCount(0);

  await page.screenshot({ path: "test-results/wenshu-image-approval.png", fullPage: false });

  await card.getByRole("button", { name: "批准并生成", exact: true }).click();
  await expect(page.getByText("文章配图已生成。")).toBeVisible();
});

test("WeChat draft → safe one-time summary, real approval, localized result", async ({
  page,
}) => {
  await page.goto("/");
  const box = page.getByPlaceholder("告诉文枢你想完成什么...");
  await box.fill("保存公众号草稿");
  await page.getByRole("button", { name: "发送" }).click();

  const card = page.locator(".approval").filter({ hasText: "只保存到草稿箱，不会正式发布" });
  await expect(card).toBeVisible();
  await expect(card).toContainText("微信公众号");
  await expect(card).toContainText("文枢内容流水线");
  await expect(card).toContainText("先审文字，再完成图文排版。");
  await expect(card).toContainText("封面 cover.png");
  await expect(card).toContainText("正文图片 2 张");
  await expect(card).toContainText("主题 classic · 配色 #1f4d3a");
  await expect(card).toContainText("将保存到微信公众号草稿箱");
  await expect(card).not.toContainText(
    /e2e-sensitive-preview-hash|\/Users\/test\/private|drafts\/assets|preview_hash|AppID|AppSecret|access_token|<article/i,
  );
  await expect(
    card.getByRole("button", { name: "批准并保存草稿", exact: true }),
  ).toHaveCount(1);
  await expect(
    card.getByRole("button", { name: "本次会话始终允许", exact: true }),
  ).toHaveCount(0);
  await expect(
    card.getByRole("button", { name: "此自动化始终允许", exact: true }),
  ).toHaveCount(0);

  await card.getByRole("button", { name: "批准并保存草稿", exact: true }).click();
  await expect(page.getByText("公众号草稿处理完成。[decision=once]")).toBeVisible();

  const group = page.locator("details.stepgroup").last();
  await group.locator("summary").click();
  const result = group.getByTestId("wechat-draft-result");
  await expect(result).toContainText("已保存到公众号草稿箱：文枢内容流水线");
  await expect(result).not.toContainText(/uploaded_assets|cover\.png|image-1\.png|media_id|access_token/);
  await expect(page.locator("body")).not.toContainText(
    /e2e-sensitive-preview-hash|\/Users\/test\/private\/drafts\/article\.md/,
  );
});

test("routine write → compact row: humanized title, inline preview, Allow resolves", async ({
  page,
}) => {
  await page.goto("/");
  const box = page.getByPlaceholder("告诉文枢你想完成什么...");
  await box.fill("请写一个文件");
  await page.getByRole("button", { name: "发送" }).click();

  const row = page.getByTestId("approval-row");
  await expect(row).toContainText("写入 fetch_data.py");
  await expect(row).not.toContainText(/permission required/i);
  await expect(row.getByRole("button", { name: "本次会话始终允许", exact: true })).toHaveAttribute(
    "title",
    /本次会话/,
  );

  // Preview expands INLINE from the tool args — the file doesn't exist yet.
  await row.getByText("预览 ▾").click();
  await expect(row).toContainText("import json");
  await row.getByText("显示全部 6 行").click();
  await expect(row).toContainText("done = True");

  await page.screenshot({ path: "test-results/ux018-compact-row.png", fullPage: false });

  await row.getByRole("button", { name: "批准", exact: true }).click();
  await expect(page.getByText(/Done via write_file/)).toBeVisible();
});

test("run_shell → full card: description title, command preview, stays-on-this-Mac note", async ({
  page,
}) => {
  await page.goto("/");
  const box = page.getByPlaceholder("告诉文枢你想完成什么...");
  await box.fill("请运行一个工具");
  await page.getByRole("button", { name: "发送" }).click();

  // The mocked proposal has no description → plain "Run a command" title; the command is
  // the preview; the reason still renders; the scope note replaces the old badge.
  await expect(page.getByText("运行命令").last()).toBeVisible();
  await expect(page.getByText("仅在此 Mac 上执行").last()).toBeVisible();
  await expect(page.getByText("The coworker wants to run a command.").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "始终允许此命令" }).last()).toBeVisible();
  await expect(page.getByText(/local action/)).toHaveCount(0);

  await page.screenshot({ path: "test-results/ux018-shell-card.png", fullPage: false });

  await page.getByRole("button", { name: "批准一次" }).last().click();
  await expect(page.getByText("The command ran; 1 file found.")).toBeVisible();
});

test("a one-paragraph digest send is clamped to a card, expandable in place", async ({
  page,
}) => {
  await page.goto("/");
  const box = page.getByPlaceholder("告诉文枢你想完成什么...");
  await box.fill("发送长摘要");
  await page.getByRole("button", { name: "发送" }).click();

  // The message rides in a clamped preview box — not an unbounded quote wall.
  const prev = page.locator(".approval-prev");
  await expect(prev).toBeVisible();
  await expect(prev).toContainText("aisuite — last 24 hours");
  const clampedHeight = (await prev.boundingBox())!.height;
  expect(clampedHeight).toBeLessThan(200);

  await page.screenshot({ path: "test-results/send-digest-clamped.png", fullPage: false });

  // Expands in place, and can collapse back.
  await prev.getByText("显示完整消息").click();
  expect((await prev.boundingBox())!.height).toBeGreaterThan(clampedHeight);
  await expect(prev.getByText("收起")).toBeVisible();
});
