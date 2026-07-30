import { wenshuTest as test, expect } from "./fixtures";

test("app loads with the persona nav and composer", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".brand-wordmark").first()).toContainText("文枢");
  await expect(page.getByPlaceholder("告诉文枢你想完成什么...")).toBeVisible();
  await expect(page.getByRole("button", { name: "发送" })).toBeVisible();
  await expect(page.getByRole("button", { name: "进度" })).toBeVisible();
  await expect(page.getByRole("button", { name: "交付物" })).toBeVisible();
  // New session + Search are the fixed top nav.
  await expect(page.getByRole("button", { name: /新建会话/ })).toBeVisible();
  // The persona groups render from /v1/personas.
  await expect(page.getByText("Ops", { exact: true })).toBeVisible();
});
