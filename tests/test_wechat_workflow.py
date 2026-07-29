from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from coworker.connectors.wechat import WeChatClient
from coworker.connectors.wechat.tools import make_wechat_tools
from coworker.content import article_text_hash, load_article
from coworker.engine import ApprovalOutcome, PermissionRequest, TurnEngine
from coworker.events import EventType
from coworker.permissions import PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.server import SessionManager, create_app
from coworker.tools import ToolRegistry

_APP_ID = "wx-workflow-app"
_APP_SECRET = "workflow-app-secret-must-not-leak"
_ACCESS_TOKEN = "workflow-access-token-must-not-leak"
_DRAFT_MEDIA_ID = "draft-workflow-media-id"
_FOUR_DRAFT_STATES = {"success", "duplicate", "failed", "unknown"}


def _write_png(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (12, 8), color).save(path, format="PNG")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(article_path: Path) -> None:
    asset_paths = ("cover.png", "images/body.png")
    manifest = {
        "reviewed_hash": article_text_hash(load_article(article_path)),
        "plan_hash": "1" * 64,
        "provider": "offline-fixture-provider",
        "model": "offline-fixture-model",
        "assets": [
            {
                "output_path": relative_path,
                "sha256": _sha256(article_path.parent / relative_path),
            }
            for relative_path in asset_paths
        ],
    }
    (article_path.parent / "assets.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_article_fixture(tmp_path: Path) -> Path:
    article_dir = tmp_path / "wechat-article"
    article_dir.mkdir()
    article_path = article_dir / "article.md"
    article_path.write_text(
        "---\n"
        "title: 离线公众号闭环\n"
        "author: 文枢团队\n"
        "summary: 审批后只保存草稿\n"
        "sourceUrl: https://example.test/reviewed-source\n"
        "coverImage: cover.png\n"
        "---\n\n"
        "## 正文\n\n"
        "这是一篇已经审阅的文章。\n\n"
        "![正文插图](images/body.png)\n",
        encoding="utf-8",
    )
    _write_png(article_dir / "cover.png", (20, 90, 180))
    _write_png(article_dir / "images/body.png", (30, 180, 90))
    _write_manifest(article_path)
    return article_path


class _WorkflowProvider(ProviderClient):
    def __init__(self, arguments: dict[str, object]) -> None:
        self._turns = [
            AssistantTurn(
                tool_calls=[
                    ToolCall(
                        id="create-first",
                        name="create_wechat_draft",
                        arguments=dict(arguments),
                    )
                ],
                finish_reason="tool_calls",
            ),
            AssistantTurn(
                tool_calls=[
                    ToolCall(
                        id="create-duplicate",
                        name="create_wechat_draft",
                        arguments=dict(arguments),
                    )
                ],
                finish_reason="tool_calls",
            ),
            AssistantTurn(text="草稿处理完成", finish_reason="stop"),
        ]
        self.provider_inputs: list[dict[str, object]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.provider_inputs.append(
            {
                "messages": copy.deepcopy(messages),
                "tools": copy.deepcopy(tools),
            }
        )
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _collect(engine: TurnEngine):
    async def collect_events():
        return [event async for event in engine.run("把已审阅内容保存到公众号草稿箱")]

    return asyncio.run(collect_events())


def _tool_result(event) -> dict[str, object]:
    assert event.data["status"] == "ok"
    result = json.loads(event.data["result_preview"])
    assert result["status"] in _FOUR_DRAFT_STATES
    assert event.data["display"] == {"wechat_draft_result": result}
    return result


def test_wechat_offline_review_to_draft_workflow(tmp_path, monkeypatch) -> None:
    """One contract from REST connection through review approval and idempotent draft."""

    manager = SessionManager(workspace=tmp_path, provider=_WorkflowProvider({}))
    with monkeypatch.context() as connect_patch:
        connect_patch.setattr(
            WeChatClient,
            "get_access_token",
            lambda self: "validator-only-token",
        )
        with TestClient(create_app(manager)) as rest:
            connected = rest.post(
                "/v1/connectors/wechat_official/connect",
                json={
                    "fields": {
                        "app_id": _APP_ID,
                        "app_secret": _APP_SECRET,
                    }
                },
            )
            assert connected.status_code == 200
            assert connected.json() == {"ok": True, "identity": _APP_ID}
            comments = rest.patch(
                "/v1/connectors/wechat_official/settings",
                json={
                    "need_open_comment": True,
                    "only_fans_can_comment": True,
                },
            )
            assert comments.status_code == 200
            assert comments.json() == {
                "need_open_comment": True,
                "only_fans_can_comment": True,
            }

    assert manager.secrets.get("wechat_official:default") == {
        "type": "token",
        "enabled": True,
        "app_id": _APP_ID,
        "app_secret": _APP_SECRET,
        "identity": _APP_ID,
        "need_open_comment": True,
        "only_fans_can_comment": True,
    }

    article_path = _write_article_fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    article_argument = "wechat-article/article.md"
    requests: list[httpx.Request] = []
    clients: list[WeChatClient] = []

    def wechat_response(request: httpx.Request) -> httpx.Response:
        request.read()
        requests.append(request)
        assert request.url.scheme == "https"
        assert request.url.host == "api.weixin.qq.com"
        if request.url.path == "/cgi-bin/token":
            return httpx.Response(
                200,
                json={"access_token": _ACCESS_TOKEN, "expires_in": 7200},
            )
        if request.url.path == "/cgi-bin/media/uploadimg":
            return httpx.Response(
                200,
                json={"url": "https://mmbiz.qpic.cn/workflow/body.png?a=1&b=2"},
            )
        if request.url.path == "/cgi-bin/material/add_material":
            return httpx.Response(
                200,
                json={
                    "media_id": "cover-workflow-media-id",
                    "url": "https://mmbiz.qpic.cn/workflow/cover.png",
                },
            )
        if request.url.path == "/cgi-bin/draft/add":
            return httpx.Response(200, json={"media_id": _DRAFT_MEDIA_ID})
        raise AssertionError(f"unexpected WeChat API path: {request.url.path}")

    def client_factory(secrets):
        client = WeChatClient.from_store(
            secrets,
            transport=httpx.MockTransport(wechat_response),
        )
        clients.append(client)
        return client

    prepare_draft, create_draft = make_wechat_tools(
        manager.secrets,
        roots=[tmp_path],
        client_factory=client_factory,
    )
    first_preview = prepare_draft(
        article_argument,
        "default",
        cover_path="cover.png",
    )
    old_hash = first_preview["preview_hash"]
    assert first_preview == {
        "channel": "微信公众号",
        "title": "离线公众号闭环",
        "digest": "审批后只保存草稿",
        "cover_path": "cover.png",
        "image_count": 1,
        "theme": "default",
        "color": "#07C160",
        "draft_only": True,
        "preview_hash": old_hash,
    }

    _write_png(article_path.parent / "images/body.png", (190, 40, 70))
    stale_result = create_draft(
        article_argument,
        old_hash,
        "default",
        cover_path="cover.png",
    )
    assert stale_result["status"] == "failed"
    assert stale_result["error_kind"] == "preview_changed"
    assert clients == []
    assert requests == []

    _write_manifest(article_path)
    refreshed_preview = prepare_draft(
        article_argument,
        "default",
        cover_path="cover.png",
    )
    preview_hash = refreshed_preview["preview_hash"]
    assert preview_hash != old_hash
    arguments = {
        "article_path": article_argument,
        "preview_hash": preview_hash,
        "theme": "default",
        "color": "#07C160",
        "cover_path": "cover.png",
    }

    provider = _WorkflowProvider(arguments)
    registry = ToolRegistry()
    registry.register_all([prepare_draft, create_draft])
    approvals: list[PermissionRequest] = []

    async def allow_once(request: PermissionRequest) -> ApprovalOutcome:
        approvals.append(request)
        return ApprovalOutcome.ONCE

    permissions = PermissionEngine(workspace_root=tmp_path)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model="offline-model",
        approver=allow_once,
    )
    events = _collect(engine)

    permission_events = [
        event for event in events if event.type is EventType.PERMISSION_REQUIRED
    ]
    assert len(permission_events) == len(approvals) == 2
    expected_approval_display = {
        "channel": "微信公众号",
        "title": "离线公众号闭环",
        "digest": "审批后只保存草稿",
        "cover_path": "cover.png",
        "image_count": 1,
        "theme": "default",
        "color": "#07C160",
        "draft_only": True,
    }
    for event, request in zip(permission_events, approvals, strict=True):
        assert event.data["arguments"] == expected_approval_display
        assert event.data["approval_once_only"] is True
        assert event.data["standing_target"] is None
        assert request.arguments == arguments
        assert request.display_arguments == expected_approval_display
        assert request.approval_once_only is True
        approval_text = json.dumps(event.data, ensure_ascii=False)
        assert "article_path" not in approval_text
        assert "preview_hash" not in approval_text
        assert preview_hash not in approval_text
        assert str(tmp_path) not in approval_text
        assert _APP_SECRET not in approval_text
        assert _ACCESS_TOKEN not in approval_text
    assert "create_wechat_draft" not in permissions.session_allow_tools

    finished = [
        event
        for event in events
        if event.type is EventType.TOOL_FINISHED
        and event.data["name"] == "create_wechat_draft"
    ]
    assert len(finished) == 2
    success = _tool_result(finished[0])
    duplicate = _tool_result(finished[1])
    assert success == {
        "status": "success",
        "title": "离线公众号闭环",
        "error_kind": None,
        "uploaded_asset_count": 2,
        "uploaded_assets": ["body:images/body.png", "cover:cover.png"],
        "draft_only": True,
    }
    assert duplicate == {
        "status": "duplicate",
        "title": "离线公众号闭环",
        "error_kind": None,
        "uploaded_asset_count": 0,
        "uploaded_assets": [],
        "draft_only": True,
    }

    assert [request.url.path for request in requests] == [
        "/cgi-bin/token",
        "/cgi-bin/media/uploadimg",
        "/cgi-bin/material/add_material",
        "/cgi-bin/draft/add",
    ]
    token_request, body_request, cover_request, draft_request = requests
    assert token_request.method == "GET"
    assert dict(token_request.url.params) == {
        "grant_type": "client_credential",
        "appid": _APP_ID,
        "secret": _APP_SECRET,
    }
    assert dict(body_request.url.params) == {"access_token": _ACCESS_TOKEN}
    assert dict(cover_request.url.params) == {
        "type": "image",
        "access_token": _ACCESS_TOKEN,
    }
    assert dict(draft_request.url.params) == {"access_token": _ACCESS_TOKEN}
    for upload_request in (body_request, cover_request):
        assert upload_request.method == "POST"
        assert upload_request.headers["content-type"].startswith("multipart/form-data;")
        assert b'name="media"' in upload_request.content
        assert b'filename="wechat-image.png"' in upload_request.content
        assert b"\x89PNG\r\n\x1a\n" in upload_request.content

    draft_payload = json.loads(draft_request.content)
    assert set(draft_payload) == {"articles"}
    assert len(draft_payload["articles"]) == 1
    submitted_article = draft_payload["articles"][0]
    assert submitted_article == {
        "title": "离线公众号闭环",
        "author": "文枢团队",
        "digest": "审批后只保存草稿",
        "content": submitted_article["content"],
        "thumb_media_id": "cover-workflow-media-id",
        "need_open_comment": 1,
        "only_fans_can_comment": 1,
        "content_source_url": "https://example.test/reviewed-source",
    }
    assert "data-wenshu-image" not in submitted_article["content"]
    assert (
        'src="https://mmbiz.qpic.cn/workflow/body.png?a=1&amp;b=2"'
        in submitted_article["content"]
    )

    receipt = json.loads((article_path.parent / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["media_id"] == _DRAFT_MEDIA_ID
    assert receipt["preview_hash"] == preview_hash
    assert receipt["title"] == "离线公众号闭环"
    assert receipt["account_id"].startswith("sha256:")
    assert all(client.is_closed for client in clients)
    assert len(clients) == 2

    assert len(provider.provider_inputs) == 3
    provider_view = json.dumps(provider.provider_inputs, ensure_ascii=False, default=str)
    assert '"_display"' not in provider_view
    assert _APP_SECRET not in provider_view
    assert _ACCESS_TOKEN not in provider_view
    assert str(tmp_path) not in provider_view
    provider_results = [
        json.loads(message["content"])
        for call in provider.provider_inputs
        for message in call["messages"]
        if message.get("role") == "tool"
    ]
    assert [result["status"] for result in provider_results] == [
        "success",
        "success",
        "duplicate",
    ]
    assert all("_display" not in result for result in provider_results)

    internal_tool_messages = [
        message for message in engine.messages if message.get("role") == "tool"
    ]
    assert [message["_display"] for message in internal_tool_messages] == [
        {"wechat_draft_result": success},
        {"wechat_draft_result": duplicate},
    ]
    observable = json.dumps(
        {
            "events": [event.data for event in events],
            "provider": provider.provider_inputs,
            "receipt": receipt,
            "tool_messages": internal_tool_messages,
        },
        ensure_ascii=False,
        default=str,
    )
    assert _APP_SECRET not in observable
    assert _ACCESS_TOKEN not in observable
    assert str(tmp_path) not in observable
