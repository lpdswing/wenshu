# 文枢原生内容与生图流水线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用第一方 Python Modules 和 Tools 完成“生成 Markdown → 纯文字预览 → 用户批准 → OpenAI Images 批量生图”的闭环，确保批准前图片接口调用次数为零。

**Architecture:** `coworker/content/` 负责文章领域模型、Frontmatter、规范化哈希、文字审阅页和配图规划；`coworker/image_generation/` 提供深层 `ImageGenerationProvider` Interface 及 OpenAI Adapter。`make_content_tools` 将文件访问、审批元数据、SecretStore 和图片 Provider 封装在 Tool 闭包内；Agent 只看到四个窄 Tool。

**Tech Stack:** Python 3.12、PyYAML、Markdown-It-Py、httpx、Pillow、FastAPI 静态文件能力、pytest。

**Depends on:** `2026-07-27-wenshu-product-shell.md` 已完成；默认 Persona id 仍为 `cowork`。

## Global Constraints

- 只接受当前会话允许根目录中的文件；路径越界在任何读取、写入或网络调用前失败。
- `prepare_article_review` 不调用图片服务；`generate_article_assets` 必须校验 `reviewed_hash`。
- 正文变化使旧 `reviewed_hash` 失效。
- 批量生图一次审批，卡片显示 Provider、模型和预计图片总数。
- API key 不进入 Tool 参数、模型上下文、普通日志、审计参数或生成文件。
- 图片 Provider 为 OpenAI Images；模型默认 `gpt-image-2`，但从 provider profile 读取可覆盖项。
- 本计划不实现公众号凭据、图片上传或 draft/add。

---

### Task 1: 文章领域模型、Frontmatter 和稳定内容哈希

**Files:**
- Create: `coworker/content/__init__.py`
- Create: `coworker/content/models.py`
- Create: `coworker/content/article.py`
- Create: `coworker/content/hashing.py`
- Create: `tests/test_content_article.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `ArticleFrontmatter`、`ArticleDocument`、`ImageAsset`、`load_article(path)`、`article_text_hash(article)`。
- Guarantee: hash 只覆盖规范化 Frontmatter 和 Markdown 正文，不覆盖文件路径、mtime 或图片二进制。

- [ ] **Step 1: 写解析与哈希失败测试**

```python
ARTICLE = """---
title: 文枢项目介绍
author: 作者
summary: 面向中文内容工作的本地 AI Worker
coverImage: cover.png
sourceUrl: https://example.com
---

# 文枢项目介绍

正文内容。
"""


def test_loads_required_frontmatter(tmp_path):
    path = tmp_path / "article.md"
    path.write_text(ARTICLE, encoding="utf-8")
    article = load_article(path)
    assert article.meta.title == "文枢项目介绍"
    assert article.body.startswith("# 文枢")


def test_hash_is_stable_across_newlines_and_key_order(tmp_path):
    first = load_article(write_article(tmp_path / "a.md", ARTICLE))
    second = load_article(write_article(tmp_path / "b.md", reordered_article(ARTICLE)))
    assert article_text_hash(first) == article_text_hash(second)

def test_title_is_required_before_work(tmp_path):
    path = tmp_path / "article.md"
    path.write_text("---\nauthor: 作者\n---\n\n正文\n", encoding="utf-8")
    with pytest.raises(ArticleValidationError, match="title"):
        load_article(path)


def test_author_summary_and_source_are_optional(tmp_path):
    path = tmp_path / "article.md"
    path.write_text("---\ntitle: 标题\n---\n\n正文\n", encoding="utf-8")
    article = load_article(path)
    assert article.meta.author == ""
    assert article.meta.summary == ""
    assert article.meta.source_url is None
```

- [ ] **Step 2: 安装显式依赖并确认测试失败**

在 `pyproject.toml` 添加 `PyYAML>=6.0` 和 `markdown-it-py>=4.0`，不要依赖 Textual 的传递依赖。

Run: `.venv/bin/pip install -e '.[messaging,dev]' && .venv/bin/pytest tests/test_content_article.py -v`

Expected: FAIL，content package 尚不存在。

- [ ] **Step 3: 实现模型和规范化**

```python
@dataclass(frozen=True)
class ArticleFrontmatter:
    title: str
    author: str = ""
    summary: str = ""
    cover_image: str | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class ImageAsset:
    path: Path
    media_type: str
    width: int
    height: int
    sha256: str
    provider: str | None = None
    model: str | None = None

@dataclass(frozen=True)
class ArticleDocument:
    path: Path
    meta: ArticleFrontmatter
    body: str


def article_text_hash(article: ArticleDocument) -> str:
    payload = {
        "frontmatter": asdict(article.meta),
        "body": article.body.replace("\r\n", "\n").strip() + "\n",
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
```

`load_article` 使用 `yaml.safe_load`，拒绝缺失或空标题、字段类型错误和多文档 YAML；`author`、`summary`、`coverImage`、`sourceUrl` 缺省为空。错误包含字段名但不回显整篇正文。

- [ ] **Step 4: 验证文章模型**

Run: `.venv/bin/pytest tests/test_content_article.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml coworker/content tests/test_content_article.py
git commit -m "feat: add Wenshu article model"
```

### Task 2: 安全根目录和纯文字审阅页

**Files:**
- Create: `coworker/content/paths.py`
- Create: `coworker/content/review.py`
- Create: `tests/test_content_review.py`

**Interfaces:**
- Produces: `resolve_in_roots(path, roots, must_exist)`、`prepare_article_review_file(article_path, roots)`。
- Produces: `ArticleReview(title, summary, article_path, preview_path, reviewed_hash)`。

- [ ] **Step 1: 写路径和零网络失败测试**

```python
def test_review_renders_complete_text_without_images(tmp_path, monkeypatch):
    article_path = write_article(tmp_path / "article.md", ARTICLE)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("network forbidden"))
    result = prepare_article_review_file(article_path, [tmp_path])
    html = result.preview_path.read_text(encoding="utf-8")
    assert "文枢项目介绍" in html
    assert "正文内容" in html
    assert "<img" not in html
    assert result.reviewed_hash == article_text_hash(load_article(article_path))


def test_review_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / "secret.md"
    outside.write_text(ARTICLE)
    (tmp_path / "escape.md").symlink_to(outside)
    with pytest.raises(ContentPathError):
        prepare_article_review_file(tmp_path / "escape.md", [tmp_path])
```

- [ ] **Step 2: 确认测试失败**

Run: `.venv/bin/pytest tests/test_content_review.py -v`

Expected: FAIL，review module 尚不存在。

- [ ] **Step 3: 实现安全路径和审阅 HTML**

`resolve_in_roots` 对输入和根目录使用 `Path.resolve(strict=must_exist)`，并用 `candidate.is_relative_to(root)` 验证；禁止仅做字符串前缀比较。

`review.py` 使用 MarkdownIt 渲染，但删除/忽略 image token；生成自包含 UTF-8 HTML，所有样式内联在 `<style>` 中，不引用 CDN。输出固定为文章同目录的 `review.html`，使用临时文件加 `os.replace` 原子写入。

- [ ] **Step 4: 验证审阅页**

Run: `.venv/bin/pytest tests/test_content_review.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add coworker/content/paths.py coworker/content/review.py tests/test_content_review.py
git commit -m "feat: render text-only article reviews"
```

### Task 3: 封面与正文配图规划模型

**Files:**
- Create: `coworker/content/images.py`
- Create: `tests/test_content_images.py`

**Interfaces:**
- Produces: `CoverRequest`、`IllustrationRequest`、`IllustrationPlan`、`AssetManifest`。
- Produces: `parse_asset_plan(cover_request, illustration_plan)`；输入直接来自 Tool 的结构化 JSON 参数。

- [ ] **Step 1: 写计划校验失败测试**

```python
def test_plan_requires_one_cover_and_unique_output_paths():
    plan = parse_asset_plan(VALID_COVER_REQUEST, VALID_ILLUSTRATION_PLAN)
    assert plan.cover.output_path == "cover.png"
    assert len(plan.illustrations) == 2

@pytest.mark.parametrize(
    ("cover", "illustrations"),
    [
        ({**VALID_COVER_REQUEST, "output_path": "/tmp/cover.png"}, VALID_ILLUSTRATION_PLAN),
        ({**VALID_COVER_REQUEST, "output_path": "../cover.png"}, VALID_ILLUSTRATION_PLAN),
        (VALID_COVER_REQUEST, [{**VALID_ILLUSTRATION_PLAN[0], "output_path": "cover.png"}]),
        ({**VALID_COVER_REQUEST, "aspect_ratio": "bad"}, VALID_ILLUSTRATION_PLAN),
    ],
)
def test_invalid_plan_is_rejected_before_generation(cover, illustrations):
    with pytest.raises(AssetPlanError):
        parse_asset_plan(cover, illustrations)
```

- [ ] **Step 2: 确认测试失败**

Run: `.venv/bin/pytest tests/test_content_images.py -v`

Expected: FAIL。

- [ ] **Step 3: 实现窄模型**

```python
@dataclass(frozen=True)
class CoverRequest:
    prompt: str
    output_path: str = "cover.png"
    aspect_ratio: str = "2.35:1"
    image_type: str = "conceptual"
    palette: str = "cool"
    rendering: str = "digital"
    text_density: str = "title-only"
    mood: str = "bold"

@dataclass(frozen=True)
class IllustrationRequest:
    heading: str
    prompt: str
    output_path: str
    aspect_ratio: str = "16:9"
```

JSON schema 只允许这些字段；拒绝未知字段、绝对路径、`..`、重复输出、非 `.png/.jpg/.webp` 扩展和空 prompt。首版总图片数限制 1–9（封面 1 + 正文最多 8），避免意外费用。

- [ ] **Step 4: 验证模型**

Run: `.venv/bin/pytest tests/test_content_images.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add coworker/content/images.py tests/test_content_images.py
git commit -m "feat: model article image plans"
```

### Task 4: ImageGenerationProvider Interface 和 OpenAI Adapter

**Files:**
- Create: `coworker/image_generation/__init__.py`
- Create: `coworker/image_generation/base.py`
- Create: `coworker/image_generation/openai.py`
- Create: `coworker/image_generation/registry.py`
- Create: `tests/test_image_generation.py`

**Interfaces:**
- Produces: `ImageRequest`、`ImageResult`、`ImageGenerationProvider.generate(request)`。
- Produces: `build_image_provider(secrets, profile="openai")`。

- [ ] **Step 1: 写 HTTP Adapter 失败测试**

使用 `httpx.MockTransport`，不要调用真实 OpenAI：

```python
@pytest.mark.asyncio
async def test_openai_images_saves_b64_response(tmp_path):
    transport = httpx.MockTransport(images_response(b64_png=TINY_PNG_B64))
    provider = OpenAIImageProvider(
        api_key="sk-test",
        model="gpt-image-2",
        transport=transport,
    )
    result = await provider.generate(ImageRequest(prompt="水墨风文枢", output_path=tmp_path / "cover.png"))
    assert result.path.read_bytes().startswith(b"\x89PNG")
    assert result.provider == "openai"
    assert result.model == "gpt-image-2"
```

另测：URL 图片响应、401 脱敏、429 分类、无 data、既无 URL 也无 b64、非法图片、超时；异常消息不得含 API key。

- [ ] **Step 2: 确认测试失败**

Run: `.venv/bin/pytest tests/test_image_generation.py -v`

Expected: FAIL。

- [ ] **Step 3: 实现 Interface**

```python
@dataclass(frozen=True)
class ImageRequest:
    prompt: str
    output_path: Path
    aspect_ratio: str = "1:1"
    quality: str = "high"

@dataclass(frozen=True)
class ImageResult:
    path: Path
    provider: str
    model: str
    sha256: str

class ImageGenerationProvider(ABC):
    @abstractmethod
    async def generate(self, request: ImageRequest) -> ImageResult: ...
```

OpenAI Adapter 使用 `httpx.AsyncClient` 调 `/v1/images/generations`，发送 `model`、`prompt`、映射后的 `size` 和 `quality`。响应同时支持 `data[0].b64_json` 与 `data[0].url`：URL 必须是 HTTPS，下载仍使用受控超时和体积上限；两条路径都要验证图片 magic bytes、解码尺寸并原子保存。不得把响应 JSON 或临时下载 URL 写日志。

`build_image_provider` 从 `provider:openai` SecretStore profile 读取 `api_key` 和可选 `base_url`，图片模型从 profile `image_model` 读取，缺省 `gpt-image-2`。同一 profile 供聊天与生图使用，但模型字段独立。

- [ ] **Step 4: 验证 Adapter**

Run: `.venv/bin/pytest tests/test_image_generation.py -q`

Expected: PASS，全部离线。

- [ ] **Step 5: 提交**

```bash
git add coworker/image_generation tests/test_image_generation.py
git commit -m "feat: add OpenAI image generation adapter"
```

#### Task 4A: OpenAI 图片模型设置与连接状态

**Files:**
- Modify: `coworker/providers/registry.py`
- Modify: `coworker/server/manager.py:get_providers,set_provider`
- Modify: `tests/test_settings.py`
- Modify: `surfaces/gui/src/api.ts`
- Modify: `surfaces/gui/src/providers/ProviderSetup.tsx`
- Modify: `surfaces/gui/src/providers/ProviderSetup.test.tsx`

**Interfaces:**
- Produces: OpenAI provider profile 的非敏感 `image_model` 设置，默认 `gpt-image-2`。
- Guarantee: 读取设置只返回模型名和 configured 状态，不返回 API key。

- [ ] **Step 1: 写设置往返失败测试**

```python
def test_openai_image_model_round_trip_without_key_leak(manager):
    result = manager.set_provider("openai", {"api_key": "sk-secret", "image_model": "gpt-image-2"})
    row = next(p for p in manager.get_providers() if p["name"] == "openai")
    assert row["image_model"] == "gpt-image-2"
    assert "sk-secret" not in repr(row)
```

- [ ] **Step 2: 实现后端字段与前端选择**

Provider registry 只允许已知图片模型或自定义非空模型 id；`ProviderSetup` 在 OpenAI 表单增加“图片模型”字段，与聊天模型选择分开。保存沿用 `setProvider`，不得新增第二份 API key。

- [ ] **Step 3: 验证设置 UI**

Run: `.venv/bin/pytest tests/test_settings.py -q`

Run: `cd surfaces/gui && npm test -- --run src/providers/ProviderSetup.test.tsx`

Expected: PASS。

- [ ] **Step 4: 提交**

```bash
git add coworker/providers/registry.py coworker/server/manager.py tests/test_settings.py surfaces/gui/src/api.ts surfaces/gui/src/providers
git commit -m "feat: configure OpenAI image model"
```

### Task 5: 内容 Tools 与 reviewed_hash 硬门

**Files:**
- Create: `coworker/content/tools.py`
- Create: `tests/test_content_tools.py`
- Modify: `coworker/risk.py`
- Modify: `tests/test_permissions_risk.py`

**Interfaces:**
- Produces Tools:
  - `prepare_article_review(article_path)`
  - `generate_article_assets(article_path, reviewed_hash, cover_request, illustration_plan)`
- Guarantee: Tool 参数无 secret；Provider 保存在闭包中。

- [ ] **Step 1: 写“批准前零调用”失败测试**

```python
class SpyImageProvider(ImageGenerationProvider):
    def __init__(self): self.calls = []
    async def generate(self, request):
        self.calls.append(request)
        return fake_result(request.output_path)

@pytest.mark.asyncio
async def test_changed_article_rejects_before_provider_call(tmp_path):
    spy = SpyImageProvider()
    tools = make_content_tools([tmp_path], image_provider=spy)
    review = tools.prepare_article_review(str(write_article(tmp_path / "article.md", ARTICLE)))
    (tmp_path / "article.md").write_text(ARTICLE + "\n修改")
    with pytest.raises(ReviewChangedError):
        await tools.generate_article_assets(
            str(tmp_path / "article.md"),
            review["reviewed_hash"],
            VALID_COVER_REQUEST,
            VALID_ILLUSTRATION_PLAN,
        )
    assert spy.calls == []
```

另测：错误 hash、越界 output、重复路径、超过 9 张、相同 hash/计划的 manifest 复用，以及中途失败后停止后续请求并准确返回已生成资产。

- [ ] **Step 2: 确认测试失败**

Run: `.venv/bin/pytest tests/test_content_tools.py tests/test_permissions_risk.py -v`

Expected: FAIL。

- [ ] **Step 3: 实现 Tool 闭包和 Manifest**

```python
@dataclass
class ContentTools:
    roots: list[Path]
    image_provider: ImageGenerationProvider

    def prepare_article_review(self, article_path: str) -> dict: ...

    async def generate_article_assets(
        self,
        article_path: str,
        reviewed_hash: str,
        cover_request: dict,
        illustration_plan: list[dict],
    ) -> dict: ...
```

执行顺序必须固定：解析安全路径 → 载入文章 → 重新计算 hash → 比较 `reviewed_hash` → 验证 cover/illustration 参数并计算总张数 → 检查全部输出路径 → 才开始 Provider 调用。请求严格串行；任一张失败即停止后续请求，结构化结果列出已生成资产和仍未执行项，不伪造完整成功。全部成功后原子写 `assets.manifest.json`；Manifest 含 `reviewed_hash`、plan hash、provider、model、每张输出相对路径与 sha256，不含 prompt 中的敏感原资料。

在 `risk._BASE`：

```python
"prepare_article_review": RiskClass.WRITE_LOCAL,
"generate_article_assets": RiskClass.EXTERNAL,
```

`generate_article_assets` metadata 设置 `requires_approval=True`、category=`content-generation`；审批展示文章标题、Provider、模型、封面数、正文配图数和总张数。

- [ ] **Step 4: 验证硬门和权限**

Run: `.venv/bin/pytest tests/test_content_tools.py tests/test_permissions_risk.py -q`

Expected: PASS；所有拒绝场景 spy.calls 都为空。

- [ ] **Step 5: 提交**

```bash
git add coworker/content/tools.py coworker/risk.py tests/test_content_tools.py tests/test_permissions_risk.py
git commit -m "feat: gate article image generation on review hash"
```

### Task 6: 将内容能力注册到文枢 Persona

**Files:**
- Modify: `coworker/agents/base.py`
- Modify: `coworker/agents/cowork.py`
- Modify: `coworker/agent.py:build_engine`
- Modify: `coworker/server/manager.py:get_engine`
- Modify: `tests/test_engine.py`
- Modify: `tests/test_builtin_personas.py`

**Interfaces:**
- Produces: `Agent.content_tools: bool = False` trait；文枢 Cowork 为 true。
- Consumes: `SecretStore`、session roots、`build_image_provider`。

- [ ] **Step 1: 写 Tool 注册失败测试**

```python
def test_wenshu_engine_has_native_content_tools(tmp_path, monkeypatch):
    engine = build_engine(
        agent=cowork_agent(),
        workspace=tmp_path,
        secrets=SecretStore(path=tmp_path / "secrets.json"),
        image_provider=SpyImageProvider(),
    )
    assert {"prepare_article_review", "generate_article_assets"} <= set(engine.registry.names())


def test_code_agent_does_not_get_content_tools(tmp_path):
    engine = build_engine(
        agent=code_agent(),
        workspace=tmp_path,
        secrets=SecretStore(path=tmp_path / "secrets.json"),
        image_provider=SpyImageProvider(),
    )
    assert {"prepare_article_review", "generate_article_assets"}.isdisjoint(engine.registry.names())
```

- [ ] **Step 2: 确认测试失败**

Run: `.venv/bin/pytest tests/test_engine.py tests/test_builtin_personas.py -q`

Expected: FAIL。

- [ ] **Step 3: 注册但不按 Persona 名称分支**

`Agent` 新增 `content_tools: bool = False`。`cowork_agent()` 设为 true；Code、Chat、Ops 默认 false。`build_engine` 接受可注入 `secrets` 和 `image_provider` 以便测试；若 `agent.content_tools`，调用 `make_content_tools(engine.roots, image_provider)` 并注册两个方法。生产环境仅在第一次需要生图时构建 Provider，避免未批准时探测网络。

- [ ] **Step 4: 更新系统提示流程**

文枢内容助手明确：先写 `article.md`；调用 `prepare_article_review`；等待用户明确确认文字；确认后根据已审阅文章构造 `cover_request` 和 `illustration_plan`，再调用 `generate_article_assets` 触发一次付费审批。禁止跳过 `reviewed_hash` 或自行编造 hash。

- [ ] **Step 5: 验证注册与回归**

Run: `.venv/bin/pytest tests/test_engine.py tests/test_builtin_personas.py tests/test_tools_permissions.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add coworker/agents coworker/agent.py coworker/server/manager.py tests/test_engine.py tests/test_builtin_personas.py
git commit -m "feat: expose native content tools to Wenshu"
```

### Task 7: 前端 Tool 人类化、审批成本信息和文章预览

**Files:**
- Modify: `surfaces/gui/src/humanize.ts`
- Modify: `surfaces/gui/src/components/ApprovalCard.tsx`
- Modify: `surfaces/gui/src/components/ApprovalCard.test.tsx`
- Modify: `surfaces/gui/e2e/approval-card.spec.ts`
- Modify: `surfaces/gui/e2e/artifact-preview.spec.ts`

**Interfaces:**
- Consumes: Tool args/result，不新增后端 UI 专用 endpoint。
- Produces: 中文文字审阅动作、一次性生图审批卡、已有 artifact preview 展示 review HTML。

- [ ] **Step 1: 写审批卡失败测试**

```tsx
it("shows image provider, model and total before approval", () => {
  render(<ApprovalCard item={imageApproval({ provider: "OpenAI", model: "gpt-image-2", total_images: 4 })} />);
  expect(screen.getByText("OpenAI · gpt-image-2")).toBeInTheDocument();
  expect(screen.getByText("预计生成 4 张图片")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "批准并生成" })).toBeInTheDocument();
});
```

- [ ] **Step 2: 确认前端测试失败**

Run: `cd surfaces/gui && npm test -- --run src/components/ApprovalCard.test.tsx`

Expected: FAIL。

- [ ] **Step 3: 添加窄模板**

`humanize.ts` 对两个 Tool 添加：

```ts
prepare_article_review: (a) => ({ pre: "生成了文章文字预览：", obj: basename(a.article_path) }),
generate_article_assets: (a) => ({ pre: "为文章生成封面与配图：", obj: basename(a.article_path) }),
```

ApprovalCard 仅对 `generate_article_assets` 渲染结构化费用信息；其他 Tool 保持通用布局。不要估算货币金额，只显示模型和张数。

- [ ] **Step 4: 验证最终 UI**

Run: `cd surfaces/gui && npm test -- --run && npx playwright test e2e/approval-card.spec.ts e2e/artifact-preview.spec.ts`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add surfaces/gui/src/humanize.ts surfaces/gui/src/components/ApprovalCard.tsx surfaces/gui/src/components/ApprovalCard.test.tsx surfaces/gui/e2e
git commit -m "feat: present article review and image approval"
```

### Task 8: 内容流水线端到端验证

**Files:**
- Create: `tests/test_content_workflow.py`
- Modify only if the workflow exposes a verified defect.

**Interfaces:**
- Produces: 后续公众号计划可消费 `article.md`、`cover.png`、`images/` 和 `assets.manifest.json`。

- [ ] **Step 1: 写离线完整工作流测试**

测试真实文件写入与 Spy Provider：初稿 → review → 修改后拒绝且零调用 → 新 review → 使用结构化封面/配图参数批准生图 → manifest。断言最终目录：

```text
article.md
review.html
cover.png
images/section-1.png
assets.manifest.json
```

- [ ] **Step 2: 运行内容测试**

Run: `.venv/bin/pytest tests/test_content_*.py tests/test_image_generation.py -q`

Expected: PASS。

- [ ] **Step 3: 运行后端和前端回归**

Run: `.venv/bin/pytest -q`

Run: `cd surfaces/gui && npm run build && npm test -- --run`

Expected: PASS。

- [ ] **Step 4: 真实 OpenAI Images 烟雾验证**

在文枢 Settings 中配置真实 OpenAI key；上传测试资料并生成文章。第一次审阅前观察 Network/后端日志，确认 `/images/generations` 调用次数为 0；修改一次正文并重新审阅；批准生成 1 张封面，确认文件可打开且 manifest sha256 与文件一致。真实凭据和输出图片不得提交。

- [ ] **Step 5: 提交工作流测试或修复**

```bash
git add tests/test_content_workflow.py <必要修复文件>
git commit -m "test: cover Wenshu content workflow"
```
