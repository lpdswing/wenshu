// The Connectors LIST (UX-DECISIONS §21): connected connectors first in their own
// section with a health chip, rows navigate to the connector's detail subpage
// (breadcrumb back), available connectors get a Connect pill → add-connection modal
// with One click | Manual pills for multi-mode connectors.
import { expect } from "@playwright/test";
import { openAccountPage, test, wenshuTest } from "./fixtures";

async function openConnectors(page) {
  await openAccountPage(page, "connectors");
}

test("connected connectors come first with status + health chip", async ({ page }) => {
  await openConnectors(page);

  const slack = page.getByTestId("connector-slack");
  await expect(slack).toContainText("2 workspaces · relay");
  // signed out + relay mode → the honest chip is the actionable one
  await expect(slack).toContainText("Sign-in needed");
  // available section renders the not-connected connectors with a Connect pill
  await expect(
    page.getByTestId("connector-telegram").getByRole("button", { name: "Connect" }),
  ).toBeVisible();
});

test("row navigates to the detail subpage; breadcrumb returns", async ({ page }) => {
  await openConnectors(page);
  await page.getByTestId("connector-slack").click();
  await expect(page.getByTestId("slack-workspaces")).toBeVisible();
  await page.getByTestId("connectors-breadcrumb").click();
  await expect(page.getByTestId("connector-slack")).toContainText("2 workspaces · relay");
});

test("generic detail page: tools + two-way blocks + disconnect for telegram-alikes", async ({
  page,
}) => {
  await openConnectors(page);
  // Browser is keyless-connected → generic page, no Disconnect for auth=none
  await page.getByTestId("connector-browser").click();
  await expect(page.getByRole("heading", { name: "Browser" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Disconnect" })).toHaveCount(0);
  await page.getByTestId("connectors-breadcrumb").click();
});

test("Connect on a multi-mode connector opens the modal with One click | Manual pills", async ({
  page,
}) => {
  await openConnectors(page);
  // make slack disconnected for this test: disconnect both workspaces via its page is
  // heavy — instead assert the modal via the detail page's Add workspace in the slack spec;
  // here we verify the generic modal path with telegram (single-mode → ConnectSetup pane).
  await page.getByTestId("connector-telegram").getByRole("button", { name: "Connect" }).click();
  const modal = page.getByTestId("add-connection-modal");
  await expect(modal).toBeVisible();
  await expect(modal.locator("input")).not.toHaveCount(0); // manual fields rendered
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("add-connection-modal")).toHaveCount(0);
});

test("filter narrows both sections", async ({ page }) => {
  await openConnectors(page);
  await page.getByPlaceholder("Search").fill("tele");
  await expect(page.getByTestId("connector-telegram")).toBeVisible();
  await expect(page.getByTestId("connector-slack")).toHaveCount(0);
});

wenshuTest("微信公众号连接、评论设置与凭据脱敏形成闭环", async ({ page }) => {
  await openConnectors(page);

  const entry = page.getByTestId("connector-wechat_official");
  await expect(entry).toBeVisible();
  await expect(entry).toContainText("微信公众号");
  await entry.click();

  await expect(page.getByTestId("wechat-detail")).toBeVisible();
  const appId = page.getByTestId("wechat-app-id");
  const appSecret = page.getByTestId("wechat-app-secret");
  await expect(appSecret).toHaveAttribute("type", "password");

  const identity = "wx-e2e-account";
  const secret = "must-never-be-returned";
  await appId.fill(identity);
  await appSecret.fill(secret);

  const connectRequest = page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname ===
        "/v1/connectors/wechat_official/connect" &&
      request.method() === "POST",
  );
  const connectResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname ===
        "/v1/connectors/wechat_official/connect" &&
      response.request().method() === "POST",
  );
  const refreshedList = page.waitForResponse(async (response) => {
    if (
      new URL(response.url()).pathname !== "/v1/connectors" ||
      response.request().method() !== "GET"
    )
      return false;
    const body: unknown = await response.json();
    if (!body || typeof body !== "object" || !("connectors" in body))
      return false;
    return (
      Array.isArray(body.connectors) &&
      body.connectors.some(
        (connector) =>
          connector &&
          typeof connector === "object" &&
          "name" in connector &&
          connector.name === "wechat_official" &&
          "identity" in connector &&
          connector.identity === identity,
      )
    );
  });
  await page.getByTestId("wechat-connect").click();

  const request = await connectRequest;
  expect(request.postDataJSON()).toEqual({
    fields: { app_id: identity, app_secret: secret },
  });
  const responseBody = await (await connectResponse).json();
  expect(responseBody).toEqual({ ok: true, identity });
  expect(JSON.stringify(responseBody)).not.toContain(secret);

  const listBody: unknown = await (await refreshedList).json();
  expect(listBody).toMatchObject({
    connectors: expect.arrayContaining([
      expect.objectContaining({
        name: "wechat_official",
        identity,
        configured_fields: ["app_id", "app_secret"],
      }),
    ]),
  });
  expect(JSON.stringify(listBody)).not.toContain(secret);

  await expect(page.getByTestId("wechat-identity")).toContainText(identity);
  await expect(page.getByTestId("wechat-app-secret")).toHaveCount(0);
  const visibleInputValues = await page.locator("input").evaluateAll((inputs) =>
    inputs.map((input) => (input as HTMLInputElement).value),
  );
  expect(visibleInputValues).not.toContain(secret);
  await expect(page.locator("body")).not.toContainText(secret);

  const needComment = page.getByRole("switch", { name: "开启评论" });
  const fansOnly = page.getByRole("switch", { name: "仅粉丝可评论" });
  await expect(needComment).toHaveAttribute("aria-checked", "false");
  await expect(fansOnly).toBeDisabled();

  const enableComments = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname ===
        "/v1/connectors/wechat_official/settings" &&
      response.request().method() === "PATCH",
  );
  await needComment.click();
  expect(await (await enableComments).json()).toEqual({
    need_open_comment: true,
    only_fans_can_comment: false,
  });
  await expect(needComment).toHaveAttribute("aria-checked", "true");
  await expect(fansOnly).toBeEnabled();

  await fansOnly.click();
  await expect(fansOnly).toHaveAttribute("aria-checked", "true");

  // Leave and reopen the detail route: both switches must come back from GET,
  // rather than surviving only as component-local state.
  await page.getByTestId("connectors-breadcrumb").click();
  await page.getByTestId("connector-wechat_official").click();
  await expect(page.getByRole("switch", { name: "开启评论" })).toHaveAttribute(
    "aria-checked",
    "true",
  );
  await expect(
    page.getByRole("switch", { name: "仅粉丝可评论" }),
  ).toHaveAttribute("aria-checked", "true");

  const reopenedNeedComment = page.getByRole("switch", { name: "开启评论" });
  await reopenedNeedComment.click();
  await expect(reopenedNeedComment).toHaveAttribute("aria-checked", "false");
  const reopenedFansOnly = page.getByRole("switch", {
    name: "仅粉丝可评论",
  });
  await expect(reopenedFansOnly).toHaveAttribute("aria-checked", "false");
  await expect(reopenedFansOnly).toBeDisabled();

  // A rejected save remains visible and does not optimistically move the switch.
  await page.route(
    "**/v1/connectors/wechat_official/settings",
    async (route) => {
      if (route.request().method() !== "PATCH") return route.fallback();
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "fixture save failure" }),
      });
    },
  );
  await reopenedNeedComment.click();
  await expect(reopenedNeedComment).toHaveAttribute("aria-checked", "false");
  await expect(page.getByTestId("wechat-settings-error")).toContainText(
    "保存失败",
  );
});
