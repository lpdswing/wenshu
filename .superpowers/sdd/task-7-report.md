# 文枢 0.1 全量验收与交付报告

## 结论

文枢 0.1 产品壳、原生文章流水线、图片生成与微信公众号草稿连接器均已完成。全量 Python、前端 build、Vitest、Playwright 和 Rust `cargo check` 通过；真实浏览器 smoke 验证默认文枢 profile 不请求上游 Cloud、Gallery、Relay、managed OAuth 或 updater。最终 Standards 与 Spec 双轴复审均为零发现。未 push。

## 交付范围

1. `ProductProfile` 成为唯一产品能力入口；文枢默认只公开 `browser` 与 `wechat_official`。
2. Connector UI、integration tools、messaging targets 和 MCP extra tools 都服从同一产品 allowlist。
3. 文枢关闭 Cloud、Gallery、managed OAuth、Relay、updater 的 UI、后台线程与网络路径。
4. 默认内容 Persona 为 `cowork`，显示名“文枢内容助手”，首屏提供内容策划、公众号草稿、资料整理三个自然语言工作入口。
5. 桌面壳、Tauri 元数据、安装包名、产品可见路径与 scratch 默认值完成文枢切换；保留既有 `~/OpenWorker` 数据目录兼容。
6. 核心工作路径中文化；内部协议、event/API key、tool 名、connector/provider/model 名保持稳定。
7. legacy OpenWorker 测试通过显式 permissive test profile 隔离，不放宽生产 `WENSHU_PROFILE`。
8. 原生文章模型、Markdown 渲染、内容审阅、配图规划和图片生成形成可审阅、可恢复的本地流水线。
9. 微信公众号连接器完成凭据校验、预览 hash、图片上传、草稿创建、幂等 receipt、错误脱敏与 fail-closed 分类。
10. `generate_article_assets` 与 `create_wechat_draft` 强制单次人工审批；auto 模式、持久授权和隐藏 connector 的 REST/MCP 写路径均不能绕过产品与审批门禁。

## 全量验证

### Python

```text
.venv/bin/pytest -q
```

结果：`1269 passed, 1 skipped`。

### 前端

```text
cd surfaces/gui
npm run build
npm test -- --run
npx playwright test
```

结果：

- Vite production build：PASS。
- Vitest：`17 passed` files，`94 passed` tests。
- Playwright：`162 passed`，两 workers，无 retry。
- 公众号草稿、一次性审批、默认 Persona、产品来源 allowlist 和 MCP/manual connector 写门禁均有 focused 回归。

全量并发下 Vite dev server 曾随机留下半启动页面；同一集合在 production preview 下连续干净通过。`playwright.config.ts` 因而让普通验收使用 fresh build + preview，`--ui` 继续使用 dev server/HMR。

### Rust / Tauri

```text
cd surfaces/gui/src-tauri
cargo check
```

结果：PASS。工作站补齐 Fedora 原生依赖 `dbus-devel`、`pango-devel`、`gtk3-devel`、`webkit2gtk4.1-devel`、`libsoup3-devel`、`libappindicator-gtk3-devel` 后，`openworker-desktop` check 成功。

既有 warning：`src/lib.rs` 的 `unused_mut`；不影响 check。

## 真实运行 smoke

运行后端 sidecar 与前端 production preview，由 Chromium 打开默认文枢首页并观察请求：

- 页面标题：`文枢 WenShu`。
- 首屏可见“文枢内容助手”、三个内容工作流入口、中文核心导航与 composer。
- 浏览器记录 `130` 个页面资源/API 请求，`0` 个 WebSocket，`0` 个禁止的上游请求。
- 未访问 `api.openworker.com`、Cloud OAuth、Gallery、Relay 或 updater 地址。
- 页面截图保存在工作站临时路径 `/tmp/wenshu-shell-card.png`。

## 最终双轴复审

固定点 `ae8f4c5` 到最终 HEAD 经 Standards 与 Spec 并行审查。首轮发现的审批绕过、HTTP 5xx 结果分类、Persona 指令缺口、通用 `generate_image` 暴露、`default_persona` 未消费、一次性审批 UI 和 README 标准问题均在 `96b7953` 关闭。

复审又定位并关闭两个收口问题：

1. `2602899` 将微信异常 taxonomy 集中到 `errors.wechat_failure_kind()`；`ReceiptStoreError` 在 draft/tool 两条路径统一为 `receipt_invalid`。
2. `2602899` 在 manual REST、MCP REST 与 manager 三条 connector 启用路径上前置 `ProductProfile.visible_connectors` 门禁，验证、OAuth、global MCP config 和 secret write 前即 fail closed。

最终 Standards 与 Spec reviewer 对增量及当前实现均报告零个 Critical、Important 或 Minor finding。

## 测试夹具修复原则

后端旧功能测试使用非 autouse、不可变、显式注入的 `permissive_product`；前端 legacy Connector suites 继续使用 `OPENWORKER_PRODUCT`，文枢 gate suites 使用 `wenshuTest`。生产默认仍为 `WENSHU_PROFILE`，没有通过测试夹具重开任何国外能力。

## 关联报告

- `.superpowers/sdd/task-1-report.md`
- `.superpowers/sdd/task-2-report.md`
- `.superpowers/sdd/task-3-report.md`
- `.superpowers/sdd/task-4-report.md`
- `.superpowers/sdd/task-5-report.md`
- `.superpowers/sdd/task-6-report.md`
- `.superpowers/sdd/task-7-backend-report.md`
- `.superpowers/sdd/task-7-frontend-report.md`

## 已知非阻塞项

- Vite 报告既有 `api.ts` 混合静态/动态导入与大 chunk warning。
- Playwright/Node 报告既有 `NO_COLOR` / `FORCE_COLOR` warning。
- Tauri/Rust 报告一个既有 `unused_mut` warning。
- 未执行 push；提交保留在本地 `feature/wenshu-0.1`。
