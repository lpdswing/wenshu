from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable

import httpx
import pytest

from coworker.connectors.wechat import (
    WeChatAPIError,
    WeChatClient,
    WeChatCredentialError,
    WeChatCredentials,
    WeChatHTTPError,
    WeChatResponseError,
    WeChatTransportError,
    classify_wechat_error,
)
from coworker.secrets import SecretStore


APP_ID = "wx-test-app"
APP_SECRET = "test-only-sensitive-value"
ACCESS_TOKEN = "test-only-access-token"


def _token_response(token: str = ACCESS_TOKEN, expires_in: int = 7200) -> httpx.Response:
    return httpx.Response(200, json={"access_token": token, "expires_in": expires_in})


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    clock: Callable[[], float] | None = None,
) -> WeChatClient:
    kwargs = {"transport": httpx.MockTransport(handler)}
    if clock is not None:
        kwargs["clock"] = clock
    return WeChatClient(WeChatCredentials(APP_ID, APP_SECRET), **kwargs)


def test_credentials_require_the_fixed_connected_profile(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put(
        "wechat_official:another",
        {"app_id": APP_ID, "app_secret": APP_SECRET},
    )

    with pytest.raises(WeChatCredentialError, match="微信公众号尚未连接"):
        WeChatCredentials.from_store(secrets)

    secrets.put(
        "wechat_official:default",
        {"app_id": "   ", "app_secret": "\n\t"},
    )
    with pytest.raises(WeChatCredentialError, match="微信公众号尚未连接"):
        WeChatCredentials.from_store(secrets)


def test_credentials_are_trimmed_and_repr_is_redacted(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put(
        "wechat_official:default",
        {"app_id": f"  {APP_ID}  ", "app_secret": f"\n{APP_SECRET}\t"},
    )

    credentials = WeChatCredentials.from_store(secrets)

    assert credentials.app_id == APP_ID
    assert credentials.app_secret == APP_SECRET
    assert APP_ID not in repr(credentials)
    assert APP_SECRET not in repr(credentials)


def test_credentials_reject_non_string_fields(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put(
        "wechat_official:default",
        {"app_id": 123, "app_secret": [APP_SECRET]},
    )

    with pytest.raises(WeChatCredentialError, match="微信公众号尚未连接"):
        WeChatCredentials.from_store(secrets)


def test_token_is_cached_and_request_uses_the_documented_get_parameters():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _token_response()

    client = _client(handler)
    try:
        assert client.get_access_token() == ACCESS_TOKEN
        assert client.get_access_token() == ACCESS_TOKEN
    finally:
        client.close()

    assert len(calls) == 1
    request = calls[0]
    assert request.method == "GET"
    assert request.url.scheme == "https"
    assert request.url.host == "api.weixin.qq.com"
    assert request.url.path == "/cgi-bin/token"
    assert dict(request.url.params) == {
        "grant_type": "client_credential",
        "appid": APP_ID,
        "secret": APP_SECRET,
    }


def test_concurrent_token_requests_share_one_refresh():
    workers = 6
    start = threading.Barrier(workers)
    call_lock = threading.Lock()
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        with call_lock:
            calls += 1
        time.sleep(0.05)
        return _token_response()

    def get_token(_index: int) -> str:
        start.wait()
        return client.get_access_token()

    client = _client(handler)
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            tokens = list(executor.map(get_token, range(workers)))
    finally:
        client.close()

    assert tokens == [ACCESS_TOKEN] * workers
    assert calls == 1


def test_httpx_request_logs_redact_wechat_queries(caplog):
    caplog.set_level("INFO", logger="httpx")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cgi-bin/token":
            return _token_response()
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    try:
        assert client.request_json("GET", "/cgi-bin/material/get_materialcount") == {
            "ok": True
        }
    finally:
        client.close()

    assert "HTTP Request" in caplog.text
    assert "?[redacted]" in caplog.text
    for sensitive in (APP_ID, APP_SECRET, ACCESS_TOKEN):
        assert sensitive not in caplog.text


def test_token_refreshes_at_the_120_second_boundary():
    now = [1_000.0]
    issued = iter(("first-token", "second-token"))
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _token_response(next(issued), expires_in=300)

    client = _client(handler, clock=lambda: now[0])
    try:
        assert client.get_access_token() == "first-token"
        now[0] = 1_179.999
        assert client.get_access_token() == "first-token"
        now[0] = 1_180.0
        assert client.get_access_token() == "second-token"
    finally:
        client.close()

    assert calls == 2


def test_access_token_is_never_persisted(tmp_path):
    secrets_path = tmp_path / "secrets.json"
    secrets = SecretStore(secrets_path)
    secrets.put(
        "wechat_official:default",
        {"app_id": APP_ID, "app_secret": APP_SECRET},
    )
    client = WeChatClient.from_store(
        secrets,
        transport=httpx.MockTransport(lambda _request: _token_response()),
    )
    try:
        assert client.get_access_token() == ACCESS_TOKEN
    finally:
        client.close()

    assert ACCESS_TOKEN not in secrets_path.read_text(encoding="utf-8")


def test_request_json_normalizes_path_and_adds_the_cached_token():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/cgi-bin/token":
            return _token_response()
        return httpx.Response(200, json={"media_id": "draft-id"})

    client = _client(handler)
    try:
        result = client.request_json(
            "POST",
            "draft/add",
            params={"lang": "zh_CN", "access_token": "caller-value"},
            json={"articles": []},
        )
    finally:
        client.close()

    assert result == {"media_id": "draft-id"}
    assert len(calls) == 2
    request = calls[1]
    assert request.url.path == "/cgi-bin/draft/add"
    assert request.url.params["lang"] == "zh_CN"
    assert request.url.params["access_token"] == ACCESS_TOKEN
    assert json.loads(request.content) == {"articles": []}


def test_request_json_forwards_multipart_files():
    upload_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal upload_request
        if request.url.path == "/cgi-bin/token":
            return _token_response()
        upload_request = request
        return httpx.Response(200, json={"media_id": "image-id"})

    client = _client(handler)
    try:
        result = client.request_json(
            "POST",
            "/cgi-bin/material/add_material",
            params={"type": "image"},
            files={"media": ("image.png", b"image-bytes", "image/png")},
        )
    finally:
        client.close()

    assert result == {"media_id": "image-id"}
    assert upload_request is not None
    assert upload_request.url.params["access_token"] == ACCESS_TOKEN
    assert upload_request.url.params["type"] == "image"
    assert upload_request.headers["content-type"].startswith("multipart/form-data;")
    assert b"image-bytes" in upload_request.content


def test_request_json_rejects_absolute_urls_before_fetching_a_token():
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _token_response()

    client = _client(handler)
    try:
        with pytest.raises(ValueError, match="relative WeChat API path"):
            client.request_json("GET", "https://example.test/cgi-bin/draft/add")
    finally:
        client.close()

    assert calls == 0


@pytest.mark.parametrize(
    ("errcode", "kind"),
    [
        (40013, "invalid_credentials"),
        (40125, "invalid_credentials"),
        (40164, "ip_allowlist"),
        (48001, "permission_denied"),
        (45009, "rate_limited"),
    ],
)
def test_error_codes_are_classified_without_vendor_message(errcode, kind):
    raw_message = "vendor message secret=must-not-survive"

    error = classify_wechat_error(errcode, raw_message)

    assert error.kind == kind
    assert error.errcode == errcode
    assert "must-not-survive" not in error.errmsg
    assert raw_message not in str(error)
    assert raw_message not in repr(error)


def test_unknown_error_is_conservatively_redacted_and_truncated():
    raw_token = "unknown-token-value-123456789"
    raw_secret = "unknown-secret-value-123456789"
    raw_message = (
        "failure at https://api.example.test/path?access_token="
        f"{raw_token}&secret={raw_secret} access_token={raw_token} "
        + "detail " * 100
    )

    error = classify_wechat_error(99999, raw_message)

    assert error.errcode == 99999
    assert error.kind == "unknown"
    assert raw_token not in error.errmsg
    assert raw_secret not in error.errmsg
    assert "?access_token" not in error.errmsg
    assert len(error.errmsg) <= 160


def test_api_error_is_raised_for_a_200_errcode_without_retry(caplog):
    raw_token = "response-token-value-123456789"
    raw_secret = "response-secret-value-123456789"
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.url.path == "/cgi-bin/token":
            return _token_response()
        return httpx.Response(
            200,
            json={
                "errcode": 99999,
                "errmsg": (
                    "failed https://example.test/callback?access_token="
                    f"{raw_token}&secret={raw_secret}"
                ),
            },
        )

    client = _client(handler)
    try:
        with pytest.raises(WeChatAPIError) as caught:
            client.request_json("POST", "/cgi-bin/draft/add", json={"articles": []})
    finally:
        client.close()

    assert calls == 2
    assert caught.value.errcode == 99999
    assert caught.value.kind == "unknown"
    observable = f"{caught.value!s} {caught.value!r} {caplog.text}"
    for sensitive in (APP_ID, APP_SECRET, ACCESS_TOKEN, raw_token, raw_secret):
        assert sensitive not in observable


def test_timeout_is_wrapped_without_request_url_or_credentials(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout with request URL", request=request)

    client = _client(handler)
    try:
        with pytest.raises(WeChatTransportError) as caught:
            client.get_access_token()
    finally:
        client.close()

    observable = f"{caught.value!s} {caught.value!r} {caplog.text}"
    assert "api.weixin.qq.com" not in observable
    assert APP_ID not in observable
    assert APP_SECRET not in observable
    assert caught.value.__context__ is None


def test_transport_error_during_api_request_does_not_expose_token(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cgi-bin/token":
            return _token_response()
        raise httpx.ConnectError("connect failed", request=request)

    client = _client(handler)
    try:
        with pytest.raises(WeChatTransportError) as caught:
            client.request_json("GET", "/cgi-bin/material/get_materialcount")
    finally:
        client.close()

    observable = f"{caught.value!s} {caught.value!r} {caplog.text}"
    assert "api.weixin.qq.com" not in observable
    assert ACCESS_TOKEN not in observable
    assert APP_SECRET not in observable
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (httpx.Response(502, text="gateway body with secret=private"), WeChatHTTPError),
        (httpx.Response(200, text="not-json access_token=private"), WeChatResponseError),
        (httpx.Response(200, json=["not", "an", "object"]), WeChatResponseError),
    ],
)
def test_invalid_http_responses_do_not_echo_response_content(response, error_type):
    def handler(_request: httpx.Request) -> httpx.Response:
        return response

    client = _client(handler)
    try:
        with pytest.raises(error_type) as caught:
            client.get_access_token()
    finally:
        client.close()

    observable = f"{caught.value!s} {caught.value!r}"
    assert "private" not in observable
    assert "not-json" not in observable
    assert caught.value.__context__ is None


def test_token_payload_requires_a_nonempty_token_and_positive_expiry():
    responses = iter(
        (
            httpx.Response(200, json={"access_token": "", "expires_in": 7200}),
            httpx.Response(200, json={"access_token": ACCESS_TOKEN, "expires_in": 0}),
        )
    )

    client = _client(lambda _request: next(responses))
    try:
        with pytest.raises(WeChatResponseError):
            client.get_access_token()
        with pytest.raises(WeChatResponseError):
            client.get_access_token()
    finally:
        client.close()


def test_client_repr_and_lifecycle_are_secret_safe():
    client = _client(lambda _request: _token_response())

    assert APP_ID not in repr(client)
    assert APP_SECRET not in repr(client)
    with client as active:
        assert active is client
    assert client.is_closed


@pytest.mark.parametrize(
    ("exception_type", "expected_phase"),
    [
        (httpx.ConnectError, "pre_send"),
        (httpx.ConnectTimeout, "pre_send"),
        (httpx.ReadTimeout, "post_send"),
        (httpx.WriteError, "post_send"),
        (httpx.RemoteProtocolError, "post_send"),
        (httpx.PoolTimeout, "pre_send"),
    ],
)
def test_transport_errors_preserve_only_safe_request_phase(
    exception_type, expected_phase, caplog
):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exception_type(
            f"network failure with {APP_SECRET} and {request.url}",
            request=request,
        )

    client = _client(handler)
    try:
        with pytest.raises(WeChatTransportError) as caught:
            client.get_access_token()
    finally:
        client.close()

    assert caught.value.phase == expected_phase
    observable = f"{caught.value!s} {caught.value!r} {caplog.text}"
    assert APP_ID not in observable
    assert APP_SECRET not in observable
    assert "api.weixin.qq.com" not in observable
    assert caught.value.__context__ is None


def test_client_exposes_only_a_hashed_account_identifier():
    client = _client(lambda _request: _token_response())
    try:
        account_id = client.account_id
    finally:
        client.close()

    assert account_id.startswith("sha256:")
    assert len(account_id) == len("sha256:") + 16
    assert APP_ID not in account_id
    assert APP_SECRET not in account_id
