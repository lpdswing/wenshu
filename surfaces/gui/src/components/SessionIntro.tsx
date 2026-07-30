import { useEffect, useState } from "react";
import { getConnectors, getSessionConnections } from "../api";
import type { Attachment } from "../types";
import { ConnectorIcon } from "../connectors/ConnectorIcon";
import { indexConnectors, visualFor, type ConnectorMap } from "../connectors/visuals";
import { useRoots } from "../useRoots";
import { AddFolderForm } from "./AddFolderForm";

const CONTENT_SUGGESTIONS = [
  {
    title: "整理这些资料，先生成一版文章草稿。",
    detail: "梳理素材、提炼重点，先形成一版可审阅的文章",
  },
  {
    title: "审阅文章后，为它规划封面和正文配图。",
    detail: "根据文章结构给出图片主题、位置和制作要求",
  },
  {
    title: "把确认后的文章整理成公众号草稿。",
    detail: "整理标题、摘要、正文层级和发布前检查项",
  },
];

const FOLDER_PROMPT = "分析这个文件夹中的文件并总结重点。";
const HUBSPOT_PROMPT =
  "根据我最近的 HubSpot 潜在客户生成报告：整理来源、阶段以及需要跟进的对象。";
const GH_SLACK_PROMPT =
  "设置每周进展报告：汇总 GitHub 仓库动态，并在每周五上午发布到 Slack。";

export function SessionIntro({
  sessionId,
  onOpenSessionSettings,
  onPrefill,
}: {
  sessionId: string;
  onOpenSessionSettings: () => void;
  onPrefill: (text: string, attachments?: Attachment[]) => void;
}) {
  const { roots, busy, error, addRoot } = useRoots(sessionId);
  const [live, setLive] = useState<Set<string>>(new Set());
  const [byName, setByName] = useState<ConnectorMap>({});
  const [addingFolder, setAddingFolder] = useState(false);

  useEffect(() => {
    getSessionConnections(sessionId)
      .then((connections) =>
        setLive(
          new Set(
            connections.connected.filter((connection) => connection.enabled).map((connection) => connection.connector),
          ),
        ),
      )
      .catch(() => {});
    getConnectors()
      .then((connectors) => setByName(indexConnectors(connectors)))
      .catch(() => {});
  }, [sessionId]);

  const shared = roots.filter((root) => !root.primary);
  const hubspotAvailable = byName.hubspot?.available === true;
  const ghSlackAvailable = byName.github?.available === true && byName.slack?.available === true;
  const hubspotReady = live.has("hubspot");
  const ghSlackReady = live.has("github") && live.has("slack");

  const dot = (name: string, on: boolean) => (
    <span className={"task-dot" + (on ? "" : " off")} key={name}>
      <ConnectorIcon connector={visualFor(name, "connector", byName)} size={12} />
    </span>
  );

  const pickFolder = () => {
    if (shared.length > 0) onPrefill(FOLDER_PROMPT);
    else setAddingFolder((visible) => !visible);
  };

  return (
    <div className="intro">
      <h1 className="greeting">
        <span className="mark">✦</span> 今天想创作什么内容？
      </h1>
      <p className="intro-lede">选择一个工作流，文枢会先把请求填入输入框，等你确认后再开始。</p>

      <div className="intro-tasks intro-content-recommendations">
        {CONTENT_SUGGESTIONS.map((suggestion) => (
          <button
            className="task-card"
            key={suggestion.title}
            onClick={() => onPrefill(suggestion.title)}
          >
            <span className="task-card-body">
              <span className="task-card-title">{suggestion.title}</span>
              <span className="task-card-sub">{suggestion.detail}</span>
            </span>
            <span className="task-card-act">填入 →</span>
          </button>
        ))}
      </div>

      <div className="suggest-head">准备资料来源（可选）</div>
      <div className="intro-tasks intro-setup-actions">
        <button className="task-card" data-testid="intro-task-folder" onClick={pickFolder}>
          <span className="task-card-body">
            <span className="task-card-title">分析文件夹中的资料</span>
            <span className="task-card-sub">读取文件并总结其中的重点</span>
          </span>
          <span className="task-card-act">选择文件夹 →</span>
        </button>
        {addingFolder && (
          <div className="intro-addfolder">
            <AddFolderForm
              startOpen
              busy={busy}
              onAdd={async (path, writable) => {
                const ok = await addRoot(path, writable);
                if (ok !== false) onPrefill(FOLDER_PROMPT);
                return ok;
              }}
              onDismiss={() => setAddingFolder(false)}
            />
            {error && <div className="roots-err">{error}</div>}
          </div>
        )}

        {hubspotAvailable && (
        <button
          className={"task-card" + (hubspotReady ? "" : " gated")}
          data-testid="intro-task-hubspot"
          onClick={() => (hubspotReady ? onPrefill(HUBSPOT_PROMPT) : onOpenSessionSettings())}
        >
          <span className="task-card-body">
            <span className="task-card-title">根据 HubSpot 潜在客户生成报告</span>
            <span className="task-card-sub">
              {dot("hubspot", hubspotReady)}
              整理来源、阶段和需要跟进的对象
            </span>
          </span>
          <span className="task-card-act">{hubspotReady ? "开始 →" : "配置 ›"}</span>
        </button>
        )}

        {ghSlackAvailable && (
        <button
          className={"task-card" + (ghSlackReady ? "" : " gated")}
          data-testid="intro-task-github-slack"
          onClick={() => (ghSlackReady ? onPrefill(GH_SLACK_PROMPT) : onOpenSessionSettings())}
        >
          <span className="task-card-body">
            <span className="task-card-title">自动生成每周 GitHub 进展报告并发布到 Slack</span>
            <span className="task-card-sub">
              {dot("github", live.has("github"))}
              {dot("slack", live.has("slack"))}
              汇总仓库动态，并在每周五发布
            </span>
          </span>
          <span className="task-card-act">{ghSlackReady ? "开始 →" : "配置 ›"}</span>
        </button>
        )}
      </div>
    </div>
  );
}
