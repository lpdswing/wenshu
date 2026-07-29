// The `_display` sidecar on a persisted tool message (privacy-filter hidden
// counts) must surface on the replayed tool item — and only there; the
// agent-visible content string carries no trace.
import { describe, expect, it } from "vitest";
import { itemsFromMessages } from "./itemsFromMessages";

describe("itemsFromMessages _display sidecar", () => {
  it("attaches hidden counts to the matching tool item", () => {
    const items = itemsFromMessages([
      { role: "user", content: "check my mail" },
      {
        role: "assistant",
        content: "",
        tool_calls: [
          { id: "t1", function: { name: "gmail_search_messages", arguments: "{}" } },
          { id: "t2", function: { name: "gmail_get_message", arguments: "{}" } },
        ],
      },
      {
        role: "tool",
        tool_call_id: "t1",
        content: '{"ok": true, "data": {"messages": []}}',
        _display: { hidden_by_filters: 2, connector: "gmail" },
      },
      { role: "tool", tool_call_id: "t2", content: '{"ok": true}' },
    ] as any);

    const tools = items.filter((i: any) => i.kind === "tool") as any[];
    expect(tools).toHaveLength(2);
    expect(tools[0].hidden).toBe(2);
    expect(tools[1].hidden).toBeUndefined();
    expect(tools[0].preview).not.toContain("hidden"); // content stays clean
  });
});

  it("preserves structured WeChat result display metadata on replay", () => {
    const summary = {
      status: "unknown",
      title: "文枢内容流水线",
      error_kind: "transport",
      uploaded_asset_count: 3,
      draft_only: true,
    };
    const items = itemsFromMessages([
      {
        role: "assistant",
        content: "",
        tool_calls: [
          {
            id: "wechat-call",
            function: {
              name: "create_wechat_draft",
              arguments: '{"article_path":"article.md"}',
            },
          },
        ],
      },
      {
        role: "tool",
        tool_call_id: "wechat-call",
        content: '{"status":"unknown","title":"truncated',
        _display: { wechat_draft_result: summary },
      },
    ]);

    const tool = items.find((item) => item.kind === "tool");
    expect(tool?.kind).toBe("tool");
    if (tool?.kind !== "tool") throw new Error("tool item missing");
    expect(tool.display?.wechat_draft_result).toEqual(summary);
  });

describe("itemsFromMessages timestamps", () => {
  it("carries the server ts through to user/assistant items; pre-stamp history gets none", () => {
    const items = itemsFromMessages([
      { role: "user", content: "hi", ts: 1752969720 },
      { role: "assistant", content: "hello", ts: 1752969724 },
      { role: "user", content: "old message" }, // saved before the server stamped ts
    ] as any);

    expect(items).toEqual([
      { kind: "user", text: "hi", ts: 1752969720 },
      { kind: "assistant", text: "hello", ts: 1752969724 },
      { kind: "user", text: "old message" },
    ]);
  });
});

describe("itemsFromMessages notices", () => {
  it("replays persisted error/interrupted markers; only errors are retriable", () => {
    const items = itemsFromMessages([
      { role: "user", content: "hi" },
      { role: "assistant", content: "partial ans" },
      { role: "notice", kind: "interrupted", ts: 1752969720 },
      { role: "user", content: "again" },
      { role: "notice", kind: "error", text: "model down", ts: 1752969724 },
    ] as any);

    expect(items).toEqual([
      { kind: "user", text: "hi" },
      { kind: "assistant", text: "partial ans" },
      { kind: "notice", tone: "warn", text: "已中断。" },
      { kind: "user", text: "again" },
      { kind: "notice", tone: "warn", text: "错误：model down", retriable: true },
    ]);
  });
});

describe("itemsFromMessages model switch", () => {
  it("replays the persisted model_switch marker as an info notice", () => {
    const items = itemsFromMessages([
      { role: "user", content: "hi" },
      { role: "notice", kind: "model_switch", text: "Model switched to Kimi K2.6 · Moonshot" },
    ] as any);
    expect(items[1]).toEqual({
      kind: "notice",
      tone: "info",
      text: "已切换模型：Kimi K2.6 · Moonshot",
    });
  });

  it("translates the persisted image warning without changing the model label", () => {
    const items = itemsFromMessages([
      {
        role: "notice",
        kind: "model_switch",
        text: "Model switched to Claude Opus 4.8 · Anthropic — earlier images can't be read by this model",
      },
    ]);
    expect(items[0]).toEqual({
      kind: "notice",
      tone: "info",
      text: "已切换模型：Claude Opus 4.8 · Anthropic — 此模型无法读取之前的图片",
    });
  });
});

describe("itemsFromMessages reasoning", () => {
  it("attaches the reasoning sidecar to assistant items; thinking-only messages still render", () => {
    const items = itemsFromMessages([
      { role: "user", content: "hi" },
      { role: "assistant", content: "answer", reasoning: "let me think" },
      { role: "assistant", content: "", reasoning: "stopped mid-thought" },
    ] as any);
    expect(items[1]).toEqual({ kind: "assistant", text: "answer", reasoning: "let me think" });
    expect(items[2]).toEqual({ kind: "assistant", text: "", reasoning: "stopped mid-thought" });
  });
});
