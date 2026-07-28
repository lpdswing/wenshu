import { expect, type Page } from "@playwright/test";
import { wenshuTest as test } from "./fixtures";

async function openPersonas(page: Page) {
  await page.addInitScript(() => localStorage.setItem("ocw.flag.personas", "1"));
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("button", { name: "角色", exact: true }).click();
}

test("WenShu hides Gallery without requesting Cloud gallery endpoints", async ({ page }) => {
  const galleryRequests: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/v1/cloud/gallery")) galleryRequests.push(path);
  });

  await openPersonas(page);

  await expect(page.getByTestId("gallery-link")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Gallery/i })).toHaveCount(0);
  expect(galleryRequests).toEqual([]);
});

test("local non-builtin personas remain removable", async ({ page }) => {
  await openPersonas(page);

  await expect(page.getByTestId("persona-delete-cowork")).toHaveCount(0);
  await expect(page.getByText("Acme Notes")).toBeVisible();
  await page.getByTestId("persona-delete-acme-notes").click();
  await page.getByTestId("persona-delete-confirm-acme-notes").click();
  await expect(page.getByText("Acme Notes")).not.toBeVisible();
});
