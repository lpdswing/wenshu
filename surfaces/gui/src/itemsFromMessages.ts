// Maps the raw transcript from GET /v1/sessions/{id}/messages into the GUI's `Item[]` model.
// Extracted from App.tsx so it can be unit-tested without standing up the whole app.
//
// A connector-delivered user message carries a structured `source` sidecar (§3.1); when present it
// becomes a `connector` item (rendered as ConnectorMessageCard) instead of a plain user bubble. This
// generalizes to any connector via the registry — no Slack special-casing here.

import type { ConversationMessage } from "./api";
import type { Attachment, Item } from "./types";

const MODEL_SWITCH_PREFIX = "Model switched to ";
const MODEL_SWITCH_IMAGE_WARNING = " — earlier images can't be read by this model";

const modelNotice = (label: string, imageWarning = false) =>
  (label ? `已切换模型：${label}` : "已切换模型") +
  (imageWarning ? " — 此模型无法读取之前的图片" : "");

export function modelChangeHasImageWarning(text: unknown): boolean {
  return (
    typeof text === "string" &&
    text.startsWith(MODEL_SWITCH_PREFIX) &&
    text.endsWith(MODEL_SWITCH_IMAGE_WARNING)
  );
}

export function modelChangedNotice(
  model: unknown,
  labels: Readonly<Record<string, string>> = {},
  imageWarning = false,
): string {
  if (typeof model !== "string" || !model) return modelNotice("");
  const label =
    labels[model] || (model.includes(":") ? model.split(":").slice(1).join(":") : model);
  return modelNotice(label, imageWarning);
}

function replayedModelChangeNotice(text: unknown): string {
  if (typeof text !== "string") return modelNotice("");
  if (text.startsWith("已切换模型")) return text;
  if (!text.startsWith(MODEL_SWITCH_PREFIX)) return modelNotice("");

  const persisted = text.slice(MODEL_SWITCH_PREFIX.length);
  const warned = modelChangeHasImageWarning(text);
  const label = warned
    ? persisted.slice(0, -MODEL_SWITCH_IMAGE_WARNING.length)
    : persisted;
  return modelNotice(label, warned);
}

export function itemsFromMessages(messages: ConversationMessage[]): Item[] {
  const items: Item[] = [];
  // Index tool results by tool_call_id so replayed tool rows can show their output
  // (the live view gets this from `tool_finished` events; on replay it's the `role:"tool"` msgs).
  const results: Record<string, string> = {};
  // `_display` sidecar on a tool message = user-facing metadata the agent never saw
  // (e.g. how many hits the privacy filters hid) — surfaces on the tool card.
  const hiddenCounts: Record<string, number> = {};
  const displays: Record<string, Record<string, unknown>> = {};
  for (const m of messages || []) {
    if (m.role === "tool" && m.tool_call_id) {
      results[m.tool_call_id] =
        typeof m.content === "string" ? m.content : JSON.stringify(m.content);
      const hidden = Number(m._display?.hidden_by_filters || 0);
      if (hidden > 0) hiddenCounts[m.tool_call_id] = hidden;
      const rawDisplay: unknown = m._display;
      if (
        rawDisplay &&
        typeof rawDisplay === "object" &&
        "wechat_draft_result" in rawDisplay
      ) {
        displays[m.tool_call_id] = {
          wechat_draft_result: rawDisplay.wechat_draft_result,
        };
      }
    }
  }
  for (const m of messages || []) {
    if (m.role === "user") {
      // Connector message → structured card; the framed `content` stays for the model, but display
      // renders from the source sidecar.
      if (m.source?.connector) {
        items.push({ kind: "connector", source: m.source });
        continue;
      }
      const user = userItemFromContent(m.content);
      // `ts` (unix seconds) is the server's canonical-message stamp; older sessions have none.
      if (typeof m.ts === "number") user.ts = m.ts;
      if (user.text || user.attachments?.length) items.push(user);
    } else if (m.role === "assistant") {
      if (m.content || m.reasoning)
        items.push({
          kind: "assistant",
          text: m.content || "",
          ...(typeof m.ts === "number" ? { ts: m.ts } : {}),
          ...(m.reasoning ? { reasoning: m.reasoning } : {}),
        });
      for (const tc of m.tool_calls || []) {
        let args: any = {};
        try {
          args = JSON.parse(tc.function?.arguments || "{}");
        } catch {
          args = {};
        }
        const preview = results[tc.id];
        const hidden = hiddenCounts[tc.id];
        const display = displays[tc.id];
        items.push({
          kind: "tool",
          id: tc.id,
          name: tc.function?.name,
          args,
          status: "ok",
          preview,
          ...(hidden ? { hidden } : {}),
          ...(display ? { display } : {}),
        });
      }
    } else if (m.role === "notice") {
      // Persisted markers (engine `_append_notice`): error/interrupted/model-switch survive
      // reload exactly like the live view rendered them. An error notice is retriable —
      // the Transcript only offers the button when it's the transcript tail.
      items.push(
        m.kind === "interrupted"
          ? { kind: "notice", tone: "warn", text: "已中断。" }
          : m.kind === "model_switch"
            ? { kind: "notice", tone: "info", text: replayedModelChangeNotice(m.text) }
            : { kind: "notice", tone: "warn", text: "错误：" + (m.text || "未知错误"), retriable: true },
      );
    }
    // system messages are omitted; tool-result messages are folded into the tool row above
  }
  return items;
}

export function userItemFromContent(content: any): Extract<Item, { kind: "user" }> {
  if (typeof content === "string") return { kind: "user", text: content };
  if (!Array.isArray(content)) return { kind: "user", text: "" };

  const text: string[] = [];
  const attachments: Attachment[] = [];
  for (const part of content) {
    if (!part || typeof part !== "object") continue;
    if (part.type === "text" && part.text) {
      text.push(String(part.text));
    } else if (part.type === "image_url") {
      const url = part.image_url?.url;
      if (typeof url === "string" && url.startsWith("data:image/")) {
        attachments.push({ kind: "image", name: "image", data_url: url });
      }
    }
  }
  return { kind: "user", text: text.join("\n\n"), attachments };
}
