// First-run onboarding (UX-DECISIONS §24 → §29 → §39): model → your tools → go.
// §39: step 1 is a provider GALLERY (cards wear their own state; a card opens its key
// form inside a fixed-height swap region; Test verifies, SAVES, and returns) and step 2
// is a two-state tools page (why-paragraph + sign-in → mini connector gallery with live
// one-click connects). Entered here via the REPLAY path (Settings ▸ Appearance ▸ "Run
// setup again") — which is itself under test.
import { expect } from "@playwright/test";
import { wenshuTest as test } from "./fixtures";

async function openOnboarding(page) {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByTestId("account-menu").getByRole("button", { name: "设置" }).click();
  await page.getByRole("button", { name: "重新运行设置" }).click();
  await expect(page.getByTestId("ob-step-model")).toBeVisible();
}

test("provider gallery: cards wear their state; Next arms off stored credentials", async ({
  page,
}) => {
  await openOnboarding(page);

  // Every card carries its own status with zero clicks (the 2026-07-16 confusion —
  // "is OpenAI already connected?" — is answered by the gallery itself).
  await expect(page.getByTestId("ob-provider-openai")).toContainText("✓ 已连接");
  await expect(page.getByTestId("ob-provider-anthropic")).toContainText("✓ 已连接");
  await expect(page.getByTestId("ob-provider-zai")).toContainText("未设置");
  await expect(page.getByTestId("ob-provider-ollama")).toContainText("无需密钥");
  // Recognition-first order: anthropic before openai before the OpenAI-compat tail.
  const names = await page
    .getByTestId("ob-provider-gallery")
    .locator("[data-testid^=ob-provider-]")
    .evaluateAll((els) => els.map((e) => e.getAttribute("data-testid")));
  expect(names.indexOf("ob-provider-anthropic")).toBeLessThan(names.indexOf("ob-provider-openai"));
  expect(names.indexOf("ob-provider-openai")).toBeLessThan(names.indexOf("ob-provider-zai"));

  // A configured provider already arms Next — no form visit required.
  await expect(page.getByTestId("ob-continue")).toBeEnabled();
  await page.getByTestId("ob-continue").click();
  await expect(page.getByTestId("ob-step-tools")).toBeVisible();
});

test("key form: Test verifies, saves, and returns to the gallery with the ✓", async ({
  page,
}) => {
  await openOnboarding(page);

  await page.getByTestId("ob-provider-zai").click();
  // The header stays put (§39 fixed frame): the welcome headline is still on screen.
  await expect(page.getByRole("heading", { name: /欢迎使用文枢/ })).toBeVisible();
  // Optional endpoint is a quiet disclosure with no explainer copy (owner call 2026-07-18).
  await expect(page.getByTestId("ob-field-base_url")).toHaveCount(0);
  await page.getByTestId("ob-endpoint-link").click();
  await expect(page.getByTestId("ob-field-base_url")).toHaveValue(/api\.z\.ai/);

  // Bad key: the error is a line, not a navigation.
  await page.getByTestId("ob-field-api_key").fill("bad-key");
  await page.getByTestId("ob-test").click();
  await expect(page.getByText("Invalid API key.")).toBeVisible();

  // Good key: state lands IN the field ("✓ Tested & saved" pill), then the form
  // auto-returns to the gallery where the Z AI card now wears its ✓.
  await page.getByTestId("ob-field-api_key").fill("zk-good");
  await page.getByTestId("ob-test").click();
  await expect(page.getByTestId("ob-saved-pill")).toBeVisible();
  await expect(page.getByTestId("ob-provider-zai")).toContainText("✓ 已连接", {
    timeout: 5_000,
  });
  await expect(page.getByTestId("ob-continue")).toBeEnabled();
});

test("key form: revisiting a connected provider shows the in-field saved state; drafts survive switching", async ({
  page,
}) => {
  await openOnboarding(page);

  // Revisit a configured provider: green in-field pill + masked placeholder — the old
  // empty-password-field-reads-as-not-set-up trap (owner complaint 2026-07-16) is gone.
  await page.getByTestId("ob-provider-openai").click();
  await expect(page.getByTestId("ob-saved-pill")).toBeVisible();
  await expect(page.getByTestId("ob-field-api_key")).toHaveAttribute("placeholder", "••••••••");

  // Typed-but-unsaved input survives a peek at another provider (drafts).
  await page.getByTestId("ob-back").click();
  await page.getByTestId("ob-provider-zai").click();
  await page.getByTestId("ob-field-api_key").fill("zk-draft");
  await page.getByTestId("ob-back").click();
  await page.getByTestId("ob-provider-openai").click();
  await expect(page.getByTestId("ob-saved-pill")).toBeVisible();
  await page.getByTestId("ob-back").click();
  await page.getByTestId("ob-provider-zai").click();
  await expect(page.getByTestId("ob-field-api_key")).toHaveValue("zk-draft");

  // Next from a dirty form auto-verifies and saves first (2026-07-12: no hidden
  // Test-then-Continue two-step), then advances.
  await page.getByTestId("ob-field-api_key").fill("zk-good");
  await page.getByTestId("ob-continue").click();
  await expect(page.getByTestId("ob-step-tools")).toBeVisible();
});

test("tools page stays local when cloud features are disabled and advances cleanly", async ({
  page,
}) => {
  await openOnboarding(page);
  await page.getByTestId("ob-continue").click();
  await expect(page.getByTestId("ob-step-tools")).toBeVisible();
  await expect(page.getByRole("heading", { name: "连接常用工具" })).toBeVisible();

  await expect(page.getByTestId("ob-cloud-signin")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "连接", exact: true })).toHaveCount(0);
  await expect(page.getByTestId("ob-continue-tools")).toHaveText("下一步");
  await page.getByTestId("ob-continue-tools").click();

  await expect(page.getByTestId("ob-step-done")).toBeVisible();
  await expect(page.getByRole("heading", { name: "设置完成" })).toBeVisible();
  await page.getByTestId("ob-cta-automation").click();
  await expect(page.getByTestId("onboarding")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Automations" })).toBeVisible();
});

test("tools page advances cleanly; starting work lands in a session with the panel open", async ({
  page,
}) => {
  await openOnboarding(page);
  await page.getByTestId("ob-continue").click();
  await page.getByTestId("ob-continue-tools").click();
  await expect(page.getByTestId("ob-step-done")).toBeVisible();
  await expect(page.getByRole("heading", { name: "设置完成" })).toBeVisible();
  await expect(page.getByRole("button", { name: "开始使用文枢" })).toBeVisible();
  await page.getByTestId("ob-start").click();
  await expect(page.getByTestId("onboarding")).toHaveCount(0);
  // §32: "Start working" lands with the rail's Access section expanded (the drawer is gone).
  await expect(page.getByRole("region", { name: "会话访问范围" })).toBeVisible();
});
