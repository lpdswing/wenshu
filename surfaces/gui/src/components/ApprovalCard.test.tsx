import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ApprovalCard } from "./ApprovalCard";
import { InboxItemCard } from "./InboxItemCard";
import type { Item } from "../types";
import type { InboxItem } from "../api";

type ApprovalItem = Extract<Item, { kind: "approval" }>;

const RUN_TASK = { id: "task-1", title: "Weekly digest" };

const sendApproval = (extra: Partial<ApprovalItem> = {}): ApprovalItem => ({
  kind: "approval",
  name: "send_message",
  args: { target: "slack:T1/C1", text: "digest" },
  reason: "requires approval",
  category: "messaging",
  ...extra,
});

afterEach(cleanup);

describe("ApprovalCard — standing scoped approvals (§25)", () => {
  it("offers Allow every time only with BOTH a run context and an eligible target", () => {
    const onApprove = vi.fn();
    // Run context + standing target → offered (and it replaces the session-scoped button).
    render(
      <ApprovalCard
        item={sendApproval({ standingTarget: "slack:T1/C1" })}
        onApprove={onApprove}
        runTask={RUN_TASK}
      />,
    );
    fireEvent.click(screen.getByText("此自动化始终允许"));
    expect(onApprove).toHaveBeenCalledWith("always_task");
    expect(screen.queryByText("本次会话始终允许")).toBeNull();
    cleanup();

    // No run context (a plain session) → never offered.
    render(
      <ApprovalCard item={sendApproval({ standingTarget: "slack:T1/C1" })} onApprove={vi.fn()} />,
    );
    expect(screen.queryByText("此自动化始终允许")).toBeNull();
    cleanup();

    // Run context but no eligible target (e.g. run_shell) → never offered.
    render(
      <ApprovalCard
        item={sendApproval({ name: "run_shell", args: { command: "ls" }, standingTarget: undefined })}
        onApprove={vi.fn()}
        runTask={RUN_TASK}
      />,
    );
    expect(screen.queryByText("此自动化始终允许")).toBeNull();
  });

  it("honors host one-shot metadata for tools without a hardcoded name", () => {
    render(
      <ApprovalCard
        item={sendApproval({
          name: "future_paid_operation",
          onceOnly: true,
          standingTarget: "external-target",
        })}
        onApprove={vi.fn()}
        runTask={RUN_TASK}
      />,
    );

    expect(screen.getByRole("button", { name: "批准一次" })).toBeTruthy();
    expect(screen.queryByText("本次会话始终允许")).toBeNull();
    expect(screen.queryByText("此自动化始终允许")).toBeNull();
  });

  it("renders the create_scheduled_task consent proposal: reads disclose, writes grant", () => {
    render(
      <ApprovalCard
        item={sendApproval({
          name: "create_scheduled_task",
          args: {
            title: "Weekly digest",
            instructions: "post it",
            cron: "0 9 * * 1",
            permissions: [
              { tool: "send_message", target: "slack:T1/C1", access: "write" },
              { tool: "github_list_commits", target: "rohit/agent-platform", access: "read" },
            ],
          },
        })}
        onApprove={vi.fn()}
      />,
    );
    const grants = screen.getByTestId("approval-grants");
    expect(grants.textContent).toContain("slack:T1/C1");
    expect(grants.textContent).toContain("批准后将始终允许");
    expect(grants.textContent).toContain("rohit/agent-platform");
    expect(grants.textContent).toContain("只读");
    // The raw permissions JSON must not also dump into the args line.
    expect(screen.queryByText(/permissions=/)).toBeNull();
  });
});

describe("ApprovalCard — article image generation", () => {
  it("shows article, provider, model and total before offering one-time approval", () => {
    const onApprove = vi.fn();
    render(
      <ApprovalCard
        item={sendApproval({
          name: "generate_article_assets",
          category: "content-generation",
          standingTarget: "article-assets",
          args: {
            article_path: "drafts/article.md",
            reviewed_hash: "secret-reviewed-hash",
            cover_request: { prompt: "raw cover prompt" },
            illustration_plan: [{ prompt: "raw illustration prompt" }],
            article_title: "文枢内容流水线",
            provider: "OpenAI",
            model: "gpt-image-2",
            total_images: 4,
          },
        })}
        onApprove={onApprove}
        runTask={RUN_TASK}
      />,
    );

    const details = screen.getByTestId("image-generation-details");
    const card = details.closest(".approval");
    expect(card?.textContent).toContain("文枢内容流水线");
    expect(screen.getByText("OpenAI · gpt-image-2")).toBeTruthy();
    expect(screen.getByText("预计生成 4 张图片")).toBeTruthy();
    expect(screen.getByText("将调用外部图片生成服务")).toBeTruthy();
    expect(screen.queryByText(/secret-reviewed-hash|raw cover prompt|raw illustration prompt/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "批准并生成" }));
    expect(onApprove).toHaveBeenCalledWith("once");
    expect(screen.getByRole("button", { name: "拒绝" })).toBeTruthy();
    expect(screen.queryByText("本次会话始终允许")).toBeNull();
    expect(screen.queryByText("此自动化始终允许")).toBeNull();
  });

  it("uses safe Chinese fallbacks and keeps the resolved layout when host display fields are absent", () => {
    render(
      <ApprovalCard
        item={sendApproval({
          name: "generate_article_assets",
          args: { article_path: "drafts/article.md", reviewed_hash: "secret-reviewed-hash" },
          resolved: "once",
        })}
        onApprove={vi.fn()}
      />,
    );

    const card = screen.getByTestId("image-generation-details").closest(".approval");
    expect(card?.textContent).toContain("article.md");
    expect(card?.textContent).toContain("外部图片服务 · 模型待确认");
    expect(card?.textContent).toContain("图片数量以本次生成计划为准");
    expect(card?.textContent).toContain("已处理：批准一次");
    expect(card?.textContent).not.toMatch(/undefined|NaN/);
    expect(screen.queryByRole("button", { name: "批准并生成" })).toBeNull();
    expect(screen.queryByRole("button", { name: "拒绝" })).toBeNull();
  });
});

describe("ApprovalCard — WeChat draft creation", () => {
  it("renders only the shared safe display fields and permits a one-time draft save", () => {
    const onApprove = vi.fn();
    const { container } = render(
      <ApprovalCard
        item={sendApproval({
          name: "create_wechat_draft",
          category: "connector",
          standingTarget: "wechat_official:default",
          args: {
            channel: "微信公众号",
            title: "文枢内容流水线",
            digest: "先审文字，再完成图文排版。",
            cover_path: "drafts/assets/cover.png",
            image_count: 2,
            theme: "classic",
            color: "#1f4d3a",
            draft_only: true,
            article_path: "/Users/test/private/article.md",
            preview_hash: "secret-preview-hash",
            app_id: "wx-full-appid",
            app_secret: "app-secret",
            access_token: "access-token",
            html: "<article>整篇敏感 HTML</article>",
          },
        })}
        onApprove={onApprove}
        runTask={RUN_TASK}
      />,
    );

    const details = screen.getByTestId("wechat-draft-details");
    expect(details.textContent).toContain("微信公众号");
    expect(details.textContent).toContain("文枢内容流水线");
    expect(details.textContent).toContain("先审文字，再完成图文排版。");
    expect(details.textContent).toContain("封面 cover.png");
    expect(details.textContent).toContain("正文图片 2 张");
    expect(details.textContent).toContain("主题 classic · 配色 #1f4d3a");
    expect(screen.getByText("只保存到草稿箱，不会正式发布")).toBeTruthy();
    expect(screen.getByText("将保存到微信公众号草稿箱")).toBeTruthy();
    expect(container.textContent).not.toMatch(
      /\/Users\/test|secret-preview-hash|wx-full-appid|app-secret|access-token|敏感 HTML|drafts\/assets/,
    );

    fireEvent.click(screen.getByRole("button", { name: "批准并保存草稿" }));
    expect(onApprove).toHaveBeenCalledWith("once");
    expect(screen.queryByText("本次会话始终允许")).toBeNull();
    expect(screen.queryByText("此自动化始终允许")).toBeNull();
    expect(screen.queryByText("始终允许此命令")).toBeNull();
  });

  it("uses safe Chinese fallbacks when optional display fields are absent", () => {
    const { container } = render(
      <ApprovalCard
        item={sendApproval({
          name: "create_wechat_draft",
          args: { channel: "unexpected", draft_only: true },
          resolved: "once",
        })}
        onApprove={vi.fn()}
      />,
    );

    const details = screen.getByTestId("wechat-draft-details");
    expect(details.textContent).toContain("微信公众号");
    expect(details.textContent).toContain("标题待确认");
    expect(details.textContent).toContain("摘要待确认");
    expect(details.textContent).toContain("封面待确认");
    expect(details.textContent).toContain("正文图片数量待确认");
    expect(details.textContent).toContain("主题待确认");
    expect(container.textContent).not.toMatch(/undefined|NaN|\[object Object\]/);
    expect(screen.queryByRole("button", { name: "批准并保存草稿" })).toBeNull();
  });
});

describe("ApprovalCard — §35 shapes", () => {
  it("routine file writes render as a compact row: humanized title, inline preview, Allow → once", () => {
    const onApprove = vi.fn();
    render(
      <ApprovalCard
        item={sendApproval({
          name: "write_file",
          args: { path: "src/fetch_data.py", content: "import json\nimport urllib\nx=1\ny=2\nz=3\ndone=1" },
          category: undefined,
        })}
        onApprove={onApprove}
      />,
    );
    const row = screen.getByTestId("approval-row");
    expect(row.textContent).toContain("写入 ");
    expect(row.textContent).toContain("fetch_data.py");
    expect(screen.queryByText(/Permission required/i)).toBeNull();

    // Preview expands INLINE from the tool args (the file doesn't exist yet).
    expect(screen.queryByText(/import json/)).toBeNull();
    fireEvent.click(screen.getByText(/预览/));
    expect(screen.getByText(/import json/)).toBeTruthy();
    expect(screen.getByText("显示全部 6 行")).toBeTruthy();

    fireEvent.click(screen.getByText("批准"));
    expect(onApprove).toHaveBeenCalledWith("once");
  });

  it("send_file gets the full external card: destination title, file chip, leaves-the-Mac note", () => {
    render(
      <ApprovalCard
        item={sendApproval({
          name: "send_file",
          args: { target: "slack:T1/C9:1700.1", path: "out/report.pdf", comment: "here you go" },
        })}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.getByText(/发送文件至/).textContent).toContain("C9");
    expect(screen.getByText(/将离开此 Mac → Slack/)).toBeTruthy();
    expect(screen.getByText(/report\.pdf/)).toBeTruthy();
    expect(screen.getByText(/here you go/)).toBeTruthy();
    expect(screen.getByText("批准一次")).toBeTruthy();
  });

  it("long single-paragraph send_message text is clamped, expandable, and never a wall", () => {
    // Owner repro 2026-07-15: a one-paragraph Slack digest (no newlines) blew the card
    // up to full-transcript height — the preview clamped by LINES only.
    const digest = "aisuite last 24 hours of work: five PRs merged covering streaming, multimodal input, Slack improvements, human attribution, and formatting. ".repeat(8);
    render(<ApprovalCard item={sendApproval({ args: { target: "slack:T1/C1", text: digest } })} onApprove={vi.fn()} />);

    const prev = document.querySelector(".approval-prev") as HTMLElement;
    expect(prev.textContent!.length).toBeLessThan(500);
    fireEvent.click(screen.getByText("显示完整消息"));
    expect(document.querySelector(".approval-prev")!.textContent!.length).toBeGreaterThan(1000);
    expect(screen.getByText("收起")).toBeTruthy();
  });

  it("short send_message text keeps the inline quote (no preview box)", () => {
    render(<ApprovalCard item={sendApproval()} onApprove={vi.fn()} />);
    expect(screen.getByText(/“digest”/)).toBeTruthy();
    expect(document.querySelector(".approval-prev")).toBeNull();
  });

  it("run_shell titles with the model's description and previews the command", () => {
    render(
      <ApprovalCard
        item={sendApproval({
          name: "run_shell",
          args: { command: "python3 fetch.py > data.json", description: "Fetch semiconductor stock data" },
          category: undefined,
        })}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.getByText(/运行命令 — Fetch semiconductor stock data/)).toBeTruthy();
    expect(screen.getByText(/python3 fetch\.py/)).toBeTruthy();
    expect(screen.getByText(/仅在此 Mac 上执行/)).toBeTruthy();
    expect(screen.getByText("始终允许此命令")).toBeTruthy();
  });
});

describe("InboxItemCard — Allow every time on parked run approvals", () => {
  const baseItem = (data?: Record<string, any>): InboxItem => ({
    id: "i1",
    session_id: "__run__r1",
    kind: "approval",
    title: "Run `send_message`?",
    body: "target: slack:T1/C1",
    state: "pending",
    resolution: null,
    inbox: "default",
    created_at: "",
    resolved_at: null,
    data,
  });

  it("shows the button only when the item carries the task binding + target", () => {
    const onResolve = vi.fn();
    render(
      <InboxItemCard
        item={baseItem({ task_id: "task-1", task_title: "Weekly digest", standing_target: "slack:T1/C1" })}
        onResolve={onResolve}
      />,
    );
    fireEvent.click(screen.getByText("此自动化始终允许"));
    expect(onResolve).toHaveBeenCalledWith("i1", "always_task");
    cleanup();

    // A plain unattended-session approval (no task data) keeps Approve/Deny only.
    render(<InboxItemCard item={baseItem()} onResolve={vi.fn()} />);
    expect(screen.queryByText("此自动化始终允许")).toBeNull();
    expect(screen.getByText("批准")).toBeTruthy();
    expect(screen.getByText("拒绝")).toBeTruthy();
  });

  it("keeps parked image generation one-time and hides raw approval payloads", () => {
    const onResolve = vi.fn();
    render(
      <InboxItemCard
        item={{
          ...baseItem({
            tool: "generate_article_assets",
            arguments: {
              article_path: "drafts/article.md",
              article_title: "文枢内容流水线",
              provider: "OpenAI",
              model: "gpt-image-2",
              total_images: 4,
            },
            task_id: "task-1",
            task_title: "Weekly digest",
            standing_target: "article-assets",
          }),
          body: "reviewed_hash=secret-reviewed-hash prompt=raw image prompt",
        }}
        onResolve={onResolve}
      />,
    );

    expect(screen.getByText("OpenAI · gpt-image-2")).toBeTruthy();
    expect(screen.getByText("预计生成 4 张图片")).toBeTruthy();
    expect(screen.getByText("将调用外部图片生成服务")).toBeTruthy();
    expect(screen.queryByText(/secret-reviewed-hash|raw image prompt/)).toBeNull();
    expect(screen.queryByText("此自动化始终允许")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "批准并生成" }));
    expect(onResolve).toHaveBeenCalledWith("i1", "allow");
    expect(screen.getByRole("button", { name: "拒绝" })).toBeTruthy();
  });

  it("parked approvals with tool data wear the §35 dress — same dialect as the live card", () => {
    const onResolve = vi.fn();
    render(
      <InboxItemCard
        item={baseItem({
          tool: "write_file",
          arguments: { path: "src/fetch_data.py", content: "import json\nx = 1" },
        })}
        onResolve={onResolve}
      />,
    );
    // Humanized title + preview from the args; the raw "Run `write_file`?" title is gone.
    expect(screen.getByText("fetch_data.py")).toBeTruthy();
    expect(screen.queryByText("Run `send_message`?")).toBeNull();
    expect(screen.getByText(/import json/)).toBeTruthy();
    expect(screen.getByText(/仅在此 Mac 上执行/)).toBeTruthy();
    // §35 labels; resolution vocabulary unchanged (works on every approver path).
    fireEvent.click(screen.getByText("批准一次"));
    expect(onResolve).toHaveBeenCalledWith("i1", "allow");
    // Old rows without tool data keep the legacy treatment (covered above).
  });
});
