# Task 7 Backend Regression Report

## Status

后端全量回归通过：`964 passed, 1 skipped`。Legacy 测试夹具修复不放宽生产配置；终审补充修复让网关、入站消息和连接视图也服从文枢产品 allowlist。

## Root cause

17 个失败共享同一条产品配置数据流：

1. `SessionManager(..., product=None)` 通过 `current_product()` 取得生产 `WENSHU_PROFILE`。
2. 文枢只公开 `browser`、`wechat_official`，并关闭 `cloud`、`gallery`、`managed_oauth`、`relay`、`updater`。
3. `SessionManager.list_connectors()` 按 `manager.product.visible_connectors` 过滤 Slack、GitHub、HubSpot 等国外 Connector。
4. `create_app()` 的 `/oauth/callback` 和 managed-connect 路由按 `manager.product.features["managed_oauth"]` gate，旧 managed OAuth 测试因此得到 404。
5. `SessionManager.get_engine()` 把同一 ProductProfile 传入 `build_engine()`；registry 据此过滤 Connector tools 和 messaging allowed platforms。GitHub tool 测试看不到 `github_search`，UI refresh E2E 虽能镜像审批卡，却无法执行目标为 `slack:C_OPS` 的 `send_message`。

这不是生产文枢回归，而是旧 OpenWorker 功能测试隐含依赖历史全功能产品配置。

## Red evidence

修改前运行列出的失败集合：

```text
.venv/bin/python -m pytest -q \
  tests/test_connections.py::test_muted_connector_tools_absent \
  tests/test_connectors_allowlist.py \
  tests/test_github_installs.py \
  tests/test_hubspot_portals.py::test_managed_callback_lands_in_portal_profile \
  tests/test_server.py::test_google_one_click_paused_but_manual_alive \
  tests/test_slack_approval_owners.py \
  tests/test_slack_workspaces.py \
  tests/test_subscriptions.py \
  tests/test_team_allowlist.py \
  tests/test_ui_refresh_e2e.py
```

结果：`17 failed, 44 passed, 1 warning`。

失败表现与 profile gate 一致：Slack rows `StopIteration`、managed OAuth callback 404、Google managed-paused 路由返回 feature-disabled envelope、GitHub tool 缺失，以及 Slack reply 未发出。

## Test-harness change

`tests/conftest.py` 新增非 autouse、session-scoped 的 `permissive_product` fixture：

- 返回仅测试可见的 `ProductProfile(id="openworker-test", ...)`。
- `visible_connectors` 是全部 Connector descriptor 名称的 `frozenset`。
- 当前五个产品 feature 全部显式为 `True`。
- `ProductProfile` 自身是 frozen dataclass，且会把 `features` 固化为 `MappingProxyType`，因此 session-scoped 共享对象不可变。
- fixture 不 monkeypatch `current_product()`，未污染任何依赖真实 `WENSHU_PROFILE` 的 gate 测试。

只在确实验证 legacy connector/cloud 行为的构造 seam 注入：

- GitHub、HubSpot、Slack managed client fixtures。
- Slack allowlist、subscription、team REST、approval-owner REST managers。
- GitHub connector-tool engine 测试。
- Google managed-paused 路由测试。
- UI refresh E2E manager；其 `send_message` target 为 Slack，注入 profile 同样显式公开 Slack。

深层共享 helper `_manager` 与 `_relay_manager` 增加可选 `product` seam；只有需要 Connector REST surface 的调用方传入 permissive fixture，其他测试继续走生产默认 profile。

## Green evidence

列出的 17 个原始失败节点：

```text
pytest: 17 passed, 1 warning in 13.73s
```

完整运行 10 个受影响测试文件，并同时运行三个文枢 gate 文件：

```text
.venv/bin/python -m pytest -q \
  tests/test_connections.py \
  tests/test_connectors_allowlist.py \
  tests/test_github_installs.py \
  tests/test_hubspot_portals.py \
  tests/test_server.py \
  tests/test_slack_approval_owners.py \
  tests/test_slack_workspaces.py \
  tests/test_subscriptions.py \
  tests/test_team_allowlist.py \
  tests/test_ui_refresh_e2e.py \
  tests/test_product.py \
  tests/test_connectors.py \
  tests/test_cloud_server.py
```

最终结果：`218 passed, 1 warning in 35.01s`。

唯一 warning 是既有 FastAPI TestClient 对 `httpx` 的 `StarletteDeprecationWarning`，与本修改无关。

## Final review follow-up

终审发现遗留手动 Slack/Telegram profile 仍可能启动本地 gateway adapter，并进入 effective/persona/session connection surface。`4ac0c1b` 完成以下收口：

- `load_settings(..., platforms=visible)` 在读取隐藏 profile 之前按产品过滤，默认 `platforms=None` 保持 legacy 行为。
- Gateway adapter 构造、入站缓冲/分发、effective connector、Persona/Session view 与 recommends 使用同一 `visible_connectors`。
- Persona/Session connection POST 拒绝隐藏 connector，同时不迁移、不删除既有隐藏存储数据。
- Legacy messaging tests 显式注入 `permissive_product`。

原 finding 复审结论：Approved，无剩余 Critical/Important。

## Full backend acceptance

```text
.venv/bin/pytest -q
```

最终结果：`964 passed, 1 skipped, 1 warning`。唯一 warning 仍是既有 FastAPI TestClient 对 `httpx` 的 `StarletteDeprecationWarning`。

## Changed files

- `tests/conftest.py`
- `tests/test_connections.py`
- `tests/test_connectors_allowlist.py`
- `tests/test_github_installs.py`
- `tests/test_hubspot_portals.py`
- `tests/test_server.py`
- `tests/test_slack_approval_owners.py`
- `tests/test_slack_workspaces.py`
- `tests/test_subscriptions.py`
- `tests/test_team_allowlist.py`
- `tests/test_ui_refresh_e2e.py`
- `coworker/connectors/config.py`
- `coworker/connectors/setup.py`
- `coworker/server/app.py`
- `coworker/server/manager.py`
- `tests/test_dm_routing.py`
- `tests/test_mention_router.py`
- `tests/test_message_source.py`
- `tests/test_persona_connections.py`
- `tests/test_slack_relay.py`
- `.superpowers/sdd/task-7-backend-report.md`

## Remaining risk

- permissive fixture 的 Connector 集合会随 descriptor catalog 自动包含新增项；feature 集合则显式覆盖当前 ProductProfile 的五个 feature，未来新增 feature 时需要明确决定 legacy fixture 是否启用。
- 产品 allowlist 现在覆盖工具注册、连接视图、写路径、gateway/profile 读取和入站分发；既有隐藏凭据保留在本地但不可见、不可启用、不可收发。
