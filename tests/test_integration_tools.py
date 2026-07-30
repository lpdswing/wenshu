from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from coworker.connectors.integration_tools import make_integration_tools
from coworker.connectors.tool_defs import TOOLS_BY_CONNECTOR, target_arg_for
from coworker.connectors.wechat.drafts import DraftResult
from coworker.connectors.wechat.tools import make_wechat_tools
from coworker.engine import ApprovalOutcome, TurnEngine
from coworker.events import EventType
from coworker.permissions import PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.risk import RiskClass, classify
from coworker.secrets import SecretStore
from coworker.tools.registry import ToolRegistry

_HASH = "a" * 64
_CHANGED_HASH = "b" * 64
_SECRET = "wechat-app-secret-must-never-leak"


def _secrets(tmp_path: Path) -> SecretStore:
    store = SecretStore(tmp_path / "secrets.json")
    store.put(
        "wechat_official:default",
        {
            "enabled": True,
            "app_id": "wx-public-id",
            "app_secret": _SECRET,
            "need_open_comment": True,
            "only_fans_can_comment": True,
        },
    )
    return store


def _preview(tmp_path: Path, preview_hash: str = _HASH):
    return SimpleNamespace(
        title="安全标题",
        digest="安全摘要",
        cover_path="images/cover.png",
        image_count=1,
        image_refs=("images/body.png",),
        theme="default",
        color="#07C160",
        need_open_comment=True,
        only_fans_can_comment=True,
        preview_path=tmp_path / "article.html",
        preview_hash=preview_hash,
    )


def _arguments(**changes):
    arguments = {
        "article_path": "article.md",
        "preview_hash": _HASH,
        "theme": "default",
        "color": "#07C160",
        "cover_path": "images/cover.png",
    }
    arguments.update(changes)
    return arguments


def test_wechat_tools_require_both_connector_and_tool_enablement(tmp_path):
    secrets = _secrets(tmp_path)

    no_connector = make_integration_tools(
        secrets,
        enabled_connectors=set(),
        enabled_tools={"prepare_wechat_draft", "create_wechat_draft"},
    )
    connector_without_tools = make_integration_tools(
        secrets,
        enabled_connectors={"wechat_official"},
        enabled_tools=set(),
    )
    prepare_only = make_integration_tools(
        secrets,
        enabled_connectors={"wechat_official"},
        enabled_tools={"prepare_wechat_draft"},
    )

    assert {tool.__name__ for tool in no_connector} == set()
    assert {tool.__name__ for tool in connector_without_tools} == set()
    assert {tool.__name__ for tool in prepare_only} == {"prepare_wechat_draft"}
    assert not any("publish" in tool.__name__ for tool in make_wechat_tools(secrets))


def test_wechat_tool_defs_schemas_metadata_and_risk_are_strict_and_secret_free(tmp_path):
    tools = {tool.__name__: tool for tool in make_wechat_tools(_secrets(tmp_path))}
    definitions = {tool.name: tool for tool in TOOLS_BY_CONNECTOR["wechat_official"]}

    assert set(tools) == {"prepare_wechat_draft", "create_wechat_draft"}
    assert set(definitions) == set(tools)
    assert all(tool.kind == "write" and tool.default_enabled for tool in definitions.values())
    assert all(tool.target_arg is None for tool in definitions.values())
    assert target_arg_for("create_wechat_draft") is None
    assert classify("prepare_wechat_draft") is RiskClass.WRITE_LOCAL
    assert classify("create_wechat_draft") is RiskClass.EXTERNAL

    create_schema = tools["create_wechat_draft"].__coworker_schema__
    parameters = create_schema["function"]["parameters"]
    assert parameters["additionalProperties"] is False
    assert set(parameters["properties"]) == {
        "article_path",
        "preview_hash",
        "theme",
        "color",
        "cover_path",
    }
    assert parameters["required"] == ["article_path", "preview_hash", "theme"]
    encoded = json.dumps(create_schema).lower()
    assert not any(
        secret_name in encoded
        for secret_name in (
            "app_id",
            "appid",
            "app_secret",
            "credential",
            "access_token",
            "need_open_comment",
            "only_fans_can_comment",
        )
    )
    assert tools["prepare_wechat_draft"].__aisuite_tool_metadata__.requires_approval
    assert tools["create_wechat_draft"].__aisuite_tool_metadata__.requires_approval


def test_registry_extracts_display_and_once_only_attributes_with_explicit_overrides(tmp_path):
    create_tool = make_wechat_tools(_secrets(tmp_path))[1]
    registry = ToolRegistry()

    registry.register_all([create_tool])
    extracted = registry.get("create_wechat_draft")
    assert extracted is not None
    assert extracted.approval_arguments is create_tool.__coworker_approval_arguments__
    assert extracted.approval_once_only is True

    registry.register(
        create_tool,
        approval_arguments=None,
        approval_once_only=False,
    )
    overridden = registry.get("create_wechat_draft")
    assert overridden is not None
    assert overridden.approval_arguments is None
    assert overridden.approval_once_only is False


def test_create_rejects_persistent_approval_without_execution(tmp_path):
    secrets = _secrets(tmp_path)
    clients = []
    prepare_tool, create_tool = make_wechat_tools(
        secrets,
        roots=[tmp_path],
        preview_factory=lambda *args, **kwargs: _preview(tmp_path),
        client_factory=lambda store: clients.append(store),
    )
    prepare_tool("article.md", "default", cover_path="images/cover.png")
    registry = ToolRegistry()
    registry.register_all([create_tool])

    class Provider(ProviderClient):
        def __init__(self):
            self.turns = [
                AssistantTurn(
                    tool_calls=[
                        ToolCall(
                            id="wechat-call",
                            name="create_wechat_draft",
                            arguments=_arguments(),
                        )
                    ],
                    finish_reason="tool_calls",
                ),
                AssistantTurn(text="未创建草稿", finish_reason="stop"),
            ]

        def complete(self, *, model, messages, tools=None, **settings):
            return self.turns.pop(0)

        def capabilities(self, model):
            return ModelCapabilities()

    async def approve_persistently(_request):
        return ApprovalOutcome.ALWAYS_TOOL

    engine = TurnEngine(
        provider=Provider(),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="test-model",
        approver=approve_persistently,
    )

    async def collect():
        return [event async for event in engine.run("创建草稿")]

    events = asyncio.run(collect())
    finished = next(
        event
        for event in events
        if event.type is EventType.TOOL_FINISHED
        and event.data["name"] == "create_wechat_draft"
    )
    assert finished.data == {
        "name": "create_wechat_draft",
        "status": "denied",
        "reason": "persistent approval is not allowed for this tool",
    }
    assert clients == []


def test_prepare_reads_connection_comments_and_dynamic_roots(tmp_path):
    secrets = _secrets(tmp_path)
    roots = [tmp_path]
    calls = []

    def prepare(*args, **kwargs):
        calls.append((args, kwargs))
        return _preview(tmp_path)

    prepare_tool = make_wechat_tools(
        secrets,
        roots=roots,
        preview_factory=prepare,
    )[0]
    first = prepare_tool("article.md", "default")
    added = tmp_path / "added"
    roots.append(added)
    second = prepare_tool("article.md", "default")

    assert first == second == {
        "channel": "微信公众号",
        "title": "安全标题",
        "digest": "安全摘要",
        "cover_path": "images/cover.png",
        "image_count": 1,
        "theme": "default",
        "color": "#07C160",
        "draft_only": True,
        "preview_hash": _HASH,
    }
    assert calls[0][0][4] == (tmp_path,)
    assert calls[1][0][4] == (tmp_path, added)
    assert calls[0][1] == {
        "need_open_comment": True,
        "only_fans_can_comment": True,
    }
    assert _SECRET not in repr(first)


def test_prepare_refuses_disconnected_connector_without_touching_preview(tmp_path):
    secrets = _secrets(tmp_path)
    profile = secrets.get("wechat_official:default")
    profile["enabled"] = False
    secrets.put("wechat_official:default", profile)
    calls = []

    result = make_wechat_tools(
        secrets,
        roots=[tmp_path],
        preview_factory=lambda *args, **kwargs: calls.append((args, kwargs)),
    )[0]("article.md", "default")

    assert result["status"] == "failed"
    assert result["error_kind"] == "not_connected"
    assert result["draft_only"] is True
    assert calls == []


def test_create_approval_display_only_reads_matching_cache(tmp_path, monkeypatch):
    secrets = _secrets(tmp_path)
    prepare_tool, create_tool = make_wechat_tools(
        secrets,
        roots=[tmp_path],
        preview_factory=lambda *args, **kwargs: _preview(tmp_path),
    )
    prepare_tool("article.md", "default", cover_path="images/cover.png")
    registry = ToolRegistry()
    registry.register_all([create_tool])
    callback = registry.get("create_wechat_draft").approval_arguments
    assert callback is not None

    monkeypatch.setattr(
        secrets,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("secret read")),
    )
    display = callback(_arguments())
    fallback = callback(_arguments(preview_hash=_CHANGED_HASH))

    assert display == {
        "channel": "微信公众号",
        "title": "安全标题",
        "digest": "安全摘要",
        "cover_path": "images/cover.png",
        "image_count": 1,
        "theme": "default",
        "color": "#07C160",
        "draft_only": True,
    }
    assert fallback == {
        "channel": "微信公众号",
        "title": "",
        "digest": "",
        "cover_path": "",
        "image_count": 0,
        "theme": "",
        "color": "",
        "draft_only": True,
    }
    assert _HASH not in repr(display)
    assert _SECRET not in repr(display)


@pytest.mark.parametrize(
    "changed",
    [
        {"article_path": "other.md"},
        {"preview_hash": _CHANGED_HASH},
        {"theme": "modern"},
        {"color": "#123456"},
        {"cover_path": "images/other.png"},
    ],
)
def test_create_rejects_missing_or_changed_cache_before_client(tmp_path, changed):
    secrets = _secrets(tmp_path)
    clients = []

    def build_client(_secrets):
        clients.append(object())
        raise AssertionError("client must not be built")

    prepare_tool, create_tool = make_wechat_tools(
        secrets,
        roots=[tmp_path],
        preview_factory=lambda *args, **kwargs: _preview(tmp_path),
        client_factory=build_client,
    )
    no_cache = create_tool(**_arguments())
    prepare_tool("article.md", "default", cover_path="images/cover.png")
    changed_result = create_tool(**_arguments(**changed))

    assert no_cache["error_kind"] == "not_prepared"
    assert changed_result["error_kind"] == "arguments_changed"
    assert clients == []


def test_create_reprepares_with_cached_comments_and_rejects_changed_hash_before_client(tmp_path):
    secrets = _secrets(tmp_path)
    hashes = iter((_HASH, _CHANGED_HASH))
    comments = []
    clients = []

    def prepare(*args, **kwargs):
        comments.append(kwargs)
        return _preview(tmp_path, next(hashes))

    prepare_tool, create_tool = make_wechat_tools(
        secrets,
        roots=[tmp_path],
        preview_factory=prepare,
        client_factory=lambda store: clients.append(store),
    )
    prepare_tool("article.md", "default", cover_path="images/cover.png")
    profile = secrets.get("wechat_official:default")
    profile["need_open_comment"] = False
    profile["only_fans_can_comment"] = False
    secrets.put("wechat_official:default", profile)
    result = create_tool(**_arguments())

    assert result["status"] == "failed"
    assert result["error_kind"] == "preview_changed"
    assert comments == [
        {"need_open_comment": True, "only_fans_can_comment": True},
        {"need_open_comment": True, "only_fans_can_comment": True},
    ]
    assert clients == []


@pytest.mark.parametrize(
    ("profile_changes", "expected_error"),
    [
        ({"enabled": False}, "not_connected"),
        (
            {
                "need_open_comment": False,
                "only_fans_can_comment": False,
            },
            "preview_changed",
        ),
    ],
)
def test_create_rechecks_live_connector_state_before_client(
    tmp_path,
    profile_changes,
    expected_error,
):
    secrets = _secrets(tmp_path)
    clients = []
    prepare_tool, create_tool = make_wechat_tools(
        secrets,
        roots=[tmp_path],
        preview_factory=lambda *args, **kwargs: _preview(tmp_path),
        client_factory=lambda store: clients.append(store),
    )
    prepare_tool("article.md", "default", cover_path="images/cover.png")
    profile = secrets.get("wechat_official:default")
    profile.update(profile_changes)
    secrets.put("wechat_official:default", profile)

    result = create_tool(**_arguments())

    assert result["status"] == "failed"
    assert result["error_kind"] == expected_error
    assert clients == []


@pytest.mark.parametrize(
    ("status", "error_kind"),
    [
        ("success", None),
        ("duplicate", None),
        ("failed", "permission_denied"),
        ("unknown", "transport"),
    ],
)
def test_create_returns_only_safe_four_state_result_and_closes_client(
    tmp_path, status, error_kind
):
    secrets = _secrets(tmp_path)
    sequence = []

    class Client:
        def close(self):
            sequence.append("close")

    def prepare(*args, **kwargs):
        sequence.append("prepare")
        return _preview(tmp_path)

    def client_factory(store):
        assert store is secrets
        sequence.append("client")
        return Client()

    def draft_creator(preview, client, receipt_store):
        sequence.append("draft")
        assert receipt_store.article_directory == tmp_path
        return DraftResult(
            status=status,
            receipt=SimpleNamespace(media_id="secret-media-id"),
            error_kind=error_kind,
            uploaded_assets=(
                "body:images/body.png",
                "cover:images/cover.png",
                "body:/absolute/secret.png",
                f"body:{_SECRET}",
            ),
        )

    prepare_tool, create_tool = make_wechat_tools(
        secrets,
        roots=[tmp_path],
        preview_factory=prepare,
        client_factory=client_factory,
        draft_creator=draft_creator,
    )
    prepare_tool("article.md", "default", cover_path="images/cover.png")
    result = create_tool(**_arguments())

    summary = {
        "status": status,
        "title": "安全标题",
        "error_kind": error_kind,
        "uploaded_asset_count": 2,
        "uploaded_assets": ["body:images/body.png", "cover:images/cover.png"],
        "draft_only": True,
    }
    assert result == {**summary, "_display": {"wechat_draft_result": summary}}
    assert sequence == ["prepare", "prepare", "client", "draft", "close"]
    serialized = json.dumps(result, ensure_ascii=False)
    assert _SECRET not in serialized
    assert _HASH not in serialized
    assert "secret-media-id" not in serialized
    assert str(tmp_path) not in serialized


def test_create_closes_client_when_draft_creator_raises(tmp_path):
    secrets = _secrets(tmp_path)
    closed = []

    class Client:
        def close(self):
            closed.append(True)

    prepare_tool, create_tool = make_wechat_tools(
        secrets,
        roots=[tmp_path],
        preview_factory=lambda *args, **kwargs: _preview(tmp_path),
        client_factory=lambda store: Client(),
        draft_creator=lambda *args: (_ for _ in ()).throw(RuntimeError(_SECRET)),
    )
    prepare_tool("article.md", "default", cover_path="images/cover.png")
    result = create_tool(**_arguments())

    assert result["status"] == "failed"
    assert result["error_kind"] == "local_io"
    assert _SECRET not in repr(result)
    assert closed == [True]
