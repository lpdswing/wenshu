# 文枢微信公众号草稿 Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已审阅并完成配图的 `article.md` 渲染为公众号 HTML，预览确认后上传图片并调用 `draft/add` 保存草稿；重复提交相同内容直接返回本地成功回执。

**Architecture:** `coworker/connectors/wechat/` 是独立深层 Module：Client 封装 token 与微信错误，Renderer 负责公众号 HTML，Images 负责正文图和封面素材，Drafts 负责哈希、幂等与 draft/add，Tools 只暴露预览和创建草稿两个 Interface。连接凭据继续使用现有 `SecretStore` 和通用 Connector 配置页。

**Tech Stack:** Python 3.12、httpx、Markdown-It-Py、PyYAML、Pillow、FastAPI、React、pytest、Vitest、Playwright。

**Depends on:** 文枢产品壳和原生内容流水线已完成；`article.md`、`cover.png`、`images/`、`assets.manifest.json` 可用。

## Global Constraints

- 首版只支持一个公众号账号，profile key 为 `wechat_official:default`。
- AppID、AppSecret、access token 不进入模型消息、Tool 参数、Markdown、回执、普通日志或审计 args。
- `prepare_wechat_draft` 不获取 access token、不访问微信、不执行外部写操作。
- `create_wechat_draft` 必须收到并重新校验 `preview_hash`，且必须经过现有 Approval Card。
- 只创建公众号草稿；不注册发布、群发、预览发送或删除草稿 Tool。
- 微信响应未知时不得自动重试 `draft/add`；返回 `unknown` 和已上传但无法回滚的素材清单，不写成功回执。
- 相同 `preview_hash` 的成功提交直接返回已有 receipt，不重复上传或建草稿。

---

### Task 1: 微信错误模型、凭据和 token Client

**Files:**
- Create: `coworker/connectors/wechat/__init__.py`
- Create: `coworker/connectors/wechat/models.py`
- Create: `coworker/connectors/wechat/errors.py`
- Create: `coworker/connectors/wechat/credentials.py`
- Create: `coworker/connectors/wechat/client.py`
- Create: `tests/test_wechat_client.py`

**Interfaces:**
- Produces: `WeChatCredentials.from_store(secrets)`、`WeChatClient.get_access_token()`、`WeChatClient.request_json()`。
- Guarantee: Client 按 `(appid, expires_at)` 内存缓存 token，提前 120 秒失效；不持久化 access token。

- [ ] **Step 1: 写 token 缓存和错误分类失败测试**

```python
def test_token_is_cached_without_leaking_secret(tmp_path):
    calls = []
    transport = httpx.MockTransport(token_handler(calls, token="ACCESS", expires_in=7200))
    client = WeChatClient(WeChatCredentials("wx-app", "app-secret"), transport=transport)
    assert client.get_access_token() == "ACCESS"
    assert client.get_access_token() == "ACCESS"
    assert len(calls) == 1
    assert "app-secret" not in repr(client)

@pytest.mark.parametrize(("errcode", "kind"), [
    (40013, "invalid_credentials"),
    (40164, "ip_allowlist"),
    (48001, "permission_denied"),
    (45009, "rate_limited"),
])
def test_error_codes_are_classified(errcode, kind):
    error = classify_wechat_error(errcode, "vendor message")
    assert error.kind == kind
    assert error.errcode == errcode
```

另测 token 过期刷新、HTTP timeout、非 JSON、200 + errcode、日志脱敏。

- [ ] **Step 2: 确认测试失败**

Run: `.venv/bin/pytest tests/test_wechat_client.py -v`

Expected: FAIL，wechat package 不存在。

- [ ] **Step 3: 实现 Client**

```python
@dataclass(frozen=True, repr=False)
class WeChatCredentials:
    app_id: str
    app_secret: str

    @classmethod
    def from_store(cls, secrets: SecretStore) -> "WeChatCredentials":
        row = secrets.get("wechat_official:default") or {}
        if not row.get("app_id") or not row.get("app_secret"):
            raise WeChatCredentialError("微信公众号尚未连接")
        return cls(row["app_id"], row["app_secret"])
```

Token endpoint：`GET https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid=...&secret=...`。所有其他请求通过 `request_json(method, path, params, json, files)`，自动附加 token。错误对象只保留 `errcode`、脱敏 errmsg 和分类 kind；请求 URL 的 query 日志必须去掉 secret/token。

- [ ] **Step 4: 验证 Client**

Run: `.venv/bin/pytest tests/test_wechat_client.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add coworker/connectors/wechat tests/test_wechat_client.py
git commit -m "feat: add WeChat Official Account client"
```

### Task 2: 公众号 Connector 描述符与凭据验证

**Files:**
- Modify: `coworker/connectors/descriptors.py`
- Modify: `coworker/connectors/setup.py`
- Modify: `coworker/server/manager.py`
- Modify: `coworker/server/app.py`
- Modify: `tests/test_connectors.py`
- Modify: `surfaces/gui/src/api.ts`
- Modify: `surfaces/gui/src/connectors/registry.tsx`
- Create: `surfaces/gui/src/components/connectors/WechatDetail.tsx`
- Modify: `surfaces/gui/src/components/connectors/ConnectorsSection.tsx`
- Modify: `surfaces/gui/e2e/connectors-list.spec.ts`

**Interfaces:**
- Produces connector id: `wechat_official`。
- Produces SecretStore profile: `wechat_official:default`，凭据 fields 为 `app_id`、`app_secret`；账号设置为 `need_open_comment`、`only_fans_can_comment` 两个 bool。

- [ ] **Step 1: 写 Connector 失败测试**

```python
def test_wechat_descriptor_is_available_and_secret_safe():
    d = get_descriptor("wechat_official")
    assert d.title == "微信公众号"
    assert [f.key for f in d.fields] == ["app_id", "app_secret"]
    assert next(f for f in d.fields if f.key == "app_secret").secret is True
    assert d.two_way is False


def test_wechat_connect_validates_credentials(monkeypatch, secrets):
    monkeypatch.setattr(WeChatClient, "get_access_token", lambda self: "token")
    result = connect_connector(secrets, "wechat_official", {"app_id": "wx1", "app_secret": "s1"})
    assert result["ok"] is True
    assert result["identity"] == "wx1"
```

- [ ] **Step 2: 确认测试失败**

Run: `.venv/bin/pytest tests/test_connectors.py -q`

Expected: FAIL，descriptor 尚不存在。

- [ ] **Step 3: 新增描述符**

```python
ConnectorDescriptor(
    name="wechat_official",
    title="微信公众号",
    icon="微",
    blurb="将已确认的图文保存到公众号草稿箱。",
    auth="api_token",
    two_way=False,
    fields=[
        Field("app_id", "AppID", placeholder="wx..."),
        Field("app_secret", "AppSecret", secret=True),
    ],
    instructions=[
        "登录微信公众平台，在开发 → 基本配置中查看 AppID 和 AppSecret。",
        "将当前出口 IP 加入公众号 IP 白名单。",
    ],
    validate=_validate_wechat_official,
    brand_color="#07C160",
    logo="wechat",
    account_field="@identity",
)
```

验证函数只调用 token endpoint；将 40013/40125、40164、48001、429/45009、network 分成明确中文错误。SecretStore 的通用 `connector_list` 只返回 `configured_fields`，不得返回字段值。连接成功时将两个评论设置初始化为 false。

- [ ] **Step 4: 添加账号设置和前端连接页**

新增 `GET/PATCH /v1/connectors/wechat_official/settings`，只读写两个 bool；未连接时返回 409，不接受任意 profile 字段。`WechatDetail.tsx` 复用现有 Detail 布局，显示“开启评论”和“仅粉丝可评论”开关；后者只有开启评论时可用。前端 registry 复用 simple-icons 微信 glyph（若包不存在该 glyph，使用内置单色 fallback，不新增远程资源）。E2E 验证 AppSecret 输入为 password、连接请求 payload 正确、评论开关往返正确且页面不会回显秘密。

- [ ] **Step 5: 验证连接闭环**

Run: `.venv/bin/pytest tests/test_connectors.py tests/test_server.py -q`

Run: `cd surfaces/gui && npx playwright test e2e/connectors-list.spec.ts`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add coworker/connectors/descriptors.py coworker/connectors/setup.py coworker/server/manager.py coworker/server/app.py tests/test_connectors.py tests/test_server.py surfaces/gui/src/api.ts surfaces/gui/src/connectors/registry.tsx surfaces/gui/src/components/connectors surfaces/gui/e2e/connectors-list.spec.ts
git commit -m "feat: add WeChat account connection"
```

### Task 3: Markdown 到公众号兼容 HTML Renderer

**Files:**
- Create: `coworker/connectors/wechat/renderer.py`
- Create: `tests/test_wechat_renderer.py`

**Interfaces:**
- Produces: `render_wechat_article(article, theme, color) -> RenderedArticle`。
- Themes: `default`、`grace`、`simple`、`modern`；未知主题失败，不静默回退。

- [ ] **Step 1: 写 Renderer 合同测试**

```python
def test_renderer_produces_inline_wechat_html(tmp_path):
    rendered = render_wechat_article(load_article(write_article(tmp_path / "article.md", RICH_ARTICLE)), "default", "#07C160")
    assert rendered.title == "文枢项目介绍"
    assert "<h1" not in rendered.html  # 标题由 draft/add 字段提供
    assert "style=" in rendered.html
    assert "<script" not in rendered.html
    assert "class=" not in rendered.html
    assert "https://example.com" in rendered.html
    assert rendered.image_refs == ("images/section-1.png",)
```

覆盖 headings、lists、blockquote、code、table、粗体、链接、图片、HTML 注入、主题色校验、摘要长度和缺失图片。

- [ ] **Step 2: 确认测试失败**

Run: `.venv/bin/pytest tests/test_wechat_renderer.py -v`

Expected: FAIL。

- [ ] **Step 3: 实现 Token Renderer**

使用 MarkdownIt token tree，不对生成 HTML 做脆弱 regex 替换。每个元素直接输出允许标签和 inline style；原始 HTML token 全部 escape。普通外链转成正文尾部“参考链接”编号，正文中保留 `[n]`，避免公众号编辑器吞链接。图片先输出占位 `data-wenshu-image="相对路径"`，后续 Images Module 替换为远端 URL。

```python
@dataclass(frozen=True)
class RenderedArticle:
    title: str
    author: str
    digest: str
    html: str
    image_refs: tuple[str, ...]
```

- [ ] **Step 4: 验证 Renderer**

Run: `.venv/bin/pytest tests/test_wechat_renderer.py -q`

Expected: PASS；输出不含 script、event handler、class 或外部 stylesheet。

- [ ] **Step 5: 提交**

```bash
git add coworker/connectors/wechat/renderer.py tests/test_wechat_renderer.py
git commit -m "feat: render WeChat-compatible article HTML"
```

### Task 4: 最终图文预览与 preview_hash

**Files:**
- Create: `coworker/connectors/wechat/hashing.py`
- Create: `coworker/connectors/wechat/preview.py`
- Create: `tests/test_wechat_preview.py`

**Interfaces:**
- Produces: `prepare_preview(article_path, theme, color, cover_path, roots) -> DraftPreview`。
- Hash covers: 规范化文章、封面和正文图片 sha256、theme、color、作者/摘要/原文链接、评论设置等全部提交选项。

- [ ] **Step 1: 写 hash 失效测试**

```python
def test_preview_hash_changes_for_every_submitted_input(tmp_path):
    base = prepare_fixture(tmp_path)
    first = prepare_preview(**base)
    for mutate in (change_body, change_cover_bytes, change_inline_image, change_theme, change_color):
        mutate(tmp_path)
        second = prepare_preview(**base)
        assert second.preview_hash != first.preview_hash
        reset_fixture(tmp_path)
```

另测 symlink escape、缺图片、损坏图片、`assets.manifest.json.reviewed_hash` 与当前文章不一致、封面缺失时回退正文首图，以及 HTML 中仍有未替换占位。不存在封面且正文无图时明确失败。

- [ ] **Step 2: 确认测试失败**

Run: `.venv/bin/pytest tests/test_wechat_preview.py -v`

Expected: FAIL。

- [ ] **Step 3: 实现本地最终预览**

预览阶段先验证 `assets.manifest.json.reviewed_hash == article_text_hash(article)`；再将本地图片编码成 `data:` URL，只用于 `article.html`。不得获取 token 或请求微信。输出原子写入文章目录 `article.html`。显式 cover 优先，其次 Frontmatter `coverImage`，最后回退第一张正文图。`DraftPreview.to_tool_result()` 返回标题、作者、摘要、封面相对路径、图片数量、theme、color、评论设置、preview_path 和 preview_hash。

- [ ] **Step 4: 验证预览**

Run: `.venv/bin/pytest tests/test_wechat_preview.py -q`

Expected: PASS；mock transport 请求数为 0。

- [ ] **Step 5: 提交**

```bash
git add coworker/connectors/wechat/hashing.py coworker/connectors/wechat/preview.py tests/test_wechat_preview.py
git commit -m "feat: prepare hashed WeChat draft previews"
```

### Task 5: 正文图片、封面素材上传

**Files:**
- Create: `coworker/connectors/wechat/images.py`
- Create: `tests/test_wechat_images.py`

**Interfaces:**
- Produces: `upload_body_image(path) -> remote_url`。
- Produces: `upload_cover(path) -> thumb_media_id`。

- [ ] **Step 1: 写端点与响应测试**

```python
def test_uploads_body_image_and_cover_to_distinct_endpoints(client, tmp_path):
    body = upload_body_image(client, tiny_png(tmp_path / "body.png"))
    cover = upload_cover(client, tiny_png(tmp_path / "cover.png"))
    assert body == "https://mmbiz.qpic.cn/body"
    assert cover == "thumb-media-id"
    assert recorded_paths == [
        "/cgi-bin/media/uploadimg",
        "/cgi-bin/material/add_material?type=image",
    ]
```

覆盖文件扩展/MIME、尺寸、体积、透明图、微信 errcode、响应缺字段。上传失败不得进入 draft/add。

- [ ] **Step 2: 确认测试失败**

Run: `.venv/bin/pytest tests/test_wechat_images.py -v`

Expected: FAIL。

- [ ] **Step 3: 实现图片校验和 multipart 上传**

Pillow 执行 verify 后重新 open；统一转 RGB，超限时等比压缩到微信约束内并写临时上传文件，保留原文件不变。正文用 `media/uploadimg` 取 URL；封面用 `material/add_material?type=image` 取永久素材 media_id。临时文件在成功或失败后均删除。

- [ ] **Step 4: 验证上传 Module**

Run: `.venv/bin/pytest tests/test_wechat_images.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add coworker/connectors/wechat/images.py tests/test_wechat_images.py
git commit -m "feat: upload WeChat article images"
```

### Task 6: 草稿创建、回执和重复提交防护

**Files:**
- Create: `coworker/connectors/wechat/drafts.py`
- Create: `tests/test_wechat_drafts.py`

**Interfaces:**
- Produces: `create_draft(preview, client, receipt_store) -> DraftResult`。
- Receipt path: 文章目录 `receipt.json`；只在 `draft/add` 明确返回 `media_id` 后写成功回执。failed/unknown 只作为本次 Tool result 返回。

- [ ] **Step 1: 写成功、重复和 unknown 测试**

```python
def test_second_identical_submission_returns_receipt_without_network(workflow):
    first = workflow.create()
    workflow.transport.reset()
    second = workflow.create()
    assert second.status == "duplicate"
    assert second.receipt.media_id == first.receipt.media_id
    assert workflow.transport.requests == []


def test_draft_add_timeout_is_unknown_and_not_retried(workflow):
    workflow.transport.timeout_on("/cgi-bin/draft/add")
    result = workflow.create()
    assert result.status == "unknown"
    assert result.uploaded_assets
    assert not (workflow.article_dir / "receipt.json").exists()
    assert workflow.transport.count("/cgi-bin/draft/add") == 1
```

- [ ] **Step 2: 确认测试失败**

Run: `.venv/bin/pytest tests/test_wechat_drafts.py -v`

Expected: FAIL。

- [ ] **Step 3: 实现确定性执行顺序**

执行：重新计算 preview_hash → 查成功 receipt → 上传正文图片 → 替换 HTML 占位 URL → 上传封面 → 调 `/cgi-bin/draft/add`。请求正文只含公众号字段；`content_source_url` 仅在 Frontmatter 提供且是 http/https 时传入；`need_open_comment` 和 `only_fans_can_comment` 来自已进入 preview_hash 的账号设置。

```python
@dataclass(frozen=True)
class DraftReceipt:
    preview_hash: str
    media_id: str
    title: str
    submitted_at: str
    account_id: str  # 仅非敏感 AppID 后四位或其 hash


@dataclass(frozen=True)
class DraftResult:
    status: Literal["success", "duplicate", "failed", "unknown"]
    receipt: DraftReceipt | None
    error_kind: str | None = None
    uploaded_assets: tuple[str, ...] = ()
```

明确 errcode 返回 failed；连接在发送 draft/add 前失败返回 failed；发送后 timeout/连接中断返回 unknown，不自动重试。failed/unknown 结果列出已经上传且微信不支持回滚的素材，不宣称撤销成功。只有 success 原子写 `receipt.json`，权限 0600；duplicate 读取该成功回执。

- [ ] **Step 4: 验证幂等和状态**

Run: `.venv/bin/pytest tests/test_wechat_drafts.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add coworker/connectors/wechat/drafts.py tests/test_wechat_drafts.py
git commit -m "feat: create idempotent WeChat drafts"
```

### Task 7: 注册公众号 Tools 与风险审批

**Files:**
- Create: `coworker/connectors/wechat/tools.py`
- Modify: `coworker/connectors/integration_tools.py`
- Modify: `coworker/connectors/tool_defs.py`
- Modify: `coworker/risk.py`
- Create: `tests/test_integration_tools.py`
- Modify: `tests/test_permissions_risk.py`

**Interfaces:**
- Produces Tools:
  - `prepare_wechat_draft(article_path, theme, color=None, cover_path=None)`
  - `create_wechat_draft(article_path, preview_hash, theme, color=None, cover_path=None)`
- Guarantee: create Tool 的参数中没有 credentials 或 access token。

- [ ] **Step 1: 写 Tool registry 失败测试**

```python
def test_wechat_tools_only_exist_when_connector_enabled(tmp_path):
    secrets = connected_wechat_store(tmp_path)
    names = {t.__name__ for t in make_integration_tools(secrets, enabled_connectors={"wechat_official"}, roots=[tmp_path])}
    assert {"prepare_wechat_draft", "create_wechat_draft"} <= names
    assert "publish_wechat_article" not in names


def test_create_tool_is_external_and_prepare_is_local():
    assert classify(tool("prepare_wechat_draft")) == RiskClass.WRITE_LOCAL
    assert classify(tool("create_wechat_draft")) == RiskClass.EXTERNAL
```

- [ ] **Step 2: 确认测试失败**

Run: `.venv/bin/pytest tests/test_integration_tools.py tests/test_permissions_risk.py -q`

Expected: FAIL。

- [ ] **Step 3: 实现独立 Tool factory**

`make_wechat_tools(secrets, roots, client_factory=WeChatClient.from_store)` 生成闭包；`integration_tools.make_integration_tools` 只 import 并 extend，不把微信逻辑塞入现有 4,000 行函数。`tool_defs` 增加两个定义，默认启用；create kind=`write`，target_arg 留空，防止给“任意草稿创建”建立宽泛 standing approval。

`create_wechat_draft` 先在本地重新调用 preview pipeline 并比较传入 hash；不一致时在构建 Client 前失败。只有通过 hash 校验才从 SecretStore 读取凭据。

- [ ] **Step 4: 验证 Tool 安全属性**

Run: `.venv/bin/pytest tests/test_integration_tools.py tests/test_permissions_risk.py tests/test_wechat_*.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add coworker/connectors/wechat/tools.py coworker/connectors/integration_tools.py coworker/connectors/tool_defs.py coworker/risk.py tests/test_integration_tools.py tests/test_permissions_risk.py
git commit -m "feat: expose approved WeChat draft tools"
```

### Task 8: 最终图文预览和第二次审批 UI

**Files:**
- Modify: `surfaces/gui/src/humanize.ts`
- Modify: `surfaces/gui/src/components/ApprovalCard.tsx`
- Modify: `surfaces/gui/src/components/ApprovalCard.test.tsx`
- Modify: `surfaces/gui/e2e/approval-card.spec.ts`
- Create: `surfaces/gui/e2e/artifact-preview.spec.ts`

**Interfaces:**
- Produces: create Tool 卡片显示公众号、标题、摘要、封面、正文图数量、theme/color 和“只保存到草稿箱”。
- Produces: Tool 结果明确显示 `success`、`duplicate`、`failed`、`unknown`，unknown 提醒先检查公众号后台。
- Guarantee: 卡片和结果不显示 AppID 全值、AppSecret、token、绝对路径或整篇 HTML。

- [ ] **Step 1: 写第二审批卡失败测试**

```tsx
it("states that WeChat action creates a draft only", () => {
  render(<ApprovalCard item={wechatDraftApproval()} />);
  expect(screen.getByText("保存到公众号草稿箱，不会正式发布")).toBeInTheDocument();
  expect(screen.getByText("正文图片 2 张")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "批准并保存草稿" })).toBeInTheDocument();
  expect(screen.queryByText(/AppSecret|access_token|wx-full-appid/)).toBeNull();
});
```

- [ ] **Step 2: 确认测试失败**

Run: `cd surfaces/gui && npm test -- --run src/components/ApprovalCard.test.tsx`

Expected: FAIL。

- [ ] **Step 3: 实现专用摘要和结果状态，不改变通用审批协议**

ApprovalCard 按 Tool name 渲染安全字段；所需展示数据应由 create Tool args 中的非敏感 article/theme/color 和最近 preview result 派生。若卡片无法访问 preview result，则后端在 approval event 中增加显式 `display` metadata，而不是把秘密或整篇 HTML 塞入 args。Transcript 对四种结果使用中文状态；`duplicate` 表示“已存在，未重复创建”，`unknown` 表示“提交结果未知，请先检查公众号草稿箱”，并列出不可回滚素材数量。

`humanize.ts`：

```ts
prepare_wechat_draft: (a) => ({ pre: "生成了公众号最终图文预览：", obj: basename(a.article_path) }),
create_wechat_draft: (a) => ({ pre: "保存到公众号草稿箱：", obj: basename(a.article_path) }),
```

- [ ] **Step 4: 验证前端**

Run: `cd surfaces/gui && npm test -- --run && npx playwright test e2e/approval-card.spec.ts e2e/artifact-preview.spec.ts`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add surfaces/gui/src/humanize.ts surfaces/gui/src/components/ApprovalCard.tsx surfaces/gui/src/components/ApprovalCard.test.tsx surfaces/gui/e2e
git commit -m "feat: present WeChat draft preview and approval"
```

### Task 9: 公众号工作流端到端测试

**Files:**
- Create: `tests/test_wechat_workflow.py`
- Modify: `tests/test_server.py`
- Modify only if workflow exposes a verified defect.

**Interfaces:**
- Produces: 真实用户闭环和回归网。

- [ ] **Step 1: 写离线 E2E**

用真实 Markdown/图片文件、FastAPI TestClient、MockTransport 和临时 SecretStore 完成：连接公众号 → prepare preview → 修改图片使旧 hash 被拒绝且零微信请求 → 新 preview → 批准 create → 检查 media_id/receipt → 相同内容再次提交零请求。

- [ ] **Step 2: 运行公众号测试**

Run: `.venv/bin/pytest tests/test_wechat_*.py -q`

Expected: PASS。

- [ ] **Step 3: 完整回归**

Run: `.venv/bin/pytest -q`

Run: `cd surfaces/gui && npm run build && npm test -- --run && npx playwright test`

Expected: PASS。

- [ ] **Step 4: 真实公众号烟雾验证**

启动本地后端和浏览器 UI；在连接页输入真实 AppID/AppSecret；使用一篇专用测试文章完成两次审批。公众号后台确认草稿存在，标题、摘要、正文、封面和正文图正确；本地 `receipt.json` media_id 一致；再次提交返回已有回执且不产生第二份草稿。测试凭据、receipt 和生成资产不得提交。

- [ ] **Step 5: 提交工作流测试或修复**

```bash
git add tests/test_wechat_workflow.py tests/test_server.py <必要修复文件>
git commit -m "test: cover WeChat draft workflow"
```
