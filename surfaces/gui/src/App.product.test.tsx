import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

const productState = vi.hoisted(() => ({
  product: {
    id: "wenshu",
    name: "文枢",
    display_name: "文枢 WenShu",
    default_persona: "cowork",
    visible_connectors: ["browser", "wechat_official"],
    features: {
      cloud: false,
      gallery: false,
      managed_oauth: false,
      relay: false,
      updater: false,
    },
  },
}));

vi.mock("./api", () => {
  class FakeSession {
    constructor(
      _sessionId: string,
      _workspace: string,
      _agent: string,
      handlers: { onOpen: () => void },
    ) {
      queueMicrotask(handlers.onOpen);
    }
    close() {}
    userMessage() {}
    approve() {}
    respondPlan() {}
    respondDirectory() {}
    respondQuestion() {}
    interrupt() {}
    retry() {}
    setMode() {}
    setModel() {}
  }

  return {
    announceInboxUnlock: vi.fn(),
    finalizeAutomationRun: vi.fn(async () => ({})),
    getArtifacts: vi.fn(async () => []),
    getHealth: vi.fn(async () => ({
      status: "ok",
      default_workspace: null,
      model: "test:model",
      product: productState.product,
    })),
    getRecentWorkspaces: vi.fn(async () => []),
    getSessionMessages: vi.fn(async () => []),
    getSessions: vi.fn(async () => []),
    announceAutomationsChanged: vi.fn(),
    connectEvents: vi.fn(() => () => {}),
    getSettings: vi.fn(async () => ({
      models: ["test:model"],
      model_labels: { "test:model": "Test Model" },
      model_ready: true,
      onboarded: true,
      surfaces: { cowork: true, chat: false, code: false },
    })),
    getPersonas: vi.fn(async () => []),
    getInbox: vi.fn(async () => []),
    getUnattended: vi.fn(async () => false),
    PERSONAS_CHANGED: "personas-changed",
    resolveInboxItem: vi.fn(async () => ({})),
    deleteSession: vi.fn(async () => ({})),
    renameSession: vi.fn(async () => ({})),
    runAutomation: vi.fn(async () => ({ ok: false })),
    setSessionFlags: vi.fn(async () => ({})),
    setUnattended: vi.fn(async () => ({})),
    Session: FakeSession,
  };
});

vi.mock("./components/UpdateBanner", () => ({
  UpdateBanner: () => <div data-testid="update-banner" />,
}));
vi.mock("./components/Sidebar", () => ({
  Sidebar: () => <div data-testid="sidebar" />,
}));
vi.mock("./components/RightRail", () => ({ RightRail: () => null }));
vi.mock("./components/SessionIntro", () => ({ SessionIntro: () => <div /> }));
vi.mock("./components/Composer", () => ({ Composer: () => null }));
vi.mock("./tauri", () => ({
  isTauri: () => false,
  platformOS: () => "linux",
  startWindowDrag: vi.fn(),
}));

import { App } from "./App";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("does not mount the updater entry point when the product disables it", async () => {
  render(<App />);

  await waitFor(() => expect(screen.getByTestId("sidebar")).toBeTruthy());
  expect(screen.queryByTestId("update-banner")).toBeNull();
});
