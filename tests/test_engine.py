"""P2 gate tests — turn engine + event bus (scripted provider, no network)."""

from __future__ import annotations

import asyncio
import threading
import time

import aisuite as ai
import pytest
from coworker.engine import ApprovalOutcome, PermissionRequest, TurnEngine
from coworker.events import EventType
from coworker.permissions import Mode, PermissionEngine
from coworker.providers import (
    AssistantTurn,
    ModelCapabilities,
    ProviderClient,
    StreamChunk,
    ToolCall,
)
from coworker.tools import ToolRegistry


def _text_turn(text):
    return AssistantTurn(text=text, finish_reason="stop")


def _tool_turn(name, args, call_id="call_1"):
    return AssistantTurn(
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        finish_reason="tool_calls",
    )


class ScriptedProvider(ProviderClient):
    """Returns queued AssistantTurns; streams via the base default (one final chunk)."""

    def __init__(self, turns, *, loop=False):
        self._turns = list(turns)
        self._loop = loop
        self.calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        return self._turns[0] if self._loop else self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


class StubImageProvider:
    async def generate(self, request):
        raise AssertionError(f"image provider must not run during registration: {request}")


def _engine(tmp_path, turns, *, approver=None, loop=False, max_iterations=12):
    provider = ScriptedProvider(turns, loop=loop)
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    permissions = PermissionEngine(workspace_root=tmp_path)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model="gpt-5.5",
        approver=approver,
        max_iterations=max_iterations,
    )
    return engine, provider


def _collect(engine, user_input):
    async def _run():
        return [ev async for ev in engine.run(user_input)]

    return asyncio.run(_run())


def _types(events):
    return [ev.type for ev in events]


# -- tests ----------------------------------------------------------------------


def test_no_tool_turn(tmp_path):
    engine, _ = _engine(tmp_path, [_text_turn("all done")])
    events = _collect(engine, "hi")
    assert _types(events) == [
        EventType.TURN_START,
        EventType.ASSISTANT_MESSAGE,
        EventType.TURN_END,
    ]
    assert events[1].data["text"] == "all done"
    assert events[-1].data["status"] == "completed"


def test_tool_turn_order_and_execution(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    engine, _ = _engine(
        tmp_path,
        [_tool_turn("read_file", {"path": "a.txt"}), _text_turn("it says hello")],
    )
    events = _collect(engine, "read a.txt")
    assert EventType.PERMISSION_REQUIRED not in _types(events)
    assert _types(events) == [
        EventType.TURN_START,
        EventType.ASSISTANT_MESSAGE,
        EventType.TOOL_PROPOSED,
        EventType.TOOL_STARTED,
        EventType.TOOL_FINISHED,
        EventType.ITERATION_END,
        EventType.ASSISTANT_MESSAGE,
        EventType.TURN_END,
    ]
    finished = next(e for e in events if e.type == EventType.TOOL_FINISHED)
    assert finished.data["status"] == "ok"
    assert any(
        m.get("role") == "tool" and "hello" in m["content"] for m in engine.messages
    )


def test_async_tool_is_awaited_before_recording_result(tmp_path):
    async def async_echo(value: str) -> dict[str, str]:
        await asyncio.sleep(0)
        return {"echo": value}

    engine, _ = _engine(
        tmp_path,
        [_tool_turn("async_echo", {"value": "awaited"}), _text_turn("done")],
    )
    engine.registry.register(
        async_echo,
        metadata=ai.ToolMetadata(
            name="async_echo",
            category="test",
            risk_level="low",
            capabilities=["test"],
            requires_approval=False,
        ),
    )

    events = _collect(engine, "run async tool")

    finished = next(e for e in events if e.type == EventType.TOOL_FINISHED)
    assert finished.data["status"] == "ok"
    assert any(
        message.get("role") == "tool" and "awaited" in message["content"]
        for message in engine.messages
    )
    assert all("coroutine object" not in message["content"] for message in engine.messages)


def test_write_requires_approval_then_approved(tmp_path):
    requests: list[PermissionRequest] = []

    async def approve_once(request: PermissionRequest):
        requests.append(request)
        return ApprovalOutcome.ONCE

    arguments = {"path": "new.py", "content": "print(1)\n"}
    engine, _ = _engine(
        tmp_path,
        [
            _tool_turn("write_file", arguments),
            _text_turn("wrote new.py"),
        ],
        approver=approve_once,
    )
    events = _collect(engine, "create new.py")
    permission = next(
        event for event in events if event.type is EventType.PERMISSION_REQUIRED
    )
    assert permission.data["arguments"] == arguments
    assert requests[0].arguments == arguments
    assert requests[0].display_arguments is None
    assert (tmp_path / "new.py").read_text() == "print(1)\n"


def test_approval_arguments_are_display_only(tmp_path):
    original_arguments = {
        "article_path": "article.md",
        "reviewed_hash": "a" * 64,
        "cover_request": {"prompt": "封面"},
        "illustration_plan": [
            {
                "heading": "第一节",
                "prompt": "配图",
                "output_path": "section.png",
            }
        ],
    }
    display_fields = {
        "article_title": "文枢审批契约",
        "provider": "OpenAI",
        "model": "gpt-image-2",
        "total_images": 2,
    }
    executed: list[dict[str, object]] = []
    summarized: list[dict[str, object]] = []
    requests: list[PermissionRequest] = []

    def generate_article_assets(
        article_path: str,
        reviewed_hash: str,
        cover_request: dict,
        illustration_plan: list,
    ) -> dict[str, bool]:
        executed.append(
            {
                "article_path": article_path,
                "reviewed_hash": reviewed_hash,
                "cover_request": cover_request,
                "illustration_plan": illustration_plan,
            }
        )
        return {"ok": True}

    def approval_arguments(arguments):
        summarized.append(dict(arguments))
        return display_fields

    async def approve_once(request: PermissionRequest):
        requests.append(request)
        return ApprovalOutcome.ONCE

    registry = ToolRegistry()
    spec = registry.register(
        generate_article_assets,
        metadata=ai.ToolMetadata(
            name="generate_article_assets",
            category="content-generation",
            risk_level="medium",
            capabilities=["article-image-generation"],
            requires_approval=True,
        ),
        approval_arguments=approval_arguments,
    )
    engine = TurnEngine(
        provider=ScriptedProvider(
            [
                _tool_turn("generate_article_assets", original_arguments),
                _text_turn("done"),
            ]
        ),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        approver=approve_once,
    )

    events = _collect(engine, "generate article images")

    expected_display = display_fields
    permission = next(
        event for event in events if event.type is EventType.PERMISSION_REQUIRED
    )
    assert permission.data["arguments"] == expected_display
    assert summarized == [original_arguments]
    assert requests[0].arguments == original_arguments
    assert requests[0].display_arguments == expected_display
    assert "reviewed_hash" not in requests[0].display_arguments
    assert "cover_request" not in requests[0].display_arguments
    assert "illustration_plan" not in requests[0].display_arguments
    assert executed == [original_arguments]
    assert set(spec.schema["function"]["parameters"]["properties"]) == set(
        original_arguments
    )


def test_one_shot_tool_rejects_persistent_approval_scope(tmp_path):
    executed: list[str] = []
    requests: list[PermissionRequest] = []

    def paid_tool(value: str) -> str:
        executed.append(value)
        return value

    async def approve_persistently(request: PermissionRequest):
        requests.append(request)
        return ApprovalOutcome.ALWAYS_TOOL

    registry = ToolRegistry()
    registry.register(
        paid_tool,
        metadata=ai.ToolMetadata(
            name="paid_tool",
            category="content-generation",
            risk_level="medium",
            capabilities=["paid-operation"],
            requires_approval=True,
        ),
        approval_once_only=True,
    )
    engine = TurnEngine(
        provider=ScriptedProvider(
            [_tool_turn("paid_tool", {"value": "run"}), _text_turn("done")]
        ),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        approver=approve_persistently,
    )

    events = _collect(engine, "run paid tool")

    permission = next(
        event for event in events if event.type is EventType.PERMISSION_REQUIRED
    )
    assert permission.data["approval_once_only"] is True
    assert requests[0].approval_once_only is True

    finished = next(
        event for event in events if event.type is EventType.TOOL_FINISHED
    )
    assert finished.data["status"] == "denied"
    assert finished.data["reason"] == "persistent approval is not allowed for this tool"
    assert executed == []
    assert "paid_tool" not in engine.permissions.session_allow_tools


def test_one_shot_tool_requires_approval_even_in_auto_mode(tmp_path):
    executed: list[str] = []
    requests: list[PermissionRequest] = []

    def paid_tool(value: str) -> str:
        executed.append(value)
        return value

    async def approve_once(request: PermissionRequest):
        requests.append(request)
        return ApprovalOutcome.ONCE

    registry = ToolRegistry()
    registry.register(
        paid_tool,
        metadata=ai.ToolMetadata(
            name="paid_tool",
            category="content-generation",
            risk_level="medium",
            capabilities=["paid-operation"],
            requires_approval=True,
        ),
        approval_once_only=True,
    )
    engine = TurnEngine(
        provider=ScriptedProvider(
            [_tool_turn("paid_tool", {"value": "run"}), _text_turn("done")]
        ),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO),
        model="gpt-5.5",
        approver=approve_once,
    )

    events = _collect(engine, "run paid tool")

    permission = next(
        event for event in events if event.type is EventType.PERMISSION_REQUIRED
    )
    assert permission.data["reason"] == "requires one-time approval"
    assert permission.data["approval_once_only"] is True
    assert requests[0].approval_once_only is True
    assert executed == ["run"]


def test_tool_result_display_sidecar_is_not_truncated_or_sent_to_provider(tmp_path):
    display = {
        "wechat_draft_result": {
            "status": "unknown",
            "title": "文枢内容流水线",
            "error_kind": "transport",
            "uploaded_asset_count": 3,
            "draft_only": True,
        }
    }

    def draft_result():
        return {
            "status": "unknown",
            "payload": "x" * 600,
            "_display": display,
        }

    registry = ToolRegistry()
    registry.register(
        draft_result,
        metadata=ai.ToolMetadata(
            name="draft_result",
            category="connector",
            risk_level="low",
            capabilities=["test"],
            requires_approval=False,
        ),
    )
    engine = TurnEngine(
        provider=ScriptedProvider(
            [_tool_turn("draft_result", {}), _text_turn("done")]
        ),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )

    events = _collect(engine, "run")

    finished = next(
        event for event in events if event.type is EventType.TOOL_FINISHED
    )
    assert finished.data["display"] == display
    assert len(finished.data["result_preview"]) <= 300
    tool_message = next(message for message in engine.messages if message["role"] == "tool")
    assert tool_message["_display"] == display
    assert "_display" not in tool_message["content"]


def test_approval_arguments_failure_falls_back_without_skipping_approval(tmp_path):
    arguments = {"value": "original"}
    executed: list[str] = []
    requests: list[PermissionRequest] = []

    def consequential_tool(value: str) -> str:
        executed.append(value)
        return value

    def broken_summary(_arguments):
        raise RuntimeError("summary unavailable")

    async def approve_once(request: PermissionRequest):
        requests.append(request)
        return ApprovalOutcome.ONCE

    registry = ToolRegistry()
    registry.register(
        consequential_tool,
        metadata=ai.ToolMetadata(
            name="consequential_tool",
            category="test",
            risk_level="medium",
            capabilities=["test"],
            requires_approval=True,
        ),
        approval_arguments=broken_summary,
    )
    engine = TurnEngine(
        provider=ScriptedProvider(
            [_tool_turn("consequential_tool", arguments), _text_turn("done")]
        ),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        approver=approve_once,
    )

    events = _collect(engine, "run it")

    permission = next(
        event for event in events if event.type is EventType.PERMISSION_REQUIRED
    )
    assert permission.data["arguments"] == arguments
    assert requests[0].display_arguments is None
    assert executed == ["original"]


def test_denied_tool_yields_error_and_continues(tmp_path):
    async def deny(_req: PermissionRequest):
        return ApprovalOutcome.DENY

    engine, _ = _engine(
        tmp_path,
        [
            _tool_turn("write_file", {"path": "new.py", "content": "x"}),
            _text_turn("ok, skipped it"),
        ],
        approver=deny,
    )
    events = _collect(engine, "create new.py")
    assert not (tmp_path / "new.py").exists()
    finished = next(e for e in events if e.type == EventType.TOOL_FINISHED)
    assert finished.data["status"] == "denied"
    assert _types(events)[-1] == EventType.TURN_END
    assert any(
        m.get("role") == "tool" and "not executed" in m["content"]
        for m in engine.messages
    )


def test_max_iterations_rail(tmp_path):
    engine, provider = _engine(
        tmp_path, [_tool_turn("list_files", {})], loop=True, max_iterations=3
    )
    events = _collect(engine, "loop forever")
    end = events[-1]
    assert end.type == EventType.TURN_END
    assert end.data["status"] == "max_iterations_exceeded"
    assert provider.calls == 3


def test_interrupt_between_iterations(tmp_path):
    engine_holder = {}

    async def approve_and_interrupt(_req: PermissionRequest):
        engine_holder["engine"].request_interrupt()
        return ApprovalOutcome.ONCE

    engine, provider = _engine(
        tmp_path,
        [
            _tool_turn("write_file", {"path": "x.py", "content": "x"}),
            _text_turn("should not be reached"),
        ],
        approver=approve_and_interrupt,
    )
    engine_holder["engine"] = engine
    events = _collect(engine, "do a thing")
    assert events[-1].type == EventType.INTERRUPTED
    assert provider.calls == 1


def test_steering_injects_next_turn(tmp_path):
    engine, provider = _engine(tmp_path, [_text_turn("first"), _text_turn("second")])
    engine.queue_steering("actually, also do this")
    events = _collect(engine, "do the first thing")
    assert provider.calls == 2
    assert any(
        m.get("role") == "user" and m["content"] == "actually, also do this"
        for m in engine.messages
    )
    assert events[-1].data["status"] == "completed"


# -- parallel tool execution ------------------------------------------------------


def _multi_tool_turn(calls):
    return AssistantTurn(
        tool_calls=[
            ToolCall(id=f"call_{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(calls)
        ],
        finish_reason="tool_calls",
    )


def _bare_engine(tmp_path, turns):
    provider = ScriptedProvider(turns)
    registry = ToolRegistry()
    permissions = PermissionEngine(workspace_root=tmp_path)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model="gpt-5.5",
    )
    return engine, registry


def test_low_risk_tool_calls_run_concurrently(tmp_path):
    # Both tools block on a 2-party barrier: the turn only completes if the engine
    # really runs them at the same time (sequential execution would trip the timeout
    # and surface as an error result).
    barrier = threading.Barrier(2, timeout=5)
    low = ai.ToolMetadata(category="search", risk_level="low", requires_approval=False)

    def side_a():
        """Wait for side_b."""
        barrier.wait()
        return {"side": "a"}

    def side_b():
        """Wait for side_a."""
        barrier.wait()
        return {"side": "b"}

    engine, registry = _bare_engine(
        tmp_path,
        [_multi_tool_turn([("side_a", {}), ("side_b", {})]), _text_turn("done")],
    )
    registry.register(side_a, metadata=low)
    registry.register(side_b, metadata=low)

    events = _collect(engine, "go")
    finished = [e for e in events if e.type == EventType.TOOL_FINISHED]
    assert len(finished) == 2
    assert all(e.data["status"] == "ok" for e in finished)
    # a tool result message exists for every call id
    tool_ids = {
        m.get("tool_call_id") for m in engine.messages if m.get("role") == "tool"
    }
    assert tool_ids == {"call_0", "call_1"}


def test_non_low_risk_tool_calls_stay_sequential(tmp_path):
    order = []
    medium = ai.ToolMetadata(
        category="filesystem", risk_level="medium", requires_approval=False
    )

    def first():
        """Record start/end with a delay."""
        order.append("first-start")
        time.sleep(0.2)
        order.append("first-end")
        return "ok"

    def second():
        """Record start/end."""
        order.append("second-start")
        order.append("second-end")
        return "ok"

    engine, registry = _bare_engine(
        tmp_path,
        [_multi_tool_turn([("first", {}), ("second", {})]), _text_turn("done")],
    )
    registry.register(first, metadata=medium)
    registry.register(second, metadata=medium)

    _collect(engine, "go")
    assert order == ["first-start", "first-end", "second-start", "second-end"]


class StreamingProvider(ProviderClient):
    def complete(self, **kwargs):  # pragma: no cover - streamed instead
        raise NotImplementedError

    def capabilities(self, model):
        return ModelCapabilities()

    def stream(self, *, model, messages, tools=None, **settings):
        for piece in ["Hel", "lo, ", "world"]:
            yield StreamChunk(text_delta=piece)
        yield StreamChunk(turn=AssistantTurn(text="Hello, world", finish_reason="stop"))


def test_streaming_emits_deltas(tmp_path):
    registry = ToolRegistry()
    permissions = PermissionEngine(workspace_root=tmp_path)
    engine = TurnEngine(
        provider=StreamingProvider(),
        registry=registry,
        permissions=permissions,
        model="gpt-5.5",
    )
    events = _collect(engine, "say hi")
    deltas = [e.data["text"] for e in events if e.type == EventType.ASSISTANT_DELTA]
    assert deltas == ["Hel", "lo, ", "world"]
    final = next(e for e in events if e.type == EventType.ASSISTANT_MESSAGE)
    assert final.data["text"] == "Hello, world"
    assert events[-1].type == EventType.TURN_END


def _pdf_file_part():
    import base64
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    url = "data:application/pdf;base64," + base64.b64encode(buf.getvalue()).decode()
    return {"type": "file", "file": {"filename": "d.pdf", "file_data": url}}


def test_outbound_adapts_pdf_for_non_pdf_models(tmp_path):
    # ScriptedProvider reports default caps (pdf=False) → the file part must be
    # replaced at send time while the stored history keeps the real document.
    engine, _ = _engine(tmp_path, [_text_turn("ok")])
    engine.messages.append(
        {
            "role": "user",
            "content": [{"type": "text", "text": "read this"}, _pdf_file_part()],
        }
    )
    parts = engine._outbound_messages()[-1]["content"]
    assert all(p["type"] != "file" for p in parts)
    assert "d.pdf" in parts[-1]["text"]
    assert engine.messages[-1]["content"][1]["type"] == "file"  # history untouched


def test_outbound_keeps_pdf_for_native_models(tmp_path):
    class NativeProvider(ScriptedProvider):
        def capabilities(self, model):
            return ModelCapabilities(vision=True, pdf=True)

    engine, _ = _engine(tmp_path, [_text_turn("ok")])
    engine.provider = NativeProvider([_text_turn("ok")])
    message = {
        "role": "user",
        "content": [{"type": "text", "text": "read this"}, _pdf_file_part()],
    }
    engine.messages.append(message)
    assert engine._outbound_messages()[-1]["content"][1]["type"] == "file"


def test_provider_extras_persist_on_message_and_survive_outbound(tmp_path):
    """A turn's provider-private sidecar (`extras`, e.g. Gemini thought signatures) rides
    the persisted assistant message and is NOT stripped by _outbound_messages — the owning
    provider needs it back; foreign providers strip it themselves."""
    turn = AssistantTurn(
        text="ok",
        finish_reason="stop",
        extras={"_gemini": {"text_sig": "c2ln", "call_sigs": []}},
    )
    engine, _ = _engine(tmp_path, [turn])
    _collect(engine, "hi")

    persisted = engine.messages[-1]
    assert persisted["_gemini"] == {"text_sig": "c2ln", "call_sigs": []}
    outbound = engine._outbound_messages()[-1]
    assert outbound["_gemini"] == {"text_sig": "c2ln", "call_sigs": []}
    assert "ts" not in outbound  # display sidecars still stripped


def test_switch_model_appends_notice_only_midsession(tmp_path):
    engine, _ = _engine(tmp_path, [_text_turn("ok")])
    # Fresh session: first bind is silent.
    assert engine.switch_model("zai:glm-5.2") is None
    assert engine.model == "zai:glm-5.2"
    _collect(engine, "hi")
    # Same model: no-op.
    assert engine.switch_model("zai:glm-5.2") is None
    # Real mid-session switch: persisted marker with the matrix label.
    text = engine.switch_model("kimi:kimi-k2.6")
    assert "Kimi K2.6" in text and engine.model == "kimi:kimi-k2.6"
    notice = engine.messages[-1]
    assert notice["role"] == "notice" and notice["kind"] == "model_switch"
    assert all(m.get("role") != "notice" for m in engine._outbound_messages())


def test_switch_model_warns_when_images_meet_text_only_model(tmp_path):
    class NoVisionProvider(ScriptedProvider):
        def capabilities(self, model):
            return ModelCapabilities(vision=False)

    engine, _ = _engine(tmp_path, [_text_turn("ok")])
    engine.provider = NoVisionProvider([_text_turn("ok")])
    engine.messages.append(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
            ],
        }
    )
    text = engine.switch_model("zai:glm-5.2")
    assert "images" in text  # degradation is called out in the marker


def test_outbound_replaces_images_for_non_vision_models(tmp_path):
    class NoVisionProvider(ScriptedProvider):
        def capabilities(self, model):
            return ModelCapabilities(vision=False)

    engine, _ = _engine(tmp_path, [_text_turn("ok")])
    engine.provider = NoVisionProvider([_text_turn("ok")])
    engine.messages.append(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
            ],
        }
    )
    parts = engine._outbound_messages()[-1]["content"]
    assert all(p["type"] != "image_url" for p in parts)
    assert "not viewable" in parts[-1]["text"]
    assert engine.messages[-1]["content"][1]["type"] == "image_url"  # history untouched





def test_native_content_tools_are_scoped_to_wenshu_cowork(tmp_path):
    from coworker.agent import build_engine
    from coworker.agents import code_agent, cowork_agent
    from coworker.secrets import SecretStore

    secrets = SecretStore(tmp_path / "secrets.json")
    cowork = build_engine(
        agent=cowork_agent(),
        workspace=tmp_path,
        provider=ScriptedProvider([]),
        secrets=secrets,
        image_provider=StubImageProvider(),
    )
    code = build_engine(
        agent=code_agent(),
        workspace=tmp_path,
        provider=ScriptedProvider([]),
        secrets=secrets,
        image_provider=StubImageProvider(),
    )
    content_tools = {"prepare_article_review", "generate_article_assets"}
    try:
        assert content_tools <= set(cowork.registry.names())
        assert content_tools.isdisjoint(code.registry.names())
        generation = cowork.registry.get("generate_article_assets")
        assert generation is not None
        assert generation.approval_arguments is not None
        assert generation.approval_once_only is True
    finally:
        cowork.executor.close()
        code.executor.close()


def test_content_approval_summary_uses_safe_secret_store_description(tmp_path):
    from coworker.agent import build_engine
    from coworker.agents import cowork_agent
    from coworker.secrets import SecretStore

    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put(
        "provider:openai",
        {
            "api_key": "sk-never-display",
            "base_url": "https://private-proxy.example.test/v1",
            "image_model": "gpt-image-2",
        },
    )
    article = tmp_path / "article.md"
    article.write_text("---\ntitle: 审批摘要\n---\n正文\n", encoding="utf-8")
    built: list[bool] = []

    def provider_factory():
        built.append(True)
        return StubImageProvider()

    engine = build_engine(
        agent=cowork_agent(),
        workspace=tmp_path,
        provider=ScriptedProvider([]),
        secrets=secrets,
        image_provider=provider_factory,
    )
    try:
        spec = engine.registry.get("generate_article_assets")
        assert spec is not None and spec.approval_arguments is not None
        arguments = {
            "article_path": str(article),
            "reviewed_hash": "a" * 64,
            "cover_request": {"prompt": "封面"},
            "illustration_plan": [],
        }

        summary = spec.approval_arguments(arguments)

        assert summary == {
            "article_title": "审批摘要",
            "provider": "OpenAI",
            "model": "gpt-image-2",
            "total_images": 1,
        }
        assert built == []
        assert "sk-never-display" not in repr(summary)
        assert "private-proxy.example.test" not in repr(summary)
    finally:
        engine.executor.close()


def test_content_tools_observe_engine_root_revocation(tmp_path):
    from coworker.agent import build_engine
    from coworker.agents import cowork_agent
    from coworker.content.paths import ContentPathError
    from coworker.roots import RootDir
    from coworker.secrets import SecretStore

    primary = tmp_path / "primary"
    extra = tmp_path / "extra"
    primary.mkdir()
    extra.mkdir()
    article = extra / "article.md"
    article.write_text("---\ntitle: 动态目录\n---\n正文\n", encoding="utf-8")
    engine = build_engine(
        agent=cowork_agent(),
        workspace=primary,
        roots=[
            RootDir(path=primary, writable=True),
            RootDir(path=extra, writable=True),
        ],
        provider=ScriptedProvider([]),
        secrets=SecretStore(tmp_path / "secrets.json"),
        image_provider=StubImageProvider(),
    )
    try:
        engine.roots[:] = [
            root for root in engine.roots if root.path != extra.resolve()
        ]
        with pytest.raises(ContentPathError):
            engine.registry.execute(
                "prepare_article_review",
                {"article_path": str(article)},
            )
        generate = engine.registry.get("generate_article_assets")
        assert generate is not None and generate.approval_arguments is not None
        with pytest.raises(ContentPathError):
            generate.approval_arguments(
                {
                    "article_path": str(article),
                    "reviewed_hash": "a" * 64,
                    "cover_request": {"prompt": "封面"},
                    "illustration_plan": [],
                }
            )
    finally:
        engine.executor.close()
