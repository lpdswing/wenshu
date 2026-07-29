// UX-015 (§33): tool calls render as concise one-liners. The model does NOT emit a purpose
// per call — the stream is name+args+result — so the sentence is synthesized here from
// per-tool templates. `run_shell` is the exception: its optional `description` argument is
// model-written intent and is preferred when present. Fallback: "Used <tool> — <short args>".

import { shortArgs } from "./components/ApprovalCard";

// A one-line sentence in three segments so the UI can emphasize the object:
// "Read " + <b>runbook.md</b> + " from the shared folder".
export interface HumanLine {
  pre: string;
  obj?: string;
  post?: string;
}

const trunc = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
const baseName = (p: string) => p.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;
const articleLabel = (args: Record<string, unknown>): string => {
  const titleValue = args.article_title ?? args.title;
  const title = typeof titleValue === "string" ? titleValue.trim() : "";
  if (title) return title;
  const path = typeof args.article_path === "string" ? args.article_path.trim() : "";
  return path ? baseName(path) || "文章" : "文章";
};
const TODO_STATUS_LABELS: Record<string, string> = {
  pending: "待处理",
  in_progress: "进行中",
  completed: "已完成",
  done: "已完成",
};

// send_message targets are "platform:chat" or "platform:chat:thread" — show the platform
// by name and the last human-ish segment of the chat id.
function messageTarget(target: string): { platform: string; tail: string } {
  const [platform, ...rest] = String(target).split(":");
  const chat = rest[0] || "";
  const tail = chat.includes("/") ? chat.split("/").pop() || chat : chat;
  const names: Record<string, string> = { slack: "Slack", telegram: "Telegram" };
  return { platform: names[platform] || platform, tail };
}

export function humanizeTool(name: string, args: any): HumanLine {
  const a = args && typeof args === "object" ? args : {};
  switch (name) {
    case "run_shell": {
      const cmd = trunc(String(a.command ?? ""), 60);
      const desc = typeof a.description === "string" && a.description.trim() ? a.description.trim() : "";
      const pre = a.run_in_background ? "已在后台启动：" : "已运行 ";
      return {
        pre,
        obj: cmd,
        ...(desc ? { post: ` — ${desc}` } : {}),
      };
    }
    case "shell_task_output":
      return { pre: "已检查后台命令" };
    case "shell_task_kill":
      return { pre: "已停止后台命令" };
    case "read_file":
      return { pre: "已读取 ", obj: baseName(String(a.path ?? "文件")) };
    case "write_file":
      return { pre: "已写入 ", obj: baseName(String(a.path ?? "文件")) };
    case "replace_in_file":
    case "apply_patch":
    case "apply_unified_diff":
      return { pre: "已编辑 ", obj: a.path ? baseName(String(a.path)) : "文件" };
    case "grep":
      return { pre: "Searched the code for ", obj: `“${trunc(String(a.pattern ?? ""), 40)}”` };
    case "git_log":
      return { pre: "Looked through recent git history" };
    case "todo_write": {
      // `todos` is current; `items` renders histories from before the rename (the old
      // key breaks Together's GLM-5.2 chat template — see coworker/tools/todo.py).
      const items = Array.isArray(a.todos) ? a.todos : Array.isArray(a.items) ? a.items : [];
      if (items.length === 1) {
        const it = items[0] || {};
        const rawStatus = String(it.status || "");
        const status = TODO_STATUS_LABELS[rawStatus] || rawStatus.replace(/_/g, " ");
        return {
          pre: "已更新计划 — ",
          obj: `“${trunc(String(it.content ?? ""), 70)}”`,
          ...(status ? { post: ` → ${status}` } : {}),
        };
      }
      return { pre: `已更新计划 — ${items.length} 项` };
    }
    case "send_message": {
      const { platform, tail } = messageTarget(String(a.target ?? ""));
      if (!tail) return { pre: "Sent a message" };
      return { pre: `Sent a ${platform} message to `, obj: tail };
    }
    case "web_search":
      return { pre: "Searched the web — ", obj: `“${trunc(String(a.query ?? ""), 60)}”` };
    case "web_fetch": {
      let host = String(a.url ?? "");
      try {
        host = new URL(host).host || host;
      } catch {
        /* keep raw */
      }
      return { pre: "Read a web page — ", obj: trunc(host, 50) };
    }
    case "explore":
      return { pre: "Sent a sub-agent to explore — ", obj: `“${trunc(String(a.task ?? a.prompt ?? ""), 60)}”` };
    case "ask_user":
      return { pre: "Asked you a question" };
    case "propose_plan":
      return { pre: "Proposed a plan" };
    case "request_directory":
      return { pre: "Asked for folder access — ", obj: String(a.path ?? "") };
    case "prepare_article_review":
      return { pre: "生成了文章文字预览：", obj: articleLabel(a) };
    case "generate_article_assets":
      return { pre: "为文章生成封面与配图：", obj: articleLabel(a) };
    case "prepare_wechat_draft":
      return { pre: "生成了公众号最终图文预览：", obj: articleLabel(a) };
    case "create_wechat_draft":
      return { pre: "保存到公众号草稿箱：", obj: articleLabel(a) };
    default: {
      const rest = trunc(shortArgs(a), 80);
      return { pre: `Used ${name}`, ...(rest ? { post: ` — ${rest}` } : {}) };
    }
  }
}

// The approval card's headline (§35): the ask, phrased as the action being decided.
// run_shell leads with the model's own description ("Run a command — fetch stock data").
export function humanizeApprovalTitle(name: string, args: any): HumanLine {
  const a = args && typeof args === "object" ? args : {};
  switch (name) {
    case "write_file":
      return { pre: "写入 ", obj: baseName(String(a.path ?? "文件")) };
    case "replace_in_file":
    case "apply_patch":
    case "apply_unified_diff":
      return { pre: "编辑 ", obj: a.path ? baseName(String(a.path)) : "文件" };
    case "run_shell": {
      const desc = typeof a.description === "string" && a.description.trim() ? a.description.trim() : "";
      return {
        pre: "运行命令",
        ...(desc ? { post: ` — ${desc}` } : {}),
      };
    }
    case "send_message": {
      const { tail } = messageTarget(String(a.target ?? ""));
      return tail ? { pre: "发送消息至 ", obj: tail } : { pre: "发送消息" };
    }
    case "send_file": {
      const { tail } = messageTarget(String(a.target ?? ""));
      return tail ? { pre: "发送文件至 ", obj: tail } : { pre: "发送文件" };
    }
    case "prepare_article_review":
      return { pre: "生成文章文字预览：", obj: articleLabel(a) };
    case "generate_article_assets":
      return { pre: "为文章生成封面与配图：", obj: articleLabel(a) };
    case "prepare_wechat_draft":
      return { pre: "生成公众号最终图文预览：", obj: articleLabel(a) };
    case "create_wechat_draft":
      return { pre: "保存到公众号草稿箱：", obj: articleLabel(a) };
    case "create_scheduled_task":
      return a.title
        ? { pre: "创建自动化 ", obj: `“${trunc(String(a.title), 60)}”` }
        : { pre: "创建自动化" };
    default:
      return { pre: `使用 ${name}` };
  }
}

// Approvals with no executed tool call (typically declined): the ask, phrased as intent.
export function humanizeAsk(name: string, args: any): HumanLine {
  const a = args && typeof args === "object" ? args : {};
  switch (name) {
    case "run_shell":
      return { pre: "曾请求运行 ", obj: trunc(String(a.command ?? ""), 60) };
    case "write_file":
      return { pre: "曾请求写入 ", obj: baseName(String(a.path ?? "文件")) };
    case "replace_in_file":
    case "apply_patch":
    case "apply_unified_diff":
      return { pre: "曾请求编辑 ", obj: a.path ? baseName(String(a.path)) : "文件" };
    case "send_message": {
      const { platform, tail } = messageTarget(String(a.target ?? ""));
      if (!tail) return { pre: "曾请求发送消息" };
      return { pre: "曾请求发送消息至 ", obj: tail, post: `（${platform}）` };
    }
    case "prepare_article_review":
      return { pre: "曾请求生成文章文字预览：", obj: articleLabel(a) };
    case "generate_article_assets":
      return { pre: "曾请求为文章生成封面与配图：", obj: articleLabel(a) };
    case "prepare_wechat_draft":
      return { pre: "曾请求生成公众号最终图文预览：", obj: articleLabel(a) };
    case "create_wechat_draft":
      return { pre: "曾请求保存到公众号草稿箱：", obj: articleLabel(a) };
    default:
      return { pre: `曾请求使用 ${name}` };
  }
}
