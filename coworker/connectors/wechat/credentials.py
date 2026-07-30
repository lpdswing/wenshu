from __future__ import annotations

from dataclasses import dataclass

from ...secrets import SecretStore
from .errors import WeChatCredentialError


_PROFILE_KEY = "wechat_official:default"
_MISSING_CREDENTIALS = "微信公众号尚未连接"


@dataclass(frozen=True, repr=False, slots=True)
class WeChatCredentials:
    app_id: str
    app_secret: str

    def __post_init__(self) -> None:
        if not isinstance(self.app_id, str) or not isinstance(self.app_secret, str):
            raise WeChatCredentialError(_MISSING_CREDENTIALS)
        app_id = self.app_id.strip()
        app_secret = self.app_secret.strip()
        if not app_id or not app_secret:
            raise WeChatCredentialError(_MISSING_CREDENTIALS)
        object.__setattr__(self, "app_id", app_id)
        object.__setattr__(self, "app_secret", app_secret)

    @classmethod
    def from_store(cls, secrets: SecretStore) -> "WeChatCredentials":
        row = secrets.get(_PROFILE_KEY) or {}
        if not isinstance(row, dict):
            raise WeChatCredentialError(_MISSING_CREDENTIALS)
        return cls(row.get("app_id", ""), row.get("app_secret", ""))

    def __repr__(self) -> str:
        return "WeChatCredentials(<redacted>)"
