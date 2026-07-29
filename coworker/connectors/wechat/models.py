from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias


WeChatErrorKind: TypeAlias = Literal[
    "invalid_credentials",
    "ip_allowlist",
    "permission_denied",
    "rate_limited",
    "unknown",
]


@dataclass(frozen=True, slots=True, repr=False)
class WeChatErrorData:
    """The deliberately small, safe-to-display part of a WeChat API error."""

    errcode: int
    errmsg: str
    kind: WeChatErrorKind

    def __repr__(self) -> str:
        return (
            "WeChatErrorData("
            f"errcode={self.errcode!r}, kind={self.kind!r}, errmsg={self.errmsg!r})"
        )
