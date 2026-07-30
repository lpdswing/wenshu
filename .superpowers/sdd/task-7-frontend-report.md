# Task 7 Frontend Regression Report

## Status

前端全量验收通过：Vite 生产构建、77 个 Vitest、155 个 Playwright E2E 均通过；Playwright 默认六 worker 运行无失败、无 retry。

测试契约修复主要修改 E2E fixture/helper 与 spec；另调整 Playwright webServer，让普通运行服务 fresh production build，`--ui` 保留 HMR。终审后生产 `SessionIntro` 的国外来源卡改为由产品过滤后的 Connector catalog 驱动：文枢隐藏 HubSpot/GitHub/Slack，legacy OpenWorker 保留原行为。没有增加双语 fallback，也没有放宽 Cloud、Relay、Gallery、managed OAuth 或 Connector gate。

## Root cause

初始 96 个失败来自三类测试契约失配，而不是生产文枢行为回归：

1. **核心 chrome 已中文化，旧 selector 仍断言英文。** 高频入口包括账号菜单中的 `Connectors`、`Settings`、`Automations`、`Inbox`、`Activity`，以及 `Session access`、`Sources`、`Channels`、`Folders`、`Recent`、`Show more`、归档/固定/删除等侧栏动作。生产 DOM 的 accessible name 已是中文，旧 `getByRole` 因此超时。
2. **WenShu-specific spec 的产品 fixture 边界不完整。** `cloud.spec.ts`、`gallery.spec.ts` 已使用 `wenshuTest`；`boot.spec.ts` 验证文枢品牌和启动文案，却仍使用默认 legacy fixture。它现已显式切到 `wenshuTest`。
3. **vendor/data-driven 内容不应跟随核心 chrome 翻译。** Connector 名称、账号/工作区/portal、服务商与模型名、服务端状态及 fixture 提供的会话标题/计划仍按原文断言。

`e2e/fixtures.ts` 的产品边界保持不变：

- 默认 `test` 继续调用 `mockApi(page, OPENWORKER_PRODUCT)`，供 Slack、GitHub、Gmail、Google Calendar、HubSpot、MCP、Cloud 等 legacy feature suites 使用。
- `wenshuTest` 继续调用真实约束的 `WENSHU_PRODUCT`，只公开文枢能力并关闭 Cloud、Gallery、managed OAuth、Relay、updater。

## Red evidence

修改前的 Task 7 完整 Playwright 结果在 `test-results/.last-run.json` 中记录 96 个失败；运行：

```text
npx playwright test --last-failed --list
```

列出 `96 tests in 34 files`。代表性失败：

- `access-section.spec.ts` 期望 `Browser, Slack +1 · 1 folder`，实际为 `Browser, Slack +1 · 1 个文件夹`。
- 多个 Connector spec 等待账号菜单按钮 `Connectors`，实际 accessible name 为 `连接器`。
- `cloud.spec.ts` / `gallery.spec.ts` 等待 `Settings`、`Automations`，实际为 `设置`、`自动化`。
- `boot.spec.ts` 等待 `Starting 文枢` / `Restoring your session`，实际为 `正在启动文枢…` / `正在恢复会话…`。
- `composer-model-loading.spec.ts` 期望 `Loading models…`，实际为 `正在加载模型…`。

先对 `access-section.spec.ts` 与 `connectors-list.spec.ts` 做代表性 red-green：第一次运行暴露中文 `数据源` locator 需要 `exact: true`；修正后代表节点通过，随后扩展到其余失败簇。

## Test-harness and selector changes

`e2e/fixtures.ts` 新增共享核心 chrome helper：

- `CORE_CHROME.accountMenu`：集中定义 `收件箱`、`连接器`、`设置`、`自动化`、`活动记录`。
- `accountMenuItem(page, destination)`：通过现有 `account-menu` test id 与精确中文 accessible name 返回稳定 locator。
- `openAccountPage(page, destination)`：统一页面导航、账号菜单展开、菜单可见性等待与目标入口点击。

高频 Connector/Automation consumers 复用该 helper，避免把同一中文 selector 复制到数十处。页面特有的核心 chrome selector 按生产中文精确更新；test ids、API paths、WebSocket event names、localStorage keys 全部保持不变。

没有通过放宽 `WENSHU_PRODUCT`、恢复国外 Connector 可见性、重开 Cloud/Relay/Gallery/managed OAuth，或在生产组件中增加中英双语匹配来让测试通过。

## Green evidence

### 1. Access and automations

Focused files：

```text
access-section.spec.ts
automations-manage.spec.ts
automations-quickstart.spec.ts
automations.spec.ts
```

结果：`12 passed`。

### 2. OPENWORKER legacy Connector / Cloud flows

Focused files：

```text
accounts-page.spec.ts
available-detail.spec.ts
cloud-signin-placement.spec.ts
connector-page.spec.ts
connectors-list.spec.ts
gcal-page.spec.ts
github-page.spec.ts
gmail-page.spec.ts
google-paused.spec.ts
hubspot-page.spec.ts
mcp-connectors.spec.ts
mcp-oauth.spec.ts
slack-directory.spec.ts
slack-health.spec.ts
slack-howitworks.spec.ts
slack-workspaces.spec.ts
```

结果：52 个测试最终全部通过。四 worker 聚合运行中有两个首轮页面启动 timing retry（Gmail、HubSpot）后通过；随后只重跑这两个文件，结果为 `7 passed`，无 retry。Slack channel/workspace 相邻集合另有一次干净重跑：`8 passed`。

这些文件仍导入默认 `test`，因此实际验证的是 `OPENWORKER_PRODUCT` 下 Slack、GitHub、Cloud、Google 与 HubSpot 等 legacy surface 可达，而不是放宽文枢 profile。

### 3. WenShu gates and boot

Focused files：

```text
boot.spec.ts
cloud.spec.ts
composer-model-loading.spec.ts
gallery.spec.ts
```

结果：`9 passed`。

其中 `boot.spec.ts`、`cloud.spec.ts`、`gallery.spec.ts` 显式使用 `wenshuTest`；Cloud/Gallery/managed OAuth 禁用断言在真实 `WENSHU_PRODUCT` 下通过。

### 4. Settings chrome

Focused files：

```text
provider-keys.spec.ts
roots.spec.ts
settings.spec.ts
sidebar-account.spec.ts
```

结果：`13 passed`。

### 5. Session/navigation chrome

Focused files：

```text
inbox.spec.ts
nav-collapse.spec.ts
persona-surfacing.spec.ts
sidebar-automations.spec.ts
sidebar-sessions.spec.ts
sources-channels.spec.ts
```

结果：`21 passed`。

以上五个 focused cluster 覆盖全部 34 个原失败 spec 文件，并连同直接相邻测试共运行 107 个 Playwright 测试。

合并三个 delegated cluster 后又独立聚合运行上述 14 个文件，结果为 `43 passed`，无 retry。

### 6. Adjacent Vitest

```text
npx vitest run \
  src/App.product.test.tsx \
  src/components/Onboarding.test.tsx \
  src/components/SettingsView.product.test.tsx \
  src/components/Sidebar.test.tsx
```

结果：`4 passed` test files，`13 passed` tests。

### 7. Full frontend acceptance

```text
cd surfaces/gui
npm run build
npm test -- --run
npx playwright test
```

最终结果：

- Vite production build：PASS。
- Vitest：`16 passed` test files，`77 passed` tests。
- Playwright（production preview、六 workers）：`155 passed`，无 retry。
- 早期稳定性运行（production preview、两 workers、`--retries=0`）：`154 passed`；新增文枢来源 gate 用例后最终集合为 155。

修改前同一全量 Playwright 集合由 Vite dev server 提供页面，分别出现 `3 failed / 151 passed` 与 `2 failed / 152 passed` 的随机页面半启动超时；手动切换 production preview 后 `154 passed`。`playwright.config.ts` 因此将普通验收固定到 fresh build + preview，同时让 `--ui` 保留 dev server。

## Intentionally retained English assertions

仍保留英文的断言只位于 OPENWORKER legacy/vendor surface 或 data-driven 内容：

- Slack、GitHub、Gmail、Google Calendar、HubSpot、MCP 等 Connector 名称、认证说明、字段标签、vendor 状态与帮助文案。
- 账号、邮箱、workspace、installation、portal、channel、person 等服务端/fixture 数据。
- provider/model 名称、计划标题与 schedule、会话标题、Inbox 请求正文等动态内容。

已中文化的核心 chrome 不再依赖英文 selector；生产 UI 未新增双语 fallback。

## Changed files

Shared fixture/helper：

- `surfaces/gui/e2e/fixtures.ts`

34 个原失败 spec：

- `surfaces/gui/e2e/access-section.spec.ts`
- `surfaces/gui/e2e/accounts-page.spec.ts`
- `surfaces/gui/e2e/automations-manage.spec.ts`
- `surfaces/gui/e2e/automations-quickstart.spec.ts`
- `surfaces/gui/e2e/automations.spec.ts`
- `surfaces/gui/e2e/available-detail.spec.ts`
- `surfaces/gui/e2e/boot.spec.ts`
- `surfaces/gui/e2e/cloud-signin-placement.spec.ts`
- `surfaces/gui/e2e/cloud.spec.ts`
- `surfaces/gui/e2e/composer-model-loading.spec.ts`
- `surfaces/gui/e2e/connector-page.spec.ts`
- `surfaces/gui/e2e/connectors-list.spec.ts`
- `surfaces/gui/e2e/gallery.spec.ts`
- `surfaces/gui/e2e/gcal-page.spec.ts`
- `surfaces/gui/e2e/github-page.spec.ts`
- `surfaces/gui/e2e/gmail-page.spec.ts`
- `surfaces/gui/e2e/google-paused.spec.ts`
- `surfaces/gui/e2e/hubspot-page.spec.ts`
- `surfaces/gui/e2e/inbox.spec.ts`
- `surfaces/gui/e2e/mcp-connectors.spec.ts`
- `surfaces/gui/e2e/mcp-oauth.spec.ts`
- `surfaces/gui/e2e/nav-collapse.spec.ts`
- `surfaces/gui/e2e/persona-surfacing.spec.ts`
- `surfaces/gui/e2e/provider-keys.spec.ts`
- `surfaces/gui/e2e/roots.spec.ts`
- `surfaces/gui/e2e/settings.spec.ts`
- `surfaces/gui/e2e/sidebar-account.spec.ts`
- `surfaces/gui/e2e/sidebar-automations.spec.ts`
- `surfaces/gui/e2e/sidebar-sessions.spec.ts`
- `surfaces/gui/e2e/slack-directory.spec.ts`
- `surfaces/gui/e2e/slack-health.spec.ts`
- `surfaces/gui/e2e/slack-howitworks.spec.ts`
- `surfaces/gui/e2e/slack-workspaces.spec.ts`
- `surfaces/gui/e2e/sources-channels.spec.ts`

Report：

- `.superpowers/sdd/task-7-frontend-report.md`

Final review follow-up：

- `381549b` — product-scope SessionIntro source cards；WenShu 隐藏 HubSpot/GitHub/Slack，legacy fixture 保留原 setup flow。
- Focused E2E：`5 passed`。
- 复审结论：Approved，无 Critical/Important。

## Commits

- `c16eefe` — shared helper plus Access/Automation/legacy Connector clusters
- `e902439` — WenShu gate, boot, and model-loading selectors
- `7cf460c` — Settings and account-menu selectors
- `d72bfcb` — Inbox/session/navigation selectors
- `381549b` — product-scoped SessionIntro sources

均未 push。

## Remaining risk

- Vite 仍报告既有的 `api.ts` 混合静态/动态导入与大 chunk warning；不影响构建或 E2E。
- Playwright/Node 仍报告既有 `NO_COLOR` / `FORCE_COLOR` 环境 warning；不影响结果。
- OPENWORKER legacy/vendor surface 仍保留原文；未来若生产代码明确翻译这些 vendor-owned 页面，应同步更新对应 selector，而不是加入双语生产 fallback。
- `SessionIntro` 现在只消费产品过滤后的 Connector catalog；生产 `WENSHU_PRODUCT` 不显示国外来源卡，legacy fixture 路径保持原文和原行为。
