import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../providers/ProviderSetup", () => ({
  useProviderSetup: () => ({
    providers: [{ name: "local", configured: true, needs_key: true }],
    keylessOk: new Set<string>(),
    sel: null,
    dirty: false,
    secretFilled: false,
    credentialed: true,
    verify: { state: "idle" },
    cancelBackTimer: vi.fn(),
    runTestAndSave: vi.fn(),
  }),
  ProviderCards: () => <div data-testid="provider-cards" />,
  ProviderForm: () => null,
}));

import { Onboarding } from "./Onboarding";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

it("does not poll or render Cloud and managed OAuth entry points when disabled", async () => {
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
    <Onboarding
      features={{
        cloud: false,
        gallery: false,
        managed_oauth: false,
        relay: false,
        updater: false,
      }}
      onDone={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByTestId("ob-continue"));
  await screen.findByTestId("ob-step-tools");

  await waitFor(() => {
    expect(requests.some((url) => url.includes("/v1/connectors"))).toBe(true);
  });
  expect(requests.some((url) => url.includes("/v1/cloud/"))).toBe(false);
  expect(screen.queryByTestId("ob-cloud-signin")).toBeNull();
  expect(screen.queryByText("Sign in for one-click connections")).toBeNull();
  expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
});
