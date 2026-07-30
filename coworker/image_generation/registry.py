from __future__ import annotations

import os
from typing import Any

from ..providers import normalize_openai_image_model
from .base import ImageAuthError, ImageGenerationProvider
from .openai import DEFAULT_BASE_URL, OpenAIImageProvider


def _stored_profile(secrets: Any, profile: str) -> dict[str, Any]:
    if profile != "openai":
        raise ValueError(f"unsupported image provider: {profile}")
    stored = secrets.get("provider:openai") or {}
    return stored if isinstance(stored, dict) else {}


def _image_model(stored: dict[str, Any]) -> str:
    return normalize_openai_image_model(stored.get("image_model"))


def describe_image_provider(
    secrets: Any,
    profile: str = "openai",
) -> dict[str, str]:
    """Return only approval-safe image provider metadata without building a provider."""

    stored = _stored_profile(secrets, profile)
    return {"provider": "OpenAI", "model": _image_model(stored)}


def build_image_provider(
    secrets: Any,
    profile: str = "openai",
    *,
    transport: Any = None,
) -> ImageGenerationProvider:
    """Build an image provider from one SecretStore profile without exposing its key."""
    stored = _stored_profile(secrets, profile)
    raw_key = stored.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
    api_key = raw_key.strip() if isinstance(raw_key, str) else ""
    model = _image_model(stored)
    raw_base_url = stored.get("base_url") or DEFAULT_BASE_URL
    base_url = raw_base_url.strip() if isinstance(raw_base_url, str) else DEFAULT_BASE_URL
    return OpenAIImageProvider(
        api_key=api_key,
        model=model,
        base_url=base_url,
        transport=transport,
    )


__all__ = ["build_image_provider", "describe_image_provider"]
