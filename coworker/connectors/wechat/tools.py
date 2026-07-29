from __future__ import annotations

import hmac
import re
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import aisuite as ai

from ...secrets import SecretStore
from .client import WeChatClient
from .drafts import DraftResult, ReceiptStore, ReceiptStoreError, create_draft
from .errors import (
    WeChatAPIError,
    WeChatCredentialError,
    WeChatHTTPError,
    WeChatResponseError,
    WeChatTransportError,
)
from .images import WeChatImageError
from .preview import DraftPreview, prepare_preview

_CHANNEL = "微信公众号"
_DEFAULT_COLOR = "#07C160"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_ERROR_KINDS = frozenset(
    {
        "arguments_changed",
        "http",
        "image",
        "invalid_credentials",
        "invalid_preview",
        "invalid_response",
        "ip_allowlist",
        "local_io",
        "not_connected",
        "not_prepared",
        "permission_denied",
        "preview_changed",
        "rate_limited",
        "receipt_invalid",
        "receipt_write",
        "transport",
        "unknown",
    }
)

_PREPARE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "prepare_wechat_draft",
        "description": (
            "Generate a local, reviewable preview for a WeChat Official Account draft. "
            "This never publishes or sends the article."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "article_path": {"type": "string"},
                "theme": {"type": "string"},
                "color": {"type": "string"},
                "cover_path": {"type": "string"},
            },
            "required": ["article_path", "theme"],
        },
    },
}

_CREATE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "create_wechat_draft",
        "description": (
            "Save the exactly reviewed article to the connected WeChat Official Account "
            "draft box. This never publishes or broadcasts it."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "article_path": {"type": "string"},
                "preview_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "theme": {"type": "string"},
                "color": {"type": "string"},
                "cover_path": {"type": "string"},
            },
            "required": ["article_path", "preview_hash", "theme"],
        },
    },
}


@dataclass(frozen=True, slots=True)
class _PreparedDraft:
    article_argument: str
    preview: DraftPreview


def _metadata(name: str) -> ai.ToolMetadata:
    return ai.ToolMetadata(
        name=name,
        category="connector",
        risk_level="medium",
        capabilities=["wechat_official", "write", "draft_only"],
        requires_approval=True,
    )


def _root_paths(roots: Iterable[Any] | Callable[[], Iterable[Any]] | None) -> tuple[Path, ...]:
    source = roots() if callable(roots) else roots
    paths: list[Path] = []
    for root in source or ():
        if hasattr(root, "writable") and not bool(root.writable):
            continue
        value = getattr(root, "path", root)
        paths.append(Path(value))
    return tuple(paths)


def _connected_settings(secrets: SecretStore) -> tuple[bool, bool] | None:
    profile = secrets.get("wechat_official:default") or {}
    if not isinstance(profile, Mapping) or not bool(profile.get("enabled", True)):
        return None
    app_id = profile.get("app_id")
    app_secret = profile.get("app_secret")
    if not isinstance(app_id, str) or not app_id.strip():
        return None
    if not isinstance(app_secret, str) or not app_secret.strip():
        return None
    need_open_comment = (
        profile.get("need_open_comment")
        if type(profile.get("need_open_comment")) is bool
        else False
    )
    only_fans_can_comment = (
        profile.get("only_fans_can_comment")
        if need_open_comment and type(profile.get("only_fans_can_comment")) is bool
        else False
    )
    return need_open_comment, only_fans_can_comment


def _safe_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        return ""
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        return ""
    return value


def _display(preview: DraftPreview | None = None) -> dict[str, Any]:
    return {
        "channel": _CHANNEL,
        "title": preview.title if preview is not None else "",
        "digest": preview.digest if preview is not None else "",
        "cover_path": _safe_relative_path(preview.cover_path) if preview is not None else "",
        "image_count": preview.image_count if preview is not None else 0,
        "theme": preview.theme if preview is not None else "",
        "color": preview.color if preview is not None else "",
        "draft_only": True,
    }


def _prepare_failure(error_kind: str) -> dict[str, Any]:
    return {**_display(), "status": "failed", "error_kind": error_kind}


def _same_hash(left: object, right: object) -> bool:
    return (
        isinstance(left, str)
        and isinstance(right, str)
        and bool(_SHA256_RE.fullmatch(left))
        and bool(_SHA256_RE.fullmatch(right))
        and hmac.compare_digest(left, right)
    )


def _arguments_match(entry: _PreparedDraft, arguments: Mapping[str, Any]) -> bool:
    preview = entry.preview
    article_path = arguments.get("article_path")
    theme = arguments.get("theme")
    color = arguments.get("color", _DEFAULT_COLOR)
    cover_path = arguments.get("cover_path")
    if not isinstance(article_path, str) or article_path != entry.article_argument:
        return False
    if not isinstance(theme, str) or theme != preview.theme:
        return False
    if not isinstance(color, str) or color.upper() != preview.color:
        return False
    if cover_path is not None and (
        not isinstance(cover_path, str) or cover_path != preview.cover_path
    ):
        return False
    return _same_hash(arguments.get("preview_hash"), preview.preview_hash)


def _safe_uploaded_assets(result: DraftResult, preview: DraftPreview) -> list[str]:
    body_paths = {
        path for path in (_safe_relative_path(item) for item in preview.image_refs) if path
    }
    cover_path = _safe_relative_path(preview.cover_path)
    allowed = {f"body:{path}" for path in body_paths}
    if cover_path:
        allowed.add(f"cover:{cover_path}")
    return [
        label
        for label in result.uploaded_assets
        if isinstance(label, str) and label in allowed
    ]


def _exception_kind(error: BaseException) -> str:
    if isinstance(error, WeChatAPIError):
        return error.kind
    if isinstance(error, WeChatCredentialError):
        return "invalid_credentials"
    if isinstance(error, WeChatTransportError):
        return "transport"
    if isinstance(error, WeChatHTTPError):
        return "http"
    if isinstance(error, WeChatResponseError):
        return "invalid_response"
    if isinstance(error, WeChatImageError):
        return "image"
    if isinstance(error, ReceiptStoreError):
        return "receipt_invalid"
    return "local_io"


def _safe_result(
    status: str,
    title: str,
    error_kind: str | None = None,
    uploaded_assets: Iterable[str] = (),
) -> dict[str, Any]:
    canonical_status = status if status in {"success", "duplicate", "failed", "unknown"} else "failed"
    canonical_error = error_kind if error_kind in _SAFE_ERROR_KINDS else None
    assets = list(uploaded_assets)
    summary = {
        "status": canonical_status,
        "title": title,
        "error_kind": canonical_error,
        "uploaded_asset_count": len(assets),
        "uploaded_assets": assets,
        "draft_only": True,
    }
    return {**summary, "_display": {"wechat_draft_result": summary}}


def make_wechat_tools(
    secrets: SecretStore,
    *,
    roots: Iterable[Any] | Callable[[], Iterable[Any]] | None = None,
    preview_factory: Callable[..., DraftPreview] | None = None,
    client_factory: Callable[[SecretStore], WeChatClient] | None = None,
    receipt_store_factory: Callable[[str | Path], ReceiptStore] | None = None,
    draft_creator: Callable[[DraftPreview, WeChatClient, ReceiptStore], DraftResult] | None = None,
) -> list[Callable[..., dict[str, Any]]]:
    """Build the two draft-only WeChat tools and their factory-local preview cache."""

    prepare = preview_factory or prepare_preview
    build_client = client_factory or WeChatClient.from_store
    build_receipt_store = receipt_store_factory or ReceiptStore
    submit_draft = draft_creator or create_draft
    cache_lock = threading.Lock()
    cached: _PreparedDraft | None = None

    def prepare_wechat_draft(
        article_path: str,
        theme: str,
        color: str = _DEFAULT_COLOR,
        cover_path: str | None = None,
    ) -> dict[str, Any]:
        nonlocal cached
        try:
            settings = _connected_settings(secrets)
        except Exception:
            return _prepare_failure("local_io")
        if settings is None:
            return _prepare_failure("not_connected")
        need_open_comment, only_fans_can_comment = settings
        try:
            preview = prepare(
                article_path,
                theme,
                color,
                cover_path,
                _root_paths(roots),
                need_open_comment=need_open_comment,
                only_fans_can_comment=only_fans_can_comment,
            )
            article_argument = str(article_path)
        except Exception:
            return _prepare_failure("invalid_preview")
        with cache_lock:
            cached = _PreparedDraft(article_argument=article_argument, preview=preview)
        return {**_display(preview), "preview_hash": preview.preview_hash}

    def create_wechat_draft(
        article_path: str,
        preview_hash: str,
        theme: str,
        color: str = _DEFAULT_COLOR,
        cover_path: str | None = None,
    ) -> dict[str, Any]:
        with cache_lock:
            entry = cached
        if entry is None:
            return _safe_result("failed", "", "not_prepared")
        arguments = {
            "article_path": article_path,
            "preview_hash": preview_hash,
            "theme": theme,
            "color": color,
            "cover_path": cover_path,
        }
        if not _arguments_match(entry, arguments):
            return _safe_result("failed", entry.preview.title, "arguments_changed")

        previous = entry.preview
        try:
            refreshed = prepare(
                entry.article_argument,
                previous.theme,
                previous.color,
                previous.cover_path,
                _root_paths(roots),
                need_open_comment=previous.need_open_comment,
                only_fans_can_comment=previous.only_fans_can_comment,
            )
        except Exception:
            return _safe_result("failed", previous.title, "preview_changed")
        if not _same_hash(refreshed.preview_hash, previous.preview_hash) or not _same_hash(
            refreshed.preview_hash, preview_hash
        ):
            return _safe_result("failed", previous.title, "preview_changed")

        try:
            current_settings = _connected_settings(secrets)
        except Exception:
            return _safe_result("failed", previous.title, "local_io")
        if current_settings is None:
            return _safe_result("failed", previous.title, "not_connected")
        if current_settings != (
            previous.need_open_comment,
            previous.only_fans_can_comment,
        ):
            return _safe_result("failed", previous.title, "preview_changed")

        client: WeChatClient | None = None
        try:
            client = build_client(secrets)
            receipt_store = build_receipt_store(refreshed.preview_path.parent)
            result = submit_draft(refreshed, client, receipt_store)
            assets = _safe_uploaded_assets(result, refreshed)
            return _safe_result(
                result.status,
                refreshed.title,
                result.error_kind,
                assets,
            )
        except Exception as error:
            return _safe_result("failed", refreshed.title, _exception_kind(error))
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    def create_approval_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
        with cache_lock:
            entry = cached
        if entry is None or not _arguments_match(entry, arguments):
            return _display()
        return _display(entry.preview)

    prepare_wechat_draft.__coworker_schema__ = _PREPARE_SCHEMA
    prepare_wechat_draft.__aisuite_tool_metadata__ = _metadata("prepare_wechat_draft")
    create_wechat_draft.__coworker_schema__ = _CREATE_SCHEMA
    create_wechat_draft.__aisuite_tool_metadata__ = _metadata("create_wechat_draft")
    create_wechat_draft.__coworker_approval_arguments__ = create_approval_arguments
    create_wechat_draft.__coworker_approval_once_only__ = True

    return [prepare_wechat_draft, create_wechat_draft]


__all__ = ["make_wechat_tools"]
