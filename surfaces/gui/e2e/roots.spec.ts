// Guards the per-session directory RO/RW gate (§ roots), which since §32 lives in the rail's
// Access section under "Folders" (folder access is standing session config, not per-message
// attachment — the composer's folder popover is gone). The section lists the primary writable
// workspace, and adding a folder is gated read-only by default with an explicit "Allow writes"
// opt-in.
import { test, expect } from "./fixtures";

test("working directories: add folders with the read-only / read-write gate", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  // Expand the rail's Access section.
  await page.getByTestId("access-toggle").click();
  const dirs = page.getByTestId("drawer-directories");
  await expect(dirs.getByText("文件夹", { exact: true })).toBeVisible();

  // The primary is the writable scratch workspace (Cowork shows it as "Temporary space").
  await expect(dirs.getByText("临时空间")).toBeVisible();

  // Add a folder — the gate defaults to read-only (Allow writes OFF). The Browse button works
  // in the BROWSER too (sidecar-opened native picker; owner report 2026-07-04).
  await dirs.getByRole("button", { name: "+ 授权访问文件夹…", exact: true }).click();
  await dirs.getByRole("button", { name: "选择位置", exact: true }).click();
  await expect(dirs.getByPlaceholder(/选择或粘贴文件夹路径/)).toHaveValue(
    "/tmp/picked-folder",
  );
  const allowWrites = dirs.locator(".addfolder-write input[type=checkbox]");
  await expect(allowWrites).not.toBeChecked();
  await dirs.getByPlaceholder(/选择或粘贴文件夹路径/).fill("/tmp/ro-data");
  await dirs.getByRole("button", { name: "添加", exact: true }).click();

  const roRow = dirs.locator(".root-row").filter({ hasText: "/tmp/ro-data" });
  await expect(roRow.getByRole("button", { name: "只读", exact: true })).toBeVisible();

  // Add another, this time opting into writes → it lands read-write.
  await dirs.getByRole("button", { name: "+ 授权访问文件夹…", exact: true }).click();
  await dirs.getByPlaceholder(/选择或粘贴文件夹路径/).fill("/tmp/rw-data");
  await dirs.locator(".addfolder-write input[type=checkbox]").check();
  await dirs.getByRole("button", { name: "添加", exact: true }).click();

  const rwRow = dirs.locator(".root-row").filter({ hasText: "/tmp/rw-data" });
  await expect(rwRow.getByRole("button", { name: "可读写", exact: true })).toBeVisible();

  // Flip the read-only one to read-write via its access button (upsert re-add).
  await roRow.getByRole("button", { name: "只读", exact: true }).click();
  await expect(roRow.getByRole("button", { name: "可读写", exact: true })).toBeVisible();

  // Remove a non-primary folder — the primary can't be removed.
  await rwRow.getByTitle("移除").click();
  await expect(dirs.locator(".root-row").filter({ hasText: "/tmp/rw-data" })).toHaveCount(0);
});
