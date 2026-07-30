from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any


class AssetPlanError(ValueError):
    """Raised when a structured article asset plan is invalid."""


@dataclass(frozen=True)
class CoverRequest:
    prompt: str
    output_path: str = "cover.png"
    aspect_ratio: str = "2.35:1"
    image_type: str = "conceptual"
    palette: str = "cool"
    rendering: str = "digital"
    text_density: str = "title-only"
    mood: str = "bold"


@dataclass(frozen=True)
class IllustrationRequest:
    heading: str
    prompt: str
    output_path: str
    aspect_ratio: str = "16:9"


@dataclass(frozen=True)
class IllustrationPlan:
    cover: CoverRequest
    illustrations: tuple[IllustrationRequest, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "illustrations", tuple(self.illustrations))


@dataclass(frozen=True)
class AssetManifest:
    reviewed_hash: str
    plan_hash: str
    provider: str
    model: str
    assets: tuple[dict[str, str], ...]

    def __post_init__(self) -> None:
        normalized: list[dict[str, str]] = []
        for index, asset in enumerate(self.assets):
            if not isinstance(asset, Mapping):
                raise AssetPlanError(f"assets[{index}] must be a mapping")
            unknown = set(asset) - {"output_path", "sha256"}
            if unknown:
                names = ", ".join(sorted((repr(name) for name in unknown)))
                raise AssetPlanError(f"assets[{index}] has unknown field(s): {names}")
            missing = {"output_path", "sha256"} - set(asset)
            if missing:
                names = ", ".join(sorted(missing))
                raise AssetPlanError(f"assets[{index}] is missing field(s): {names}")
            output_path = _require_string(asset["output_path"], f"assets[{index}].output_path")
            sha256 = _require_string(asset["sha256"], f"assets[{index}].sha256")
            if not output_path.strip() or not sha256.strip():
                raise AssetPlanError(f"assets[{index}] fields must not be blank")
            normalized.append({"output_path": output_path, "sha256": sha256})
        object.__setattr__(self, "assets", tuple(normalized))


_COVER_FIELDS = {
    "prompt",
    "output_path",
    "aspect_ratio",
    "image_type",
    "palette",
    "rendering",
    "text_density",
    "mood",
}
_COVER_DEFAULTS = {
    "output_path": "cover.png",
    "aspect_ratio": "2.35:1",
    "image_type": "conceptual",
    "palette": "cool",
    "rendering": "digital",
    "text_density": "title-only",
    "mood": "bold",
}
_ILLUSTRATION_FIELDS = {"heading", "prompt", "output_path", "aspect_ratio"}
_ILLUSTRATION_DEFAULTS = {"aspect_ratio": "16:9"}
_ASPECT_RATIO = re.compile(r"[1-9]\d*(?:\.\d+)?:[1-9]\d*(?:\.\d+)?\Z")
_ALLOWED_EXTENSIONS = {".png", ".jpg", ".webp"}
_WINDOWS_FORBIDDEN = frozenset('<>:"|?*')
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)


def parse_asset_plan(
    cover_request: object,
    illustration_plan: object,
) -> IllustrationPlan:
    """Validate Tool JSON arguments without performing generation or filesystem I/O."""
    cover_data = _parse_mapping(
        cover_request,
        location="cover_request",
        allowed=_COVER_FIELDS,
        required={"prompt"},
        defaults=_COVER_DEFAULTS,
    )
    if not isinstance(illustration_plan, list):
        raise AssetPlanError("illustration_plan must be a list")
    if len(illustration_plan) > 8:
        raise AssetPlanError("asset plan exceeds the 9 images total limit")

    cover = CoverRequest(
        prompt=_nonblank(cover_data["prompt"], "cover_request.prompt"),
        output_path=_validate_output_path(
            cover_data["output_path"], "cover_request.output_path"
        ),
        aspect_ratio=_validate_aspect_ratio(
            cover_data["aspect_ratio"], "cover_request.aspect_ratio"
        ),
        image_type=_require_string(
            cover_data["image_type"], "cover_request.image_type"
        ),
        palette=_require_string(cover_data["palette"], "cover_request.palette"),
        rendering=_require_string(
            cover_data["rendering"], "cover_request.rendering"
        ),
        text_density=_require_string(
            cover_data["text_density"], "cover_request.text_density"
        ),
        mood=_require_string(cover_data["mood"], "cover_request.mood"),
    )

    illustrations: list[IllustrationRequest] = []
    for index, raw_request in enumerate(illustration_plan):
        location = f"illustration_plan[{index}]"
        data = _parse_mapping(
            raw_request,
            location=location,
            allowed=_ILLUSTRATION_FIELDS,
            required={"heading", "prompt", "output_path"},
            defaults=_ILLUSTRATION_DEFAULTS,
        )
        illustrations.append(
            IllustrationRequest(
                heading=_nonblank(data["heading"], f"{location}.heading"),
                prompt=_nonblank(data["prompt"], f"{location}.prompt"),
                output_path=_validate_output_path(
                    data["output_path"], f"{location}.output_path"
                ),
                aspect_ratio=_validate_aspect_ratio(
                    data["aspect_ratio"], f"{location}.aspect_ratio"
                ),
            )
        )

    seen: set[str] = set()
    for output_path in (cover.output_path, *(item.output_path for item in illustrations)):
        duplicate_key = output_path.casefold()
        if duplicate_key in seen:
            raise AssetPlanError(f"duplicate output_path: {output_path!r}")
        seen.add(duplicate_key)

    return IllustrationPlan(cover=cover, illustrations=tuple(illustrations))


def _parse_mapping(
    value: object,
    *,
    location: str,
    allowed: set[str],
    required: set[str],
    defaults: Mapping[str, str],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AssetPlanError(f"{location} must be a mapping")
    unknown = set(value) - allowed
    if unknown:
        names = ", ".join(sorted((repr(name) for name in unknown)))
        raise AssetPlanError(f"{location} has unknown field(s): {names}")
    missing = required - set(value)
    if missing:
        names = ", ".join(sorted(missing))
        raise AssetPlanError(f"{location} is missing required field(s): {names}")
    return {**defaults, **value}


def _require_string(value: object, location: str) -> str:
    if not isinstance(value, str):
        raise AssetPlanError(f"{location} must be a string")
    return value


def _nonblank(value: object, location: str) -> str:
    text = _require_string(value, location)
    if not text.strip():
        raise AssetPlanError(f"{location} must not be blank")
    return text


def _validate_aspect_ratio(value: object, location: str) -> str:
    aspect_ratio = _require_string(value, location)
    if _ASPECT_RATIO.fullmatch(aspect_ratio) is None:
        raise AssetPlanError(f"{location} is not a valid aspect_ratio")
    return aspect_ratio


def _validate_output_path(value: object, location: str) -> str:
    output_path = _require_string(value, location)
    if not output_path or output_path != output_path.strip():
        raise AssetPlanError(f"{location} must be a non-blank relative path")

    windows_path = PureWindowsPath(output_path)
    if windows_path.drive or windows_path.root:
        raise AssetPlanError(f"{location} must be a relative path")

    normalized = output_path.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    parts = normalized.split("/")
    if posix_path.is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise AssetPlanError(f"{location} must not be absolute or contain traversal")

    for part in parts:
        if part[-1] in {" ", "."} or any(ord(character) < 32 for character in part):
            raise AssetPlanError(f"{location} is not portable across platforms")
        if any(character in _WINDOWS_FORBIDDEN for character in part):
            raise AssetPlanError(f"{location} is not portable across platforms")
        if part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED:
            raise AssetPlanError(f"{location} is not portable across platforms")

    if posix_path.suffix.casefold() not in _ALLOWED_EXTENSIONS:
        raise AssetPlanError(f"{location} must end in .png, .jpg, or .webp")
    return normalized


__all__ = [
    "AssetManifest",
    "AssetPlanError",
    "CoverRequest",
    "IllustrationPlan",
    "IllustrationRequest",
    "parse_asset_plan",
]
