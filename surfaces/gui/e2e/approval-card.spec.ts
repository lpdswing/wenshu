// §35 (UX-018): approval cards speak the transcript's language. Routine workspace writes
// are a compact ROW (humanized title, inline args-preview, short "Always allow" with the
// full rule on hover); everything else is a full card — shell titles with the model's
// description, external actions wear the leaves-this-Mac note. No "PERMISSION REQUIRED"
// kicker, no raw args dump, no solid-fill buttons.
import { expect } from "@playwright/test";
import { wenshuTest as test } from "./fixtures";

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
