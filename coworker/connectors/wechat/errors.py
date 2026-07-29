from __future__ import annotations

import re
from collections.abc import Iterable

from .models import WeChatErrorData, WeChatErrorKind


_MAX_ERRMSG_LENGTH = 160
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(access[_-]?token|token|app[_-]?secret|appsecret|secret|authorization|"
    r"credential|password)\b\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_TOKENISH_RE = re.compile(r"(?<![\w])(?=[A-Za-z0-9._~-]{20,}(?![\w]))(?=[^\s]*\d)[A-Za-z0-9._~-]+")

_KNOWN_ERRORS: dict[int, tuple[WeChatErrorKind, str]] = {
    40013: ("invalid_credentials", "微信公众号凭据无效"),
    40125: ("invalid_credentials", "微信公众号凭据无效"),
    40164: ("ip_allowlist", "当前网络地址不在微信公众号白名单中"),
    48001: ("permission_denied", "微信公众号接口权限不足"),
    45009: ("rate_limited", "微信公众号接口调用过于频繁"),
}


class WeChatError(Exception):
    """Base class whose text and repr are always safe for logs and UI errors."""

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str(self)!r})"


class WeChatCredentialError(WeChatError):
    pass


class WeChatTransportError(WeChatError):
    def __init__(self) -> None:
        super().__init__("连接微信接口失败")


class WeChatHTTPError(WeChatError):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"微信接口返回 HTTP {status_code}")


class WeChatResponseError(WeChatError):
    def __init__(self) -> None:
        super().__init__("微信接口返回了无效响应")


class WeChatAPIError(WeChatError):
    def __init__(self, data: WeChatErrorData) -> None:
        self.data = data
        super().__init__(f"微信接口错误 {data.errcode} ({data.kind}): {data.errmsg}")

    @property
    def errcode(self) -> int:
        return self.data.errcode

    @property
    def errmsg(self) -> str:
        return self.data.errmsg

    @property
    def kind(self) -> WeChatErrorKind:
        return self.data.kind


def sanitize_wechat_errmsg(
    errmsg: object,
    *,
    sensitive_values: Iterable[str] = (),
) -> str:
    """Conservatively retain useful vendor text without retaining secret-shaped data."""

    if not isinstance(errmsg, str):
        return "微信接口返回未知错误"

    sanitized = " ".join(errmsg.split())
    sanitized = _URL_RE.sub("[redacted-url]", sanitized)
    sanitized = _SENSITIVE_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}=[redacted]", sanitized
    )
    sanitized = _BEARER_RE.sub("Bearer [redacted]", sanitized)
    for value in sorted(
        {value for value in sensitive_values if isinstance(value, str) and value},
        key=len,
        reverse=True,
    ):
        sanitized = sanitized.replace(value, "[redacted]")
    sanitized = _TOKENISH_RE.sub("[redacted]", sanitized)
    if not sanitized:
        sanitized = "微信接口返回未知错误"
    if len(sanitized) > _MAX_ERRMSG_LENGTH:
        sanitized = sanitized[: _MAX_ERRMSG_LENGTH - 1].rstrip() + "…"
    return sanitized


def classify_wechat_error(
    errcode: int,
    errmsg: object,
    *,
    sensitive_values: Iterable[str] = (),
) -> WeChatAPIError:
    known = _KNOWN_ERRORS.get(errcode)
    if known is not None:
        kind, safe_message = known
    else:
        kind = "unknown"
        safe_message = sanitize_wechat_errmsg(
            errmsg,
            sensitive_values=sensitive_values,
        )
    return WeChatAPIError(
        WeChatErrorData(errcode=errcode, errmsg=safe_message, kind=kind)
    )
