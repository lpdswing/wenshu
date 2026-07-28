# 文枢产品壳 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 OpenWorker 的用户可见产品壳改为文枢，集中控制默认 Persona、连接器可见性和云功能，同时保留内部 `coworker` 兼容标识。

**Architecture:** 新增不可变 `ProductProfile` 作为后端运行时产品 Interface，由 Health、SessionManager 和 Engine 共同读取；前端通过 `/v1/health` 获取功能开关。Tauri 构建期品牌保持静态配置。国外连接器和云代码保留，但在文枢 Profile 下不可见且不进入默认运行路径。

**Tech Stack:** Python 3.12、dataclasses、FastAPI、React 18、TypeScript、Tauri 2、pytest、Vitest、Playwright。

## Global Constraints

- 用户可见品牌为“文枢 WenShu”。
- 保留 Python 包 `coworker`、`.coworker`、`COWORKER_*`、数据库文件和 localStorage 键。
- 默认模型继续使用当前 OpenAI 默认值 `gpt-5.6-sol`。
- 国外连接器只隐藏，不删除实现和测试。
- OpenWorker Cloud、Gallery、托管 OAuth、Relay 和上游 updater 默认关闭。
- 不新增云服务、OAuth Broker、Relay 或更新服务。
- 本计划不实现文章、生图或微信公众号功能；它们由后续计划完成。

---

### Task 1: ProductProfile 运行时 Interface

**Files:**
- Create: `coworker/product.py`
- Create: `tests/test_product.py`
- Modify: `coworker/server/manager.py:SessionManager.__init__`
- Modify: `coworker/server/app.py:health`
- Modify: `surfaces/gui/src/api.ts:Health`

**Interfaces:**
- Produces: `ProductProfile`, `WENSHU_PROFILE`, `current_product() -> ProductProfile`。
- Produces: `/v1/health.product` JSON，供后续前端功能开关使用。

- [ ] **Step 1: 写 ProductProfile 失败测试**

```python
from coworker.product import current_product


def test_wenshu_product_defaults():
    p = current_product()
    assert p.id == "wenshu"
    assert p.name == "文枢"
    assert p.display_name == "文枢 WenShu"
    assert p.default_persona == "cowork"
    assert p.features == {
        "cloud": False,
        "gallery": False,
        "managed_oauth": False,
        "relay": False,
        "updater": False,
    }
    assert p.visible_connectors == frozenset({"browser", "wechat_official"})
```

- [ ] **Step 2: 确认测试失败**

Run: `.venv/bin/pytest tests/test_product.py -v`

Expected: FAIL，`ModuleNotFoundError: No module named 'coworker.product'`。

- [ ] **Step 3: 实现不可变 Profile**

```python
# coworker/product.py
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ProductProfile:
    id: str
    name: str
    display_name: str
    default_persona: str
    visible_connectors: frozenset[str]
    features: dict[str, bool]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["visible_connectors"] = sorted(self.visible_connectors)
        return data


WENSHU_PROFILE = ProductProfile(
    id="wenshu",
    name="文枢",
    display_name="文枢 WenShu",
    default_persona="cowork",
    visible_connectors=frozenset({"browser", "wechat_official"}),
    features={
        "cloud": False,
        "gallery": False,
        "managed_oauth": False,
        "relay": False,
        "updater": False,
    },
)


def current_product() -> ProductProfile:
    return WENSHU_PROFILE
```

`SessionManager.__init__` 接受可选 `product: ProductProfile | None = None`，保存 `self.product = product or current_product()`。Health 的已认证响应增加 `"product": manager.product.to_dict()`；未认证 tokenless Health 仍只返回 `{"status": "ok"}`。

前端类型增加：

```ts
export interface ProductInfo {
  id: string;
  name: string;
  display_name: string;
  default_persona: string;
  visible_connectors: string[];
  features: Record<string, boolean>;
}

export interface Health {
  status: string;
  default_workspace: string | null;
  model: string;
  product: ProductInfo;
}
```

- [ ] **Step 4: 验证 Profile 和 Health**

Run: `.venv/bin/pytest tests/test_product.py tests/test_server.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add coworker/product.py coworker/server/manager.py coworker/server/app.py surfaces/gui/src/api.ts tests/test_product.py
git commit -m "feat: add Wenshu product profile"
```

### Task 2: 默认隐藏连接器并阻止隐藏工具进入 Agent

**Files:**
- Modify: `coworker/server/manager.py:SessionManager.list_connectors`
- Modify: `coworker/agent.py:_enabled_connector_tools`
- Modify: `tests/test_connectors.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: `ProductProfile.visible_connectors`。
- Produces: `visible_connector_list(secrets, product)` 结果只含文枢允许项。
- Guarantee: 已连接但隐藏的国外 Connector 不会把 Tool 注册给文枢 Agent。

- [ ] **Step 1: 写隐藏行为失败测试**

```python
def test_wenshu_lists_only_visible_connectors(manager):
    names = {row["name"] for row in manager.list_connectors()}
    assert names <= {"browser", "wechat_official"}
    assert "slack" not in names


def test_hidden_connected_connector_tools_are_not_enabled(tmp_path):
    secrets = SecretStore(path=tmp_path / "secrets.json")
    secrets.put("notion:default", {"access_token": "secret", "enabled": True})
    connectors, tools = _enabled_connector_tools(secrets, current_product())
    assert "notion" not in connectors
    assert not any(name.startswith("notion_") for name in tools)
```

- [ ] **Step 2: 确认测试失败**

Run: `.venv/bin/pytest tests/test_connectors.py tests/test_server.py -q`

Expected: FAIL，当前列表仍包含 Slack/Notion 等连接器。

- [ ] **Step 3: 在后端统一过滤**

将 `_enabled_connector_tools` 改为：

```python
def _enabled_connector_tools(
    secrets: SecretStore,
    product: ProductProfile,
) -> tuple[set[str], set[str]]:
    connectors = {
        c["name"]: c
        for c in connector_list(secrets)
        if c["name"] in product.visible_connectors
    }
    # 其余 enabled_connectors/enabled_tools 逻辑保持现有实现
```

`build_engine` 增加 `product: ProductProfile | None = None`，默认 `current_product()`；`SessionManager.get_engine` 传 `self.product`。`SessionManager.list_connectors` 在完成现有实时状态 enrichment 后过滤，而不是在 descriptor registry 删除数据。

- [ ] **Step 4: 运行连接器和 Agent 回归测试**

Run: `.venv/bin/pytest tests/test_connectors.py tests/test_engine.py tests/test_server.py -q`

Expected: PASS；旧 Connector registry 测试仍能直接测试完整 catalog。

- [ ] **Step 5: 提交**

```bash
git add coworker/agent.py coworker/server/manager.py tests/test_connectors.py tests/test_server.py
git commit -m "feat: gate connectors through product profile"
```

### Task 3: 关闭云功能、Relay 和 Gallery 运行路径

**Files:**
- Modify: `coworker/server/app.py`
- Modify: `coworker/server/manager.py:start_gateway`
- Modify: `tests/test_cloud_server.py`
- Modify: `tests/test_slack_relay.py`
- Modify: `surfaces/gui/src/App.tsx`
- Modify: `surfaces/gui/src/components/Sidebar.tsx`
- Modify: `surfaces/gui/src/components/Onboarding.tsx`
- Modify: `surfaces/gui/e2e/cloud.spec.ts`
- Modify: `surfaces/gui/e2e/gallery.spec.ts`

**Interfaces:**
- Consumes: `manager.product.features`。
- Produces: disabled features return HTTP 404 `{ "error": "feature disabled" }`。
- Produces: GUI does not render Cloud account、Gallery、managed OAuth 或 updater entry points。

- [ ] **Step 1: 写后端功能门测试**

```python
def test_cloud_routes_are_disabled_for_wenshu(client):
    for path in ("/v1/cloud/status", "/v1/gallery"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json() == {"error": "feature disabled"}


@pytest.mark.asyncio
async def test_relay_is_not_started_when_disabled(manager, monkeypatch):
    called = False
    async def forbidden(*args, **kwargs):
        nonlocal called
        called = True
    monkeypatch.setattr(manager, "_start_relay", forbidden)
    await manager.start_gateway()
    assert called is False
```

- [ ] **Step 2: 确认门测试失败**

Run: `.venv/bin/pytest tests/test_cloud_server.py tests/test_slack_relay.py -q`

Expected: FAIL，Cloud route 或 Relay 仍可进入。

- [ ] **Step 3: 实现 FastAPI feature gate**

在 `create_app` 内定义并由 Cloud/Gallery/managed OAuth 路由调用：

```python
def require_feature(name: str) -> JSONResponse | None:
    if manager.product.features.get(name, False):
        return None
    return JSONResponse({"error": "feature disabled"}, status_code=404)
```

每个被禁功能 route 在任何网络调用前返回该响应。`start_gateway` 在 `relay=False` 时只允许本地手动 Adapter；不得创建 `RelayHub`。

- [ ] **Step 4: 用 Health feature flags 隐藏前端入口**

`App.tsx` 保存 `health.product`；仅在 `features.updater` 为 true 时渲染 `<UpdateBanner />`，仅在 `features.gallery` 为 true 时接受 gallery surface。Sidebar/Onboarding 仅在 `features.cloud` 为 true 时显示 Cloud 登录；Connectors 后端已经过滤，因此前端不得维护第二份国外 Connector denylist。

- [ ] **Step 5: 更新 E2E fixture 和断言**

在 `surfaces/gui/e2e/fixtures.ts` 的 Health mock 增加文枢 ProductInfo。Cloud/Gallery E2E 改为断言入口不存在，而不是删除测试文件：

```ts
await expect(page.getByTestId("account-row")).not.toContainText("Sign in");
await expect(page.getByRole("button", { name: "Gallery" })).toHaveCount(0);
```

- [ ] **Step 6: 验证云功能关闭**

Run: `.venv/bin/pytest tests/test_cloud_server.py tests/test_slack_relay.py -q`

Run: `cd surfaces/gui && npm test -- --run`

Expected: 两条命令 PASS。

- [ ] **Step 7: 提交**

```bash
git add coworker/server/app.py coworker/server/manager.py tests/test_cloud_server.py tests/test_slack_relay.py surfaces/gui/src/App.tsx surfaces/gui/src/components/Sidebar.tsx surfaces/gui/src/components/Onboarding.tsx surfaces/gui/e2e
git commit -m "feat: disable upstream cloud features"
```

### Task 4: 文枢默认 Persona 和首页内容

**Files:**
- Modify: `coworker/agents/cowork.py`
- Modify: `coworker/personas/registry.py`
- Modify: `tests/test_builtin_personas.py`
- Modify: `surfaces/gui/src/App.tsx:SUGGESTIONS`
- Modify: `surfaces/gui/src/components/SessionIntro.tsx`
- Modify: `surfaces/gui/e2e/session-intro.spec.ts`

**Interfaces:**
- Consumes: `ProductProfile.default_persona == "cowork"`。
- Produces: 内部 Persona id 保持 `cowork`，用户显示名称变为“文枢内容助手”。

- [ ] **Step 1: 写 Persona 品牌测试**

```python
def test_default_persona_is_wenshu_content_assistant(tmp_path):
    registry = PersonaRegistry(state_path=tmp_path / "personas.json")
    row = next(p for p in registry.list_all() if p["id"] == "cowork")
    assert row["name"] == "文枢内容助手"
    assert "资料" in row["tagline"]
    assert "公众号" in registry.resolve("cowork").system_prompt
```

- [ ] **Step 2: 确认测试失败**

Run: `.venv/bin/pytest tests/test_builtin_personas.py -q`

Expected: FAIL，当前名称为 OpenWorker/Cowork。

- [ ] **Step 3: 修改 Persona 文案但保留 id**

`cowork_agent()` 保持 `name="cowork"`，将 title 和系统提示改成中文内容工作定位；`PersonaRegistry._load_builtin` 的显示名称改为“文枢内容助手”，tagline 改为“整理资料、撰写文章并交付内容成果”。不要在本任务提前描述尚未实现的具体 Tool 名。

- [ ] **Step 4: 修改首页建议**

```ts
const SUGGESTIONS = [
  { ico: "文", text: "整理这些资料，先生成一版文章草稿。" },
  { ico: "图", text: "审阅文章后，为它规划封面和正文配图。" },
  { ico: "微", text: "把确认后的文章整理成公众号草稿。" },
];
```

未实现功能入口仍通过自然语言进入 Agent，不新增假按钮或假成功状态。

- [ ] **Step 5: 验证 Persona 与首页**

Run: `.venv/bin/pytest tests/test_builtin_personas.py -q`

Run: `cd surfaces/gui && npx playwright test e2e/session-intro.spec.ts`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add coworker/agents/cowork.py coworker/personas/registry.py tests/test_builtin_personas.py surfaces/gui/src/App.tsx surfaces/gui/src/components/SessionIntro.tsx surfaces/gui/e2e/session-intro.spec.ts
git commit -m "feat: make Wenshu the default content persona"
```

### Task 5: 用户可见品牌和 Tauri 构建元数据

**Files:**
- Modify: `README.md`
- Modify: `surfaces/gui/index.html`
- Modify: `surfaces/gui/package.json`
- Modify: `surfaces/gui/package-lock.json`
- Modify: `surfaces/gui/src-tauri/Cargo.toml`
- Modify: `surfaces/gui/src-tauri/src/lib.rs`
- Modify: `surfaces/gui/src-tauri/tauri.conf.json`
- Modify: `packaging/openworker-server.spec`
- Modify: `packaging/server_entry.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Produces: app productName `文枢`、identifier `com.wenshu.desktop`、publisher `WenShu`。
- Constraint: CLI entrypoint 和内部 sidecar 文件名首版保持 `openworker-server`，避免打断现有开发脚本。

- [ ] **Step 1: 写浏览器标题测试**

在 `surfaces/gui/e2e/boot.spec.ts` 增加：

```ts
test("uses the Wenshu product title", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle("文枢 WenShu");
});
```

- [ ] **Step 2: 确认标题测试失败**

Run: `cd surfaces/gui && npx playwright test e2e/boot.spec.ts`

Expected: FAIL，当前 title 为 OpenWorker。

- [ ] **Step 3: 修改静态品牌**

`index.html` title 改为 `文枢 WenShu`；Tauri 配置设置：

```json
{
  "productName": "文枢",
  "identifier": "com.wenshu.desktop",
  "bundle": { "publisher": "WenShu" },
  "plugins": {}
}
```

删除 updater endpoints 和原公钥，而不是换成空字符串。Rust 页面、托盘、菜单、OAuth 回调 HTML 和日志中的用户可见 `OpenWorker` 改为 `文枢`；内部环境变量、state dir 和 sidecar 文件名不改。

- [ ] **Step 4: 更新 README 和包描述**

README 明确：项目是基于 OpenWorker 的 MIT fork、文枢定位、Linux 浏览器开发流程、内部兼容名称暂留。保留原 LICENSE，不覆盖原版权。

- [ ] **Step 5: 验证构建元数据**

Run: `cd surfaces/gui && npm run build`

Run: `cd surfaces/gui/src-tauri && cargo check`

Expected: 两条命令 PASS；构建输出不含 updater 配置错误。

- [ ] **Step 6: 提交**

```bash
git add README.md surfaces/gui/index.html surfaces/gui/package.json surfaces/gui/package-lock.json surfaces/gui/src-tauri packaging tests/test_server.py
git commit -m "feat: rebrand desktop shell as Wenshu"
```

### Task 6: 核心路径中文化

**Files:**
- Modify: `surfaces/gui/src/components/Composer.tsx`
- Modify: `surfaces/gui/src/components/Sidebar.tsx`
- Modify: `surfaces/gui/src/components/Onboarding.tsx`
- Modify: `surfaces/gui/src/components/SettingsView.tsx`
- Modify: `surfaces/gui/src/components/ApprovalCard.tsx`
- Modify: `surfaces/gui/src/components/RightRail.tsx`
- Modify: `surfaces/gui/src/humanize.ts`
- Modify: relevant files under `surfaces/gui/e2e/`

**Interfaces:**
- Produces: 首页、会话、模型连接、进度、交付物和审批核心路径中文文案。
- Non-goal: 本任务不翻译所有国外 Connector 内部说明。

- [ ] **Step 1: 添加核心中文 E2E 断言**

在 `e2e/smoke.spec.ts` 和 `e2e/approval-card.spec.ts` 增加可观察文本断言：

```ts
await expect(page.getByPlaceholder("告诉文枢你想完成什么…")).toBeVisible();
await expect(page.getByRole("button", { name: "发送" })).toBeVisible();
await expect(page.getByRole("button", { name: "进度" })).toBeVisible();
await expect(page.getByRole("button", { name: /批准一次/ })).toBeVisible();
```

- [ ] **Step 2: 确认 E2E 失败**

Run: `cd surfaces/gui && npx playwright test e2e/smoke.spec.ts e2e/approval-card.spec.ts`

Expected: FAIL，英文元素仍存在。

- [ ] **Step 3: 翻译核心路径**

逐个替换用户可见字符串，不改 test id、event name、localStorage key 或 Tool name。`humanize.ts` 增加文枢后续 Tool 的中文模板占位只允许在对应 Tool 实现时加入；本任务只翻译现有 read/write/shell/todo/approval 模板。

- [ ] **Step 4: 运行前端测试**

Run: `cd surfaces/gui && npm test -- --run`

Run: `cd surfaces/gui && npx playwright test e2e/smoke.spec.ts e2e/approval-card.spec.ts e2e/onboarding.spec.ts`

Expected: PASS。

- [ ] **Step 5: 浏览器烟雾验证**

Run: 后端 `.venv/bin/openworker-server --cwd "$PWD" --port 8765`，前端 `cd surfaces/gui && npm run dev`。

Verify: 打开 `http://127.0.0.1:1420`，首页、输入框、模型连接、审批和交付物可正常操作；浏览器 Console 无 error。

- [ ] **Step 6: 提交**

```bash
git add surfaces/gui/src surfaces/gui/e2e
git commit -m "feat: localize Wenshu core workflow"
```

### Task 7: 产品壳完整回归

**Files:**
- Modify only if a verified regression requires a source fix.

**Interfaces:**
- Produces: 后续内容计划可以依赖稳定的文枢 Profile、Persona 和中文 UI。

- [ ] **Step 1: 后端完整测试**

Run: `.venv/bin/pytest -q`

Expected: PASS，无失败。

- [ ] **Step 2: 前端类型、单测和 E2E**

Run: `cd surfaces/gui && npm run build && npm test -- --run && npx playwright test`

Expected: PASS，无失败。

- [ ] **Step 3: 确认上游服务没有默认请求**

启动浏览器版，观察 Network；未点击任何外部 Connector 时不得请求 `api.openworker.com`、Auth0、OpenWorker updater 或 Relay WebSocket。

- [ ] **Step 4: 提交必要回归修复**

若步骤 1–3 发现真实问题，添加最窄测试和修复后提交：

```bash
git add <受影响文件>
git commit -m "fix: complete Wenshu product shell"
```
