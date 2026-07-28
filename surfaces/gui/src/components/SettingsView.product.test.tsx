import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

vi.mock("../api", () => ({
  getSettings: vi.fn(async () => ({ sessions_peek: 5, scratch_base: "/tmp/wenshu" })),
  getTrustedWorkspaces: vi.fn(async () => []),
  setOnboarded: vi.fn(async () => ({ ok: true })),
  setPdfSettings: vi.fn(async () => ({ ok: true })),
  setScratchBase: vi.fn(async () => ({ ok: true })),
  setSessionsPeek: vi.fn(async () => ({ ok: true })),
  setWorkspaceTrusted: vi.fn(async () => ({ ok: true })),
}));

vi.mock("../tauri", () => ({
  cancelDictationModelDownload: vi.fn(async () => undefined),
  deleteDictationModel: vi.fn(async () => ({})),
  downloadDictationModel: vi.fn(async () => ({})),
  getAutostart: vi.fn(async () => false),
  getDictationStatus: vi.fn(async () => null),
  getKeepAwake: vi.fn(async () => false),
  checkForUpdate: vi.fn(async () => null),
  installUpdate: vi.fn(async () => undefined),
  isTauri: () => true,
  listenDictationDownloadProgress: vi.fn(async () => () => undefined),
  markDictationTestPassed: vi.fn(async () => ({})),
  pickFolder: vi.fn(async () => null),
  setAutostart: vi.fn(async (enabled: boolean) => enabled),
  setKeepAwake: vi.fn(async (enabled: boolean) => enabled),
  startDictation: vi.fn(async () => ({})),
  stopDictation: vi.fn(async () => ""),
  verifyDictationModel: vi.fn(async () => ({})),
}));

vi.mock("../theme", () => ({ useThemePref: () => ["auto", vi.fn()] }));
vi.mock("../flags", () => ({ showPersonas: () => false }));
vi.mock("./Icon", () => ({ Icon: () => null }));
vi.mock("./IntegrationsView", () => ({
  PanelHead: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
vi.mock("./ManageTabs", () => ({ ModelsTab: () => null }));
vi.mock("./GalleryModal", () => ({ GalleryModal: () => null }));
vi.mock("./PersonasTab", () => ({ PersonasTab: () => null }));

import { SettingsView } from "./SettingsView";

afterEach(cleanup);

it("hides manual updater controls when the product disables updates", () => {
  render(<SettingsView galleryEnabled={false} updaterEnabled={false} />);

  expect(screen.queryByTestId("settings-update-check")).toBeNull();
  expect(screen.getByText("Setup")).toBeTruthy();
});

it("keeps manual updater controls for an updater-enabled product", () => {
  render(<SettingsView galleryEnabled={false} updaterEnabled />);

  expect(screen.getByTestId("settings-update-check")).toBeTruthy();
  expect(screen.getByText("Setup & updates")).toBeTruthy();
});
