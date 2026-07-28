// Model-layer roadmap item 3 (2026-07-22): the model picker stays actionable for the
// session's whole life (supersedes the 2026-07-04 lock that hid it after the first turn).
// A mid-session switch drops a persisted info marker into the transcript, and later
// messages ride the new model.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("mid-session model switch shows the marker and later turns use the new model", async ({
  page,
}) => {
  await page.goto("/");
  const box = page.getByPlaceholder("告诉文枢你想完成什么...");
  await box.fill("hello there");
  await box.press("Enter");
  await expect(page.getByText("Echo: hello there", { exact: false }).first()).toBeVisible();

  // The picker is still in the composer after the first turn (the old lock hid it).
  const picker = page.locator(".dd").filter({ hasText: "Claude Opus 4.8" });
  await expect(picker).toBeVisible();
  await picker.locator(".pill").click();
  await page.locator(".dd-item").filter({ hasText: "GPT-5.5" }).click();

  // The switch marker lands in the transcript…
  await expect(page.getByText("已切换模型：gpt-5.5", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/此模型无法读取之前的图片/)).toHaveCount(0);

  // …and the next message carries the new model (the fixture echoes it back).
  await box.fill("after the switch");
  await box.press("Enter");
  await expect(
    page.getByText("Echo: after the switch [model=gpt-5.5]", { exact: false }).first(),
  ).toBeVisible();
});

test("live model switch localizes the image degradation warning", async ({ page }) => {
  await page.goto("/");
  const box = page.getByPlaceholder("告诉文枢你想完成什么...");
  await page.locator('input[type="file"]').setInputFiles({
    name: "diagram.png",
    mimeType: "image/png",
    buffer: Buffer.from("fixture-image"),
  });
  await box.fill("inspect this image");
  await box.press("Enter");
  await expect(page.getByText(/Echo: inspect this image/).first()).toBeVisible();

  const picker = page.locator(".dd").filter({ hasText: "Claude Opus 4.8" });
  await picker.locator(".pill").click();
  await page.locator(".dd-item").filter({ hasText: "GPT-5.5" }).click();

  await expect(
    page
      .getByText(
        "已切换模型：gpt-5.5 — 此模型无法读取之前的图片",
        { exact: true },
      )
      .first(),
  ).toBeVisible();
});
