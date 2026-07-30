"""Managed Slack relay client (Milestone 3) — dual-mode desktop, team-qualified
addressing, per-team reply tokens. Hermetic: an injected fake relay transport
(no live WebSocket) + captured senders (no network)."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from coworker.connectors import relay_client
from coworker.connectors.adapters import make_adapter
from coworker.connectors.base import InteractionEvent, MessageEvent
from coworker.connectors.config import ConnectorSettings, load_settings
from coworker.connectors.relay_client import SlackRelayAdapter
from coworker.connectors.slack_addr import qualify, split
from coworker.connectors.tools import make_send_message_tool
from coworker.secrets import SecretStore
from coworker.server import SessionManager


@pytest.fixture(autouse=True)
def _no_slack_network(monkeypatch):
    """Name/channel resolution is best-effort; unstubbed lookups must fail
    instantly at a dead loopback port, never reach slack.com — a slow real
    answer was blowing the 2s wait_dispatched window intermittently."""
    monkeypatch.setenv("SLACK_API_URL", "http://127.0.0.1:9/")


TEAMS = {
    "T1": {"bot_token": "xoxb-team1", "bot_user_id": "UBOT1"},
    "T2": {"bot_token": "xoxb-team2", "bot_user_id": "UBOT2"},
}


class FakeTransport:
    """Yields queued frames, then either closes (recv→None) or blocks forever."""

    def __init__(self, frames, *, close_after: bool):
        self._q: asyncio.Queue = asyncio.Queue()
        for f in frames:
            self._q.put_nowait(f)
        self._close_after = close_after
        self.opened = False

    async def open(self):
        self.opened = True

    async def recv(self):
        if not self._q.empty():
            return self._q.get_nowait()
        if self._close_after:
            return None
        await asyncio.Event().wait()  # block until cancelled

    async def close(self):
        pass


def _factory(*transports):
    """A transport_factory that hands out the given transports in order."""
    it = iter(transports)

    def make():
        return next(it)

    return make


def _event_frame(team, channel, user, text="hi", ts="1.0"):
    return {
        "provider": "slack",
        "team_id": team,
        "address": f"slack:{team}:{channel}",
        "channel": channel,
        "event_id": f"Ev-{ts}",
        "event": {
            "type": "app_mention",
            "user": user,
            "channel": channel,
            "text": text,
            "ts": ts,
        },
    }


def _adapter(frames, *, close_after=False, **kw) -> SlackRelayAdapter:
    return SlackRelayAdapter(
        "wss://relay.test/ws",
        token_provider=lambda: "jwt-token",
        teams=dict(TEAMS),
        transport_factory=_factory(FakeTransport(frames, close_after=close_after)),
        reconnect_delay=0.0,
        **kw,
    )


def _collect(adapter):
    events: list[MessageEvent] = []
    adapter.set_message_handler(lambda e: events.append(e) or asyncio.sleep(0))
    return events


# --- pure addressing --------------------------------------------------------


def test_slack_addr_roundtrip():
    assert qualify("T1", "C9") == "T1/C9"
    assert qualify(None, "C9") == "C9"
    assert split("T1/C9") == ("T1", "C9")
    assert split("C9") == (None, "C9")


# --- inbound dispatch -------------------------------------------------------


async def test_relay_dispatches_team_qualified_event():
    adapter = _adapter([_event_frame("T1", "C1", "U_ALICE")])
    events: list[MessageEvent] = []

    async def handler(e):
        events.append(e)

    adapter.set_message_handler(handler)
    assert await adapter.connect() is True
    try:
        await adapter.wait_dispatched(1)
    finally:
        await adapter.disconnect()

    assert len(events) == 1
    ev = events[0]
    assert ev.source.chat_id == "T1/C1"
    assert ev.source.target == "slack:T1/C1"  # team-qualified reply handle
    assert ev.source.team_id == "T1"
    assert ev.text == "hi"


async def test_relay_resolves_names_and_mentions(monkeypatch):
    adapter = _adapter([_event_frame("T1", "C1", "U_ALICE", text="<@UBOT1> hey bot")])

    async def fake_get(team_id, method, params):
        assert team_id == "T1"  # resolved with THIS workspace's token
        if method == "users.info":
            names = {
                "U_ALICE": {"profile": {"display_name": "Rohit"}},
                "UBOT1": {"profile": {"display_name": "ocw"}},
            }
            return {"ok": True, "user": names.get(params["user"], {})}
        if method == "conversations.info":
            return {"ok": True, "channel": {"name": "ocw-test"}}
        return None

    monkeypatch.setattr(adapter, "_slack_get", fake_get)
    events: list[MessageEvent] = []

    async def handler(e):
        events.append(e)

    adapter.set_message_handler(handler)
    await adapter.connect()
    try:
        await adapter.wait_dispatched(1)
    finally:
        await adapter.disconnect()

    ev = events[0]
    assert ev.source.user_name == "Rohit"  # not U_ALICE
    assert ev.source.chat_name == "ocw-test"  # not C1
    assert ev.text == "@ocw hey bot"  # <@UBOT1> rewritten


async def test_relay_name_cache_is_per_workspace(monkeypatch):
    """A cached (team, id) must not leak across workspaces."""
    adapter = SlackRelayAdapter("wss://x", lambda: "jwt", teams=dict(TEAMS))
    calls = []

    async def fake_get(team_id, method, params):
        calls.append((team_id, params.get("user")))
        return {
            "ok": True,
            "user": {"profile": {"display_name": f"{team_id}:{params['user']}"}},
        }

    monkeypatch.setattr(adapter, "_slack_get", fake_get)
    # Same uid string in two workspaces resolves independently + caches per team.
    assert await adapter._display_name("T1", "U9") == "T1:U9"
    assert await adapter._display_name("T2", "U9") == "T2:U9"
    assert await adapter._display_name("T1", "U9") == "T1:U9"  # cached, no new call
    assert calls == [("T1", "U9"), ("T2", "U9")]  # T1 second lookup served from cache


async def test_relay_two_workspace_fan_in():
    adapter = _adapter(
        [
            _event_frame("T1", "C1", "U_A", ts="1"),
            _event_frame("T2", "C2", "U_B", ts="2"),
        ]
    )
    events: list[MessageEvent] = []

    async def handler(e):
        events.append(e)

    adapter.set_message_handler(handler)
    await adapter.connect()
    try:
        await adapter.wait_dispatched(2)
    finally:
        await adapter.disconnect()

    chats = {e.source.chat_id for e in events}
    assert chats == {"T1/C1", "T2/C2"}


async def test_relay_ignores_own_bot_echo():
    # event.user == the team's bot user id → dropped by the mapper.
    adapter = _adapter([_event_frame("T1", "C1", "UBOT1")])
    events: list = []
    adapter.set_message_handler(lambda e: events.append(e))
    await adapter.connect()
    await asyncio.sleep(0.05)
    await adapter.disconnect()
    assert events == []


# --- reconnect --------------------------------------------------------------


async def test_relay_watchdog_reconnects():
    t1 = FakeTransport([_event_frame("T1", "C1", "U_A", ts="1")], close_after=True)
    t2 = FakeTransport([_event_frame("T1", "C1", "U_A", ts="2")], close_after=False)
    adapter = SlackRelayAdapter(
        "wss://relay.test/ws",
        token_provider=lambda: "jwt",
        teams=dict(TEAMS),
        transport_factory=_factory(t1, t2),
        reconnect_delay=0.0,
    )
    events: list = []

    async def handler(e):
        events.append(e)

    adapter.set_message_handler(handler)
    await adapter.connect()
    try:
        await adapter.wait_dispatched(2)
    finally:
        await adapter.disconnect()

    assert adapter.reconnects == 1  # dropped socket self-healed
    assert t2.opened is True
    assert len(events) == 2


# --- interactivity / revoked / nudge ---------------------------------------


async def test_relay_interactivity_maps_to_interaction():
    frame = {
        "kind": "interactivity",
        "team_id": "T2",
        "interaction": {
            "user": {"id": "U2", "username": "bob"},
            "channel": {"id": "C7"},
            "message": {"ts": "9.9"},
            "response_url": "https://hooks.slack.com/actions/abc",
            "actions": [{"value": "approve:42"}],
        },
    }
    adapter = _adapter([frame])
    seen: list[InteractionEvent] = []

    async def on_interaction(e):
        seen.append(e)

    adapter.set_interaction_handler(on_interaction)
    await adapter.connect()
    try:
        await adapter.wait_dispatched(1)
    finally:
        await adapter.disconnect()

    assert len(seen) == 1
    assert seen[0].chat_id == "T2/C7" and seen[0].value == "approve:42"
    assert seen[0].user_id == "U2"
    assert seen[0].team_id == "T2"
    assert seen[0].response_url == "https://hooks.slack.com/actions/abc"


async def test_relay_revoked_drops_team():
    adapter = _adapter([{"kind": "revoked", "team_id": "T1"}])
    await adapter.connect()
    try:
        await adapter.wait_dispatched(1)
    finally:
        await adapter.disconnect()
    assert "T1" not in adapter._teams and "T2" in adapter._teams


async def test_relay_nudge_pulls_history():
    fetched = {}

    async def fetcher(team, channel, count):
        fetched.update(team=team, channel=channel, count=count)
        return [
            {"type": "message", "user": "U_A", "text": "missed one", "ts": "1"},
            {"type": "message", "user": "U_A", "text": "missed two", "ts": "2"},
        ]

    adapter = _adapter(
        [{"kind": "missed", "team_id": "T1", "channel": "C1", "count": 2}],
        history_fetcher=fetcher,
    )
    events: list[MessageEvent] = []

    async def handler(e):
        events.append(e)

    adapter.set_message_handler(handler)
    await adapter.connect()
    try:
        await adapter.wait_dispatched(1)
    finally:
        await adapter.disconnect()

    assert fetched == {"team": "T1", "channel": "C1", "count": 2}
    assert [e.text for e in events] == ["missed one", "missed two"]
    assert all(e.source.chat_id == "T1/C1" for e in events)


# --- outbound per-team token ------------------------------------------------


async def test_relay_send_selects_per_team_token(monkeypatch):
    captured = {}

    def fake_send(token, chat_id, text, thread_id=None):
        captured.update(token=token, chat_id=chat_id, text=text)
        from coworker.connectors.base import SendResult

        return SendResult(True, message_id="ts1")

    monkeypatch.setattr(relay_client, "_send_slack", fake_send)
    adapter = SlackRelayAdapter("wss://x", lambda: "jwt", teams=dict(TEAMS))
    res = await adapter.send("T2/C9", "hello")
    assert res.ok
    assert captured["token"] == "xoxb-team2"  # T2's token, not T1's
    assert captured["chat_id"] == "T2/C9"


# --- send_message tool per-team selection -----------------------------------


def test_send_message_tool_per_team_and_default_token():
    secrets = SecretStore()
    secrets.put("slack:team:T1", {"bot_token": "xoxb-team1"})
    secrets.put("slack:default", {"bot_token": "xoxb-manual", "mode": "socket"})

    calls = []

    def fake_slack(token, chat_id, text, thread_id):
        calls.append((token, chat_id))
        from coworker.connectors.base import SendResult

        return SendResult(True, message_id="ts")

    tool = make_send_message_tool(secrets, senders={"slack": fake_slack})
    # Team-qualified → per-team token
    tool("slack:T1/C1", "hi")
    # Bare (manual socket mode) → default token
    tool("slack:Cbare", "hi")

    assert calls[0][0] == "xoxb-team1"
    assert calls[1][0] == "xoxb-manual"


# --- dual-mode adapter selection -------------------------------------------


def test_make_adapter_relay_mode_builds_relay_client():
    adapter = make_adapter(
        "slack",
        {"mode": "relay", "enabled": True},
        secrets=SecretStore(),
        token_provider=lambda: "jwt",
        relay_url="wss://relay.test/ws",
    )
    assert isinstance(adapter, SlackRelayAdapter)


def test_make_adapter_socket_mode_builds_socket_adapter():
    from coworker.connectors.adapters import SlackAdapter

    adapter = make_adapter("slack", {"bot_token": "xoxb", "app_token": "xapp"})
    assert isinstance(adapter, SlackAdapter)


def test_make_adapter_relay_without_endpoint_returns_none():
    # Relay mode configured but no relay_url / sign-in → don't build (falls back).
    adapter = make_adapter(
        "slack", {"mode": "relay", "enabled": True}, secrets=SecretStore()
    )
    assert adapter is None


def test_relay_mode_enables_slack_without_bot_token():
    secrets = SecretStore()
    secrets.put("slack:default", {"mode": "relay", "enabled": True})
    settings = load_settings(secrets)
    assert settings["slack"].enabled is True


def test_make_adapter_loads_per_team_tokens():
    secrets = SecretStore()
    secrets.put("slack:team:T9", {"bot_token": "xoxb-9", "bot_user_id": "U9"})
    adapter = make_adapter(
        "slack",
        {"mode": "relay", "enabled": True},
        secrets=secrets,
        token_provider=lambda: "jwt",
        relay_url="wss://relay/ws",
    )
    assert adapter._bot_token("T9") == "xoxb-9"



@pytest.mark.asyncio
async def test_disabled_relay_disconnects_slack_workspace_locally(
    tmp_path, monkeypatch
):
    from coworker import cloud

    manager = SessionManager(data_dir=tmp_path)
    manager.secrets.put(
        "slack:default",
        {"type": "oauth", "managed": True, "mode": "relay", "enabled": True},
    )
    manager.secrets.put(
        "slack:team:T1",
        {"type": "oauth", "managed": True, "bot_token": "xoxb-1"},
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("disabled Relay must not call Cloud on disconnect")

    async def no_refresh():
        return None

    monkeypatch.setattr(cloud, "slack_disconnect_workspace", forbidden)
    monkeypatch.setattr(manager, "refresh_gateway", no_refresh)

    result = await manager.disconnect_slack_workspace("T1")

    assert result == {"ok": True, "remaining_workspaces": 0}
    assert manager.secrets.get("slack:team:T1") is None


@pytest.mark.asyncio
async def test_disabled_relay_disconnects_github_installation_locally(
    tmp_path, monkeypatch
):
    from coworker import cloud

    manager = SessionManager(data_dir=tmp_path)
    manager.secrets.put(
        "github:default",
        {"type": "oauth", "managed": True, "mode": "relay", "enabled": True},
    )
    manager.secrets.put(
        "github:install:101",
        {
            "type": "oauth",
            "managed": True,
            "installation_id": "101",
            "account_login": "acme",
        },
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("disabled Relay must not call Cloud on disconnect")

    async def no_refresh():
        return None

    monkeypatch.setattr(cloud, "github_disconnect_installation", forbidden)
    monkeypatch.setattr(manager, "refresh_gateway", no_refresh)

    result = await manager.disconnect_github_installation("101")

    assert result == {"ok": True, "remaining_installs": 0}
    assert manager.secrets.get("github:install:101") is None


class _GatewayTestAdapter:
    def __init__(self, platform: str):
        self.platform = platform

    def set_message_handler(self, _handler):
        pass

    def set_interaction_handler(self, _handler):
        pass

    async def connect(self):
        return True

    async def disconnect(self):
        pass


@pytest.mark.asyncio
async def test_wenshu_gateway_skips_hidden_manual_profiles_before_read_or_adapter(
    tmp_path, monkeypatch
):
    manager = SessionManager(data_dir=tmp_path)
    manager.secrets.put(
        "slack:default",
        {
            "bot_token": "xoxb-preserved",
            "app_token": "xapp-preserved",
            "enabled": True,
        },
    )
    manager.secrets.put(
        "telegram:default", {"bot_token": "telegram-preserved", "enabled": True}
    )
    original_get = manager.secrets.get
    profile_reads: list[str] = []
    adapter_platforms: list[str] = []

    def recording_get(profile):
        profile_reads.append(profile)
        return original_get(profile)

    def recording_adapter(platform, _profile, **_kwargs):
        adapter_platforms.append(platform)
        return _GatewayTestAdapter(platform)

    monkeypatch.setattr(manager.secrets, "get", recording_get)
    monkeypatch.setattr("coworker.server.manager.make_adapter", recording_adapter)
    try:
        started = await manager.start_gateway()
    finally:
        await manager.aclose()

    assert started == []
    assert adapter_platforms == []
    assert "slack:default" not in profile_reads
    assert "telegram:default" not in profile_reads
    assert original_get("slack:default")["bot_token"] == "xoxb-preserved"
    assert original_get("telegram:default")["bot_token"] == "telegram-preserved"


@pytest.mark.asyncio
async def test_gateway_skips_relay_profiles_when_feature_is_disabled(
    tmp_path, monkeypatch, permissive_product
):
    product = replace(
        permissive_product,
        features={**permissive_product.features, "relay": False},
    )
    manager = SessionManager(data_dir=tmp_path, product=product)
    manager.secrets.put("slack:default", {"mode": "relay", "enabled": True})
    manager.secrets.put(
        "telegram:default", {"bot_token": "manual-token", "enabled": True}
    )
    relay_hubs = []
    adapter_profiles = {}

    class RecordingRelayHub:
        def __init__(self, *args):
            relay_hubs.append(args)

    def recording_adapter(platform, profile, **_kwargs):
        adapter_profiles[platform] = profile
        return _GatewayTestAdapter(platform)

    monkeypatch.setattr(relay_client, "RelayHub", RecordingRelayHub)
    monkeypatch.setattr("coworker.server.manager.make_adapter", recording_adapter)
    try:
        started = await manager.start_gateway()
    finally:
        await manager.aclose()

    assert relay_hubs == []
    assert "slack" not in adapter_profiles
    assert adapter_profiles["telegram"]["bot_token"] == "manual-token"
    assert started == ["telegram"]


@pytest.mark.asyncio
async def test_gateway_starts_relay_for_an_enabled_product(
    tmp_path, monkeypatch, permissive_product
):
    product = replace(
        permissive_product,
        id="openworker",
        features={**permissive_product.features, "relay": True},
    )
    manager = SessionManager(data_dir=tmp_path, product=product)
    manager.secrets.put("slack:default", {"mode": "relay", "enabled": True})
    relay_hubs = []
    adapter_profiles = {}

    class RecordingRelayHub:
        def __init__(self, *args):
            relay_hubs.append(args)

    def recording_adapter(platform, profile, **_kwargs):
        adapter_profiles[platform] = profile
        return _GatewayTestAdapter(platform)

    monkeypatch.setattr(relay_client, "RelayHub", RecordingRelayHub)
    monkeypatch.setattr("coworker.server.manager.make_adapter", recording_adapter)
    try:
        started = await manager.start_gateway()
    finally:
        await manager.aclose()

    assert len(relay_hubs) == 1
    assert adapter_profiles["slack"]["mode"] == "relay"
    assert started == ["slack"]
