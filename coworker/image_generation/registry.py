from __future__ import annotations

import os
from typing import Any

from ..providers import normalize_openai_image_model
from .base import ImageAuthError, ImageGenerationProvider
from .openai import DEFAULT_BASE_URL, OpenAIImageProvider


def build_image_provider(
    secrets: Any,
    profile: str = "openai",
    *,
    transport: Any = None,
) -> ImageGenerationProvider:
    """Build an image provider from one SecretStore profile without exposing its key."""
    if profile != "openai":
        raise ValueError(f"unsupported image provider: {profile}")
    stored = secrets.get("provider:openai") or {}
    if not isinstance(stored, dict):
        stored = {}
    raw_key = stored.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
    api_key = raw_key.strip() if isinstance(raw_key, str) else ""
    model = normalize_openai_image_model(stored.get("image_model"))
    raw_base_url = stored.get("base_url") or DEFAULT_BASE_URL
    base_url = raw_base_url.strip() if isinstance(raw_base_url, str) else DEFAULT_BASE_URL
    return OpenAIImageProvider(
        api_key=api_key,
        model=model,
        base_url=base_url,
        transport=transport,
    )


__all__ = ["build_image_provider"]
