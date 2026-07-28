from __future__ import annotations

import asyncio
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import aisuite as ai

from ..content.paths import resolve_in_roots
from .base import ImageRequest
from .registry import build_image_provider

_ALLOWED_OUTPUT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_ALLOWED_QUALITIES = {"auto", "low", "medium", "high"}

_GENERATE_IMAGE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "generate_image",
        "description": (
            "Generate one image with the configured image provider and save it inside "
            "the current workspace. This is a paid operation and requires approval."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Detailed visual prompt for the image provider.",
                    "minLength": 1,
                },
                "output_path": {
                    "type": "string",
                    "description": "Workspace-relative output path ending in PNG, JPG, JPEG, or WebP.",
                    "minLength": 1,
                },
                "aspect_ratio": {
                    "type": "string",
                    "description": "Positive width:height ratio, for example 1:1 or 16:9.",
                    "default": "1:1",
                },
                "quality": {
                    "type": "string",
                    "enum": ["auto", "low", "medium", "high"],
                    "default": "high",
                },
            },
            "required": ["prompt", "output_path"],
            "additionalProperties": False,
        },
    },
}


def _validate_aspect_ratio(value: str) -> str:
    try:
        width_text, height_text = value.split(":", 1)
        width = float(width_text)
        height = float(height_text)
        if (
            not math.isfinite(width)
            or not math.isfinite(height)
            or width <= 0
            or height <= 0
        ):
            raise ValueError
    except (AttributeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError("aspect_ratio must be a positive width:height ratio") from exc
    return value


def make_generate_image_tool(*, secrets: Any, roots: Iterable[str | Path]):
    """Build a lazy, approval-gated image tool whose closure retains provider credentials."""
    allowed_roots = tuple(Path(root).expanduser().resolve() for root in roots)
    if not allowed_roots:
        raise ValueError("generate_image requires at least one writable root")

    def generate_image(
        prompt: str,
        output_path: str,
        aspect_ratio: str = "1:1",
        quality: str = "high",
    ) -> dict[str, str]:
        """Generate one approved image inside the current workspace."""
        raw_output = Path(output_path).expanduser()
        candidate = raw_output if raw_output.is_absolute() else allowed_roots[0] / raw_output
        resolved_output = resolve_in_roots(candidate, allowed_roots, must_exist=False)
        if resolved_output.suffix.casefold() not in _ALLOWED_OUTPUT_SUFFIXES:
            raise ValueError("output_path must end in .png, .jpg, .jpeg, or .webp")
        ratio = _validate_aspect_ratio(aspect_ratio)
        if quality not in _ALLOWED_QUALITIES:
            raise ValueError("quality must be auto, low, medium, or high")

        request = ImageRequest(
            prompt=prompt,
            output_path=resolved_output,
            aspect_ratio=ratio,
            quality=quality,
        )
        provider = build_image_provider(secrets)
        result = asyncio.run(provider.generate(request))
        return {
            "path": str(result.path),
            "provider": result.provider,
            "model": result.model,
            "sha256": result.sha256,
        }

    generate_image.__coworker_schema__ = _GENERATE_IMAGE_SCHEMA
    generate_image.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="generate_image",
        category="content-generation",
        risk_level="medium",
        capabilities=["image-generation"],
        requires_approval=True,
    )
    return generate_image


__all__ = ["make_generate_image_tool"]
