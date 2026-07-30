import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { InboxItem } from "../api";
import { InboxItemCard } from "./InboxItemCard";

function wechatApproval(): InboxItem {
  return {
    id: "wechat-approval",
    session_id: "session-1",
    kind: "approval",
    title: "Save draft?",
    body: "",
    state: "pending",
    resolution: null,
    inbox: "default",
    created_at: "2026-07-29T00:00:00Z",
    resolved_at: null,
    data: {
      tool: "create_wechat_draft",
      arguments: {
        channel: "微信公众号",
        title: "文枢内容流水线",
        digest: "安全摘要",
        cover_path: "images/cover.png",
        image_count: 2,
        theme: "default",
        color: "#07C160",
        draft_only: true,
      },
      task_id: "task-1",
      task_title: "公众号草稿",
      standing_target: "forbidden-target",
      approval_once_only: true,
    },
  };
}

describe("InboxItemCard WeChat approval", () => {
  it("offers only the one-shot draft action for parked automation approvals", () => {
    const onResolve = vi.fn();
    const { container } = render(
      <InboxItemCard item={wechatApproval()} onResolve={onResolve} />,
    );

    expect(screen.getByText("只保存到草稿箱，不会正式发布")).toBeTruthy();
    expect(screen.getByText("正文图片 2 张")).toBeTruthy();
    expect(screen.queryByText("此自动化始终允许")).toBeNull();
    expect(container.textContent).not.toMatch(/forbidden-target|AppSecret|access_token/);

    fireEvent.click(screen.getByRole("button", { name: "批准并保存草稿" }));
    expect(onResolve).toHaveBeenCalledWith("wechat-approval", "allow");
  });
});
