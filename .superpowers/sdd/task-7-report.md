# Task 7：全量验收与交付报告

## 结论

文枢 0.1 产品壳计划的 7 个任务均已完成。全量 Python、前端 build、Vitest、Playwright 和 Rust `cargo check` 通过；真实浏览器 smoke 验证默认文枢 profile 不请求上游 Cloud、Gallery、Relay、managed OAuth 或 updater。未 push。

## 交付范围

1. `ProductProfile` 成为唯一产品能力入口；文枢默认只公开 `browser` 与 `wechat_official`。
2. Connector UI、integration tools、messaging targets 和 MCP extra tools 都服从同一产品 allowlist。
3. 文枢关闭 Cloud、Gallery、managed OAuth、Relay、updater 的 UI、后台线程与网络路径。
4. 默认内容 Persona 为 `cowork`，显示名“文枢内容助手”，首屏提供内容策划、公众号草稿、资料整理三个自然语言工作入口。
5. 桌面壳、Tauri 元数据、安装包名、产品可见路径与 scratch 默认值完成文枢切换；保留既有 `~/OpenWorker` 数据目录兼容。
6. 核心工作路径中文化；内部协议、event/API key、tool 名、connector/provider/model 名保持稳定。
7. legacy OpenWorker 测试通过显式 permissive test profile 隔离，不放宽生产 `WENSHU_PROFILE`。

## 全量验证

### Python

```text
.venv/bin/pytest -q
```

结果：`964 passed, 1 skipped, 1 warning`。

唯一 warning 是既有 FastAPI TestClient 对 `httpx` 的 `StarletteDeprecationWarning`。

### 前端

```text
cd surfaces/gui
npm run build
npm test -- --run
npx playwright test
```

结果：

- Vite production build：PASS。
- Vitest：`16 passed` files，`77 passed` tests。
- Playwright：`155 passed`，六 workers，无 retry。
- Playwright 稳定性复验：早期 154-test 集合以两 workers、`--retries=0` 全绿；新增文枢来源 gate 后最终集合 155 全绿。

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

## 终审修复

最终审查定位并修复两个 Important：

1. `4ac0c1b`：产品 allowlist 前移到 gateway settings/profile 读取，并覆盖入站分发、effective connectors、Persona/Session views/recommends 与连接写路径；遗留隐藏凭据保留但不可见、不可启用、不可收消息。
2. `381549b`：新会话来源卡由产品过滤后的 Connector catalog 驱动；文枢不再显示 HubSpot/GitHub/Slack，legacy OpenWorker 保留原 setup flow。

两项 focused 复审均 Approved，无剩余 Critical/Important。

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
