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

const FOLDER_PROMPT = "Analyze the files in this folder and summarize what matters.";
const HUBSPOT_PROMPT =
  "Create a report on my recent HubSpot leads: sources, stages, and who needs follow-up.";
const GH_SLACK_PROMPT =
  "Set up a weekly progress report: summarize activity in my GitHub repos and post it to Slack every Friday morning.";

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
            <span className="task-card-title">Analyze the files in a directory</span>
            <span className="task-card-sub">I'll read them and summarize what matters</span>
          </span>
          <span className="task-card-act">Pick a folder →</span>
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

        <button
          className={"task-card" + (hubspotReady ? "" : " gated")}
          data-testid="intro-task-hubspot"
          onClick={() => (hubspotReady ? onPrefill(HUBSPOT_PROMPT) : onOpenSessionSettings())}
        >
          <span className="task-card-body">
            <span className="task-card-title">Create a report from my HubSpot leads</span>
            <span className="task-card-sub">
              {dot("hubspot", hubspotReady)}
              Sources, stages, and who needs follow-up
            </span>
          </span>
          <span className="task-card-act">{hubspotReady ? "Start →" : "Configure ›"}</span>
        </button>

        <button
          className={"task-card" + (ghSlackReady ? "" : " gated")}
          data-testid="intro-task-github-slack"
          onClick={() => (ghSlackReady ? onPrefill(GH_SLACK_PROMPT) : onOpenSessionSettings())}
        >
          <span className="task-card-body">
            <span className="task-card-title">Automate a weekly GitHub progress report to Slack</span>
            <span className="task-card-sub">
              {dot("github", live.has("github"))}
              {dot("slack", live.has("slack"))}
              Repo activity, summarized and posted every Friday
            </span>
          </span>
          <span className="task-card-act">{ghSlackReady ? "Start →" : "Configure ›"}</span>
        </button>
      </div>
    </div>
  );
}
