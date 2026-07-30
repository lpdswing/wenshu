import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { AutomationQuickstart } from "./AutomationQuickstart";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

it("keeps disabled profiles local without polling Cloud or showing managed templates", async () => {
  const requests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      requests.push(url);
      if (url.includes("/v1/connectors")) {
        return { ok: true, json: async () => ({ connectors: [] }) } as Response;
      }
      if (url.includes("/v1/cloud/status")) {
        return {
          ok: true,
          json: async () => ({ signed_in: false, account: "", user_id: "" }),
        } as Response;
      }
      return { ok: true, json: async () => ({}) } as Response;
    }),
  );

  render(
    <AutomationQuickstart
      busy={false}
      features={{
        cloud: false,
        gallery: false,
        managed_oauth: false,
        relay: false,
        updater: false,
      }}
      onCreate={vi.fn()}
    />,
  );

  await waitFor(() => {
    expect(requests.some((url) => url.includes("/v1/connectors"))).toBe(true);
  });
  expect(requests.some((url) => url.includes("/v1/cloud/"))).toBe(false);
  expect(screen.getByTestId("qs-template-news")).toBeTruthy();
  expect(screen.getByTestId("qs-template-cleanup")).toBeTruthy();
  expect(screen.queryByTestId("qs-template-github")).toBeNull();
  expect(screen.queryByTestId("qs-template-pipeline")).toBeNull();
  expect(screen.queryByTestId("qs-template-brief")).toBeNull();
  expect(screen.queryByTestId("qs-template-inboxdigest")).toBeNull();
  expect(screen.queryByTestId("ob-cloud-signin")).toBeNull();
  expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
});
