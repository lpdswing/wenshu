import { expect, type Page } from "@playwright/test";
import { wenshuTest as test } from "./fixtures";

const trackDisabledFeatureRequests = (page: Page) => {
  const paths: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/v1/cloud/") || path.endsWith("/connect-managed")) {
      paths.push(path);
    }
  });
  return paths;
};

test("WenShu hides Cloud account entry points without polling Cloud", async ({ page }) => {
  const featureRequests = trackDisabledFeatureRequests(page);

  await page.goto("/");
  const row = page.getByTestId("account-row");
  await expect(row).not.toContainText(/signed in/i);
  await row.click();

  const menu = page.getByTestId("account-menu");
  await expect(menu.getByTestId("account-sign-in")).toHaveCount(0);
  await expect(menu).not.toContainText("OpenWorker Cloud");
  await expect(menu.getByRole("button", { name: "Connectors", exact: true })).toBeVisible();
  expect(featureRequests).toEqual([]);
});

test("WenShu onboarding hides Cloud sign-in and managed OAuth entry points", async ({ page }) => {
  const featureRequests = trackDisabledFeatureRequests(page);

  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByTestId("account-menu").getByRole("button", { name: "Settings" }).click();
  await page.getByRole("button", { name: "Run setup again" }).click();
  await page.getByTestId("ob-continue").click();

  await expect(page.getByTestId("ob-step-tools")).toBeVisible();
  await expect(page.getByTestId("ob-cloud-signin")).toHaveCount(0);
  await expect(page.getByText("Sign in for one-click connections")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Connect" })).toHaveCount(0);
  expect(featureRequests).toEqual([]);
});

test("WenShu automations stay local without managed templates or Cloud polling", async ({
  page,
}) => {
  const featureRequests = trackDisabledFeatureRequests(page);

  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page
    .getByTestId("account-menu")
    .getByRole("button", { name: "Automations", exact: true })
    .click();
  await page.getByRole("button", { name: "+ New automation" }).click();

  await expect(page.getByTestId("qs-template-news")).toBeVisible();
  await expect(page.getByTestId("qs-template-cleanup")).toBeVisible();
  await expect(page.getByTestId("qs-template-github")).toHaveCount(0);
  await expect(page.getByTestId("qs-template-pipeline")).toHaveCount(0);
  await expect(page.getByTestId("qs-template-brief")).toHaveCount(0);
  await expect(page.getByTestId("qs-template-inboxdigest")).toHaveCount(0);
  await expect(page.getByTestId("ob-cloud-signin")).toHaveCount(0);
  expect(featureRequests).toEqual([]);
});
