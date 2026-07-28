import { test, expect } from "./fixtures";

// Guards the three-control composer row (§22): send-gating (accent only with content), the "+"
// attach menu, and the Mode menu (permission options + the folded-in Send-to-Inbox toggle).
test("composer: send-gating, + attach menu, Mode menu", async ({ page }) => {
  await page.goto("/");

  const box = page.getByPlaceholder("告诉文枢你想完成什么...");
  const send = page.getByRole("button", { name: "发送" });

  // Send is subtle grey when empty, accent once there's content, grey again when cleared.
  await expect(send).not.toHaveClass(/bg-accent/);
  await box.fill("hello there");
  await expect(send).toHaveClass(/bg-accent/);
  await box.fill("");
  await expect(send).not.toHaveClass(/bg-accent/);

  // "+" attach menu offers the three typed shortcuts.
  await page.getByRole("button", { name: "添加附件" }).click();
  await expect(page.getByRole("button", { name: "照片或图片" })).toBeVisible();
  await expect(page.getByRole("button", { name: "PDF", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "其他文件" })).toBeVisible();
  // Clicking the backdrop closes it.
  await page.locator(".fixed.inset-0.z-30").click();
  await expect(page.getByRole("button", { name: "照片或图片" })).toHaveCount(0);

  // Mode menu: the three shipped permission options with the current one marked, plus the
  // Unattended/send-to-Inbox toggle (§22). Plan + Custom hidden for this release (2026-07-22).
  await page.getByRole("button", { name: "权限模式", exact: true }).click();
  const menu = page.getByTestId("mode-menu");
  await expect(menu.getByText("讨论")).toBeVisible();
  await expect(menu.getByText("方案", { exact: true })).toHaveCount(0);
  await expect(menu.getByText("自定义", { exact: true })).toHaveCount(0);
  // The current mode is marked with a ✓.
  await expect(menu.locator("button").filter({ hasText: "操作前批准" })).toContainText("✓");
  await expect(menu.getByRole("switch", { name: "将审批发送到收件箱" })).toBeVisible();
  // Picking an option closes the menu (and would flip the live engine's mode).
  await menu.getByText("完全访问").click();
  await expect(page.getByTestId("mode-menu")).toHaveCount(0);
});

// PDFs read as data URLs and show a named chip (DMG #29 walkthrough catch: PDFs silently
// no-op'd because readFile only handled images and text).
test("composer: picking a PDF shows an attachment chip and arms send", async ({ page }) => {
  await page.goto("/");

  const send = page.getByRole("button", { name: "发送" });
  await expect(send).not.toHaveClass(/bg-accent/);

  await page.locator('input[type="file"]').setInputFiles({
    name: "report.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"),
  });

  const chip = page.locator(".attach-chip");
  await expect(chip).toContainText("report.pdf");
  await expect(send).toHaveClass(/bg-accent/); // attachment alone arms send

  // Removing the chip disarms send again.
  await chip.locator(".attach-x").click();
  await expect(page.locator(".attach-chip")).toHaveCount(0);
  await expect(send).not.toHaveClass(/bg-accent/);
});

// Token-savings threshold (owner ask, 2026-07-17): a PDF over the user's page limit is
// REJECTED with a visible notice — no chip, send stays disarmed. Fixture limit: 2 pages;
// the mock inspect endpoint reads the page count from a "%%pages=N" marker in the body.
test("composer: PDF over the page threshold is rejected with a notice", async ({ page }) => {
  await page.goto("/");

  await page.locator('input[type="file"]').setInputFiles({
    name: "big-report.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\n%%pages=34\ntrailer\n<<>>\n%%EOF"),
  });

  const notice = page.getByTestId("attach-notice");
  await expect(notice).toContainText("big-report.pdf 已跳过");
  await expect(notice).toContainText("34 页超过 2 页上限");
  await expect(page.locator(".attach-chip")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "发送" })).not.toHaveClass(/bg-accent/);

  // The ✕ dismisses the notice.
  await notice.getByRole("button").click();
  await expect(page.getByTestId("attach-notice")).toHaveCount(0);

  // A small PDF (1 page per the mock) still attaches fine after a rejection.
  await page.locator('input[type="file"]').setInputFiles({
    name: "small.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\n%%pages=1\ntrailer\n<<>>\n%%EOF"),
  });
  await expect(page.locator(".attach-chip")).toContainText("small.pdf");
});
