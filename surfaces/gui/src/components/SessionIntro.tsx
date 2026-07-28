
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

export function SessionIntro({ onPrefill }: { onPrefill: (text: string) => void }) {
  return (
    <div className="intro">
      <h1 className="greeting">
        <span className="mark">✦</span> 今天想创作什么内容？
      </h1>
      <p className="intro-lede">选择一个工作流，文枢会先把请求填入输入框，等你确认后再开始。</p>

      <div className="intro-tasks">
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
            <span className="task-card-act">开始 →</span>
          </button>
        ))}
      </div>
    </div>
  );
}
