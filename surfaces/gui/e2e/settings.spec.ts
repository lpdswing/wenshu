import { expect, openAccountPage, test } from "./fixtures";

// Guards the Settings-as-page refactor (§13, IA per UX-021): the ⚙ menu opens a full-page
// surface with a left sub-nav — General · Models · Voice input — and each section renders.
// Files is a card inside General; Personas is launch-flagged off.
test("Settings opens as a full page and navigates sections", async ({ page }) => {
  await openAccountPage(page, "settings");

  // Full-page: left sub-nav + the General section (no modal backdrop).
  await expect(page.getByRole("heading", { name: "通用" })).toBeVisible();
  await expect(page.locator(".modal-backdrop")).toHaveCount(0);
  for (const label of ["通用", "模型", "语音输入"]) {
    await expect(page.getByRole("button", { name: label, exact: true })).toBeVisible();
  }
  // Folded/hidden tabs: Files is a General card now; Personas is launch-flagged off.
  await expect(page.getByRole("button", { name: "文件", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "角色", exact: true })).toHaveCount(0);

  // The Files card lives inside General.
  await expect(page.getByText(/每个会话都会在此位置下获得独立文件夹/)).toBeVisible();
  await expect(page.locator('input[placeholder="~/WenShu"]')).toHaveValue("~/WenShu");

  await page.getByRole("button", { name: "模型", exact: true }).click();
  await expect(page.getByTestId("set-provider-openai")).toBeVisible();
});

// The launch flag brings the Personas tab back (the gallery/persona suites rely on it).
test("Settings: Personas tab returns behind the launch flag", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("ocw.flag.personas", "1"));
  await openAccountPage(page, "settings");
  await page.getByRole("button", { name: "角色", exact: true }).click();
  await expect(page.getByText("Add personas")).toBeVisible();
});

// UX-021: Settings ▸ Models is the shared provider gallery (§39 components). Cards wear
// their own state (✓ Connected · used …); a vendor card opens the shared key form with the
// prefilled endpoint behind the disclosure; unconfigured providers preview their models.
test("Models: provider gallery states; vendor form previews models", async ({ page }) => {
  await openAccountPage(page, "settings");
  await page.getByRole("button", { name: "模型", exact: true }).click();

  // Card states from the fixtures: openai configured+used, anthropic configured, zai not.
  await expect(page.getByTestId("set-provider-openai")).toContainText("✓ 已连接 · 上次使用：2 小时前");
  await expect(page.getByTestId("set-provider-anthropic")).toContainText("✓ 已连接");
  await expect(page.getByTestId("set-provider-zai")).toContainText("未设置");
  await expect(page.getByTestId("set-provider-ollama")).toContainText("无需密钥");

  // The composer-picker card lists the curated models with provider tags.
  const picker = page.getByTestId("composer-picker");
  await expect(picker).toContainText("输入框中的模型");

  // Vendor form: blurb renders; the prefilled endpoint hides behind the disclosure.
  await page.getByTestId("set-provider-zai").click();
  await expect(page.getByText(/Uses Z AI's OpenAI-compatible API/)).toBeVisible();
  await page.getByTestId("set-endpoint-link").click();
  await expect(page.getByTestId("set-field-base_url")).toHaveValue("https://api.z.ai/api/paas/v4");

  // Unconfigured providers still preview their curated models (read-only, matrix labels).
  const preview = page.getByTestId("model-preview");
  await expect(preview).toContainText("包含的模型");
  await expect(preview).toContainText("GLM-5.2 · Z AI");

  // Back to the gallery via the crumb.
  await page.getByTestId("set-back").click();
  await expect(page.getByTestId("set-provider-openai")).toBeVisible();
});

// UX-021: a configured provider's form shows the in-field saved state and the Remove key…
// affordance; removing reverts the card to "Not set up".
test("Models: Remove key reverts a configured provider", async ({ page }) => {
  await openAccountPage(page, "settings");
  page.on("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "模型", exact: true }).click();

  await page.getByTestId("set-provider-anthropic").click();
  await expect(page.getByTestId("set-saved-pill")).toContainText("已测试并保存");
  await page.getByTestId("set-remove-key").click();

  // Back on the gallery, the card has forgotten its key.
  await expect(page.getByTestId("set-provider-anthropic")).toContainText("未设置");
});

// Token savings (owner ask 2026-07-17; moved under Models by UX-021): the card renders with
// the PDF fallback segmented control + attach thresholds, and edits POST through.
test("Settings: Token savings card edits PDF fallback and thresholds", async ({ page }) => {
  await openAccountPage(page, "settings");
  await page.getByRole("button", { name: "模型", exact: true }).click();

  const card = page.getByTestId("token-savings-card");
  await expect(card).toBeVisible();
  await expect(card.getByText("节省令牌", { exact: true })).toBeVisible();

  // Fallback mode: fixture says "text"; switching marks "Send page images" active.
  const seg = page.getByTestId("pdf-fallback");
  await expect(seg.getByRole("button", { name: "提取文字" })).toHaveClass(/active/);
  const [req] = await Promise.all([
    page.waitForRequest((r) => r.url().endsWith("/v1/settings/pdf") && r.method() === "POST"),
    seg.getByRole("button", { name: "发送页面图片" }).click(),
  ]);
  expect(req.postDataJSON()).toEqual({ pdf_fallback: "images" });
  await expect(seg.getByRole("button", { name: "发送页面图片" })).toHaveClass(/active/);

  // Thresholds: fixture starts at 2 pages / 10 MB; editing pages POSTs the clamped value.
  await expect(card.getByTestId("pdf-max-pages")).toHaveValue("2");
  await expect(card.getByTestId("pdf-max-mb")).toHaveValue("10");
  const [req2] = await Promise.all([
    page.waitForRequest((r) => r.url().endsWith("/v1/settings/pdf") && r.method() === "POST"),
    card.getByTestId("pdf-max-pages").fill("30"),
  ]);
  expect(req2.postDataJSON()).toEqual({ pdf_max_pages: 30 });
});
