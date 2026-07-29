from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from ...secrets import SecretStore
from .credentials import WeChatCredentials
from .errors import (
    WeChatHTTPError,
    WeChatResponseError,
    WeChatTransportError,
    classify_wechat_error,
)


_BASE_URL = "https://api.weixin.qq.com"
_DEFAULT_TIMEOUT = 30.0
_TOKEN_REFRESH_MARGIN = 120.0
_WECHAT_LOG_HOST = "api.weixin.qq.com"


def _redact_wechat_log_arg(value: Any) -> Any:
    text = str(value)
    if _WECHAT_LOG_HOST not in text or "?" not in text:
        return value
    return text.split("?", 1)[0] + "?[redacted]"


class _WeChatQueryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_wechat_log_arg(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {
                key: _redact_wechat_log_arg(value)
                for key, value in record.args.items()
            }
        return True


# httpx logs full request URLs at INFO after a response. The WeChat API carries
# credentials in its required query parameters, so remove the whole query before
# any configured handler can format the record.
logging.getLogger("httpx").addFilter(_WeChatQueryFilter())


@dataclass(frozen=True, slots=True, repr=False)
class _CachedToken:
    value: str
    expires_at: float


class WeChatClient:
    """Synchronous, single-account client for the WeChat Official Account API."""

    def __init__(
        self,
        credentials: WeChatCredentials,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        timeout: float | httpx.Timeout = _DEFAULT_TIMEOUT,
    ) -> None:
        self._credentials = credentials
        self._clock = clock
        self._tokens: dict[str, _CachedToken] = {}
        self._token_lock = threading.Lock()
        self._http = httpx.Client(
            base_url=_BASE_URL,
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
        )

    @classmethod
    def from_store(
        cls,
        secrets: SecretStore,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        timeout: float | httpx.Timeout = _DEFAULT_TIMEOUT,
    ) -> "WeChatClient":
        return cls(
            WeChatCredentials.from_store(secrets),
            transport=transport,
            clock=clock,
            timeout=timeout,
        )

    def __repr__(self) -> str:
        return "WeChatClient(<redacted>)"

    @property
    def account_id(self) -> str:
        digest = hashlib.sha256(self._credentials.app_id.encode("utf-8")).hexdigest()
        return f"sha256:{digest[:16]}"

    @property
    def is_closed(self) -> bool:
        return self._http.is_closed

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "WeChatClient":
        self._http.__enter__()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self._http.__exit__(exc_type, exc_value, traceback)

    def get_access_token(self) -> str:
        app_id = self._credentials.app_id
        with self._token_lock:
            cached = self._tokens.get(app_id)
            now = self._clock()
            if cached is not None and now < cached.expires_at - _TOKEN_REFRESH_MARGIN:
                return cached.value

            payload = self._request_object(
                "GET",
                "/cgi-bin/token",
                params={
                    "grant_type": "client_credential",
                    "appid": app_id,
                    "secret": self._credentials.app_secret,
                },
                sensitive_values=(app_id, self._credentials.app_secret),
            )
            token = payload.get("access_token")
            expires_in = payload.get("expires_in")
            if (
                not isinstance(token, str)
                or not token
                or isinstance(expires_in, bool)
                or not isinstance(expires_in, (int, float))
                or expires_in <= 0
            ):
                raise WeChatResponseError()

            self._tokens[app_id] = _CachedToken(
                value=token,
                expires_at=now + float(expires_in),
            )
            return token

    def request_json(
        self,
        method: str,
        path: str,
        params: Any = None,
        json: Any = None,
        files: Any = None,
    ) -> dict[str, Any]:
        normalized_path = self._normalize_path(path)
        access_token = self.get_access_token()
        request_params = dict(params or {})
        request_params["access_token"] = access_token
        return self._request_object(
            method,
            normalized_path,
            params=request_params,
            json=json,
            files=files,
            sensitive_values=(
                self._credentials.app_id,
                self._credentials.app_secret,
                access_token,
            ),
        )

    @staticmethod
    def _normalize_path(path: str) -> str:
        if not isinstance(path, str):
            raise ValueError("path must be a relative WeChat API path")
        path = path.strip()
        if not path or "://" in path or path.startswith("//") or "?" in path or "#" in path:
            raise ValueError("path must be a relative WeChat API path")
        if path.startswith("/cgi-bin/"):
            return path
        if path.startswith("cgi-bin/"):
            return "/" + path
        return "/cgi-bin/" + path.lstrip("/")

    def _request_object(
        self,
        method: str,
        path: str,
        *,
        params: Any = None,
        json: Any = None,
        files: Any = None,
        sensitive_values: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        request_kwargs: dict[str, Any] = {"params": params}
        if json is not None:
            request_kwargs["json"] = json
        if files is not None:
            request_kwargs["files"] = files
        response: httpx.Response | None = None
        transport_phase: str | None = None
        try:
            response = self._http.request(method, path, **request_kwargs)
        except httpx.RequestError as exc:
            transport_phase = (
                "pre_send"
                if isinstance(
                    exc,
                    (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout),
                )
                else "post_send"
            )
        if response is None:
            # Raise outside the handler so the original request (including its
            # credential-bearing query) is not retained as exception context.
            raise WeChatTransportError(transport_phase or "post_send")

        if response.status_code < 200 or response.status_code >= 300:
            raise WeChatHTTPError(response.status_code)
        invalid_json = False
        try:
            payload = response.json()
        except ValueError:
            invalid_json = True
            payload = None
        if invalid_json:
            # JSONDecodeError retains the response document; do not chain it.
            raise WeChatResponseError()
        if not isinstance(payload, Mapping):
            raise WeChatResponseError()

        result = dict(payload)
        errcode = result.get("errcode", 0)
        if isinstance(errcode, bool) or not isinstance(errcode, int):
            if errcode not in (None, 0, "0"):
                raise WeChatResponseError()
        elif errcode != 0:
            raise classify_wechat_error(
                errcode,
                result.get("errmsg"),
                sensitive_values=sensitive_values,
            )
        return result
