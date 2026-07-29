from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import stat
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

import aisuite as ai

from ..image_generation.base import (
    ImageGenerationError,
    ImageGenerationProvider,
    ImageRequest,
)
from .article import parse_article_text
from .hashing import article_text_hash
from .images import (
    AssetManifest,
    AssetPlanError,
    IllustrationPlan,
    parse_asset_plan,
)
from .paths import ContentPathError, resolve_in_roots
from .review import _BoundDirectory, bind_directory, prepare_article_review_file

_MANIFEST_NAME = "assets.manifest.json"
_MANIFEST_FIELDS = {"reviewed_hash", "plan_hash", "provider", "model", "assets"}

_PREPARE_ARTICLE_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "prepare_article_review",
        "description": (
            "Validate an article, create a text-only review page beside it, and return "
            "the hash that binds a later asset request to the reviewed text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "article_path": {
                    "type": "string",
                    "description": "Path to the Markdown article inside an allowed directory.",
                    "minLength": 1,
                }
            },
            "required": ["article_path"],
            "additionalProperties": False,
        },
    },
}

_GENERATE_ARTICLE_ASSETS_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "generate_article_assets",
        "description": (
            "Generate one cover and the requested article illustrations after confirming "
            "that the article still matches its reviewed hash. This is a paid operation "
            "and requires approval."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "article_path": {
                    "type": "string",
                    "description": "Path to the reviewed Markdown article.",
                    "minLength": 1,
                },
                "reviewed_hash": {
                    "type": "string",
                    "description": "The 64-character hash returned by prepare_article_review.",
                    "pattern": "^[0-9a-f]{64}$",
                },
                "cover_request": {
                    "type": "object",
                    "description": "The single cover image request.",
                    "properties": {
                        "prompt": {"type": "string", "minLength": 1},
                        "output_path": {
                            "type": "string",
                            "description": "Article-relative PNG, JPG, or WebP output path.",
                            "default": "cover.png",
                        },
                        "aspect_ratio": {
                            "type": "string",
                            "description": "Positive width:height ratio.",
                            "default": "2.35:1",
                        },
                        "image_type": {
                            "type": "string",
                            "default": "conceptual",
                        },
                        "palette": {"type": "string", "default": "cool"},
                        "rendering": {"type": "string", "default": "digital"},
                        "text_density": {
                            "type": "string",
                            "default": "title-only",
                        },
                        "mood": {"type": "string", "default": "bold"},
                    },
                    "required": ["prompt"],
                    "additionalProperties": False,
                },
                "illustration_plan": {
                    "type": "array",
                    "description": "Ordered article illustration requests, at most eight.",
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "heading": {"type": "string", "minLength": 1},
                            "prompt": {"type": "string", "minLength": 1},
                            "output_path": {
                                "type": "string",
                                "description": (
                                    "Article-relative PNG, JPG, or WebP output path."
                                ),
                                "minLength": 1,
                            },
                            "aspect_ratio": {
                                "type": "string",
                                "description": "Positive width:height ratio.",
                                "default": "16:9",
                            },
                        },
                        "required": ["heading", "prompt", "output_path"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": [
                "article_path",
                "reviewed_hash",
                "cover_request",
                "illustration_plan",
            ],
            "additionalProperties": False,
        },
    },
}


class ReviewChangedError(ValueError):
    """The current article no longer matches the hash returned for review."""


class _AssetValidationError(ValueError):
    """A provider result does not match the staged image request."""


@dataclass(frozen=True)
class _AssetTarget:
    output_path: str
    prompt: str
    aspect_ratio: str


ImageProviderFactory = Callable[[], ImageGenerationProvider]
ImageProviderSource = ImageGenerationProvider | ImageProviderFactory
RootSource = Iterable[str | Path] | Callable[[], Iterable[str | Path]]


@dataclass
class ContentTools:
    """Article review and hash-gated image tools bound to roots and a provider source."""

    roots: RootSource
    image_provider: ImageProviderSource = field(repr=False)
    _resolved_provider: ImageGenerationProvider | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _provider_factory: ImageProviderFactory | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not callable(self.roots) and not isinstance(self.roots, (list, tuple)):
            self.roots = tuple(self.roots)
        self._root_paths()

        source = self.image_provider
        if not isinstance(source, type) and callable(getattr(source, "generate", None)):
            self._resolved_provider = source
            return
        if not callable(source):
            raise TypeError("image_provider must provide generate() or be a zero-argument factory")
        self._provider_factory = source

    def prepare_article_review(self, article_path: str) -> dict[str, str]:
        """Create a safe text review and return its article-bound hash."""

        review = prepare_article_review_file(article_path, self._root_paths())
        return {
            "title": review.title,
            "summary": review.summary,
            "article_path": str(review.article_path),
            "preview_path": str(review.preview_path),
            "reviewed_hash": review.reviewed_hash,
        }

    async def generate_article_assets(
        self,
        article_path: str,
        reviewed_hash: str,
        cover_request: dict[str, Any],
        illustration_plan: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Generate a validated article asset plan only for unchanged reviewed text."""

        root_paths = self._root_paths()
        resolved_article_path = resolve_in_roots(
            article_path,
            root_paths,
            must_exist=True,
        )
        article_directory = resolved_article_path.parent
        directory_binding = bind_directory(article_directory)

        with directory_binding as directory:
            article_text = directory.read_text(resolved_article_path.name)
            article = parse_article_text(resolved_article_path, article_text)
            current_hash = article_text_hash(article)
            if not isinstance(reviewed_hash, str) or not hmac.compare_digest(
                current_hash,
                reviewed_hash,
            ):
                raise ReviewChangedError(
                    "article changed after review; reviewed_hash does not match "
                    "the current article hash"
                )

            plan = parse_asset_plan(cover_request, illustration_plan)
            plan_hash = _asset_plan_hash(plan)
            targets = _resolve_asset_targets(plan, article_directory, root_paths)
            _validate_manifest_path(article_directory, root_paths)
            manifest = _load_manifest(directory)
            if manifest is not None and _manifest_is_reusable(
                manifest,
                reviewed_hash=current_hash,
                plan_hash=plan_hash,
                targets=targets,
                directory=directory,
            ):
                return _completed_result(manifest, reused=True)

            provider = self._get_image_provider()
            _discard_manifest(directory)

            generated: list[dict[str, str]] = []
            provider_name: str | None = None
            model_name: str | None = None
            for index, target in enumerate(targets):
                try:
                    with tempfile.TemporaryDirectory(
                        prefix="coworker-image-asset-"
                    ) as staging_directory_name:
                        staging_directory = Path(staging_directory_name).resolve(
                            strict=True
                        )
                        if staging_directory.is_relative_to(article_directory):
                            raise ContentPathError(
                                "secure image staging is unavailable for this article directory"
                            )
                        staging_path = staging_directory / PurePosixPath(
                            target.output_path
                        ).name
                        request = ImageRequest(
                            prompt=target.prompt,
                            output_path=staging_path,
                            aspect_ratio=target.aspect_ratio,
                        )
                        result = await provider.generate(request)
                        asset, result_provider, result_model = (
                            _validated_generated_asset(
                                result,
                                target,
                                directory,
                                staging_path,
                                provider_name,
                                model_name,
                            )
                        )
                        if provider_name is None:
                            provider_name = result_provider
                            model_name = result_model
                except Exception as exc:
                    error_type, error_message = _safe_asset_failure(exc)
                    return {
                        "ok": False,
                        "status": "partial",
                        "reused": False,
                        "reviewed_hash": current_hash,
                        "plan_hash": plan_hash,
                        "assets": generated,
                        "failed_asset": {
                            "output_path": target.output_path,
                            "error_type": error_type,
                            "error": error_message,
                        },
                        "pending_assets": [
                            pending.output_path for pending in targets[index + 1 :]
                        ],
                    }
                generated.append(asset)

            if provider_name is None or model_name is None:  # pragma: no cover
                raise RuntimeError("asset plan did not contain a cover request")

            manifest = AssetManifest(
                reviewed_hash=current_hash,
                plan_hash=plan_hash,
                provider=provider_name,
                model=model_name,
                assets=tuple(generated),
            )
            _atomic_write_manifest(directory, manifest)
            return _completed_result(manifest, reused=False)

    def _root_paths(self) -> tuple[Path, ...]:
        source = self.roots() if callable(self.roots) else self.roots
        roots = tuple(
            Path(root).expanduser().resolve(strict=False) for root in source
        )
        if not roots:
            raise ContentPathError("at least one content root is required")
        return roots


    def _get_image_provider(self) -> ImageGenerationProvider:
        provider = self._resolved_provider
        if provider is not None:
            return provider

        factory = self._provider_factory
        if factory is None:  # pragma: no cover - guarded by __post_init__
            raise TypeError("image provider is not configured")
        provider = factory()
        if not callable(getattr(provider, "generate", None)):
            raise TypeError("image provider factory must return an object with generate()")
        self._resolved_provider = provider
        return provider


def _asset_plan_hash(plan: IllustrationPlan) -> str:
    canonical = json.dumps(
        asdict(plan),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _resolve_asset_targets(
    plan: IllustrationPlan,
    article_directory: Path,
    roots: tuple[Path, ...],
) -> tuple[_AssetTarget, ...]:
    requests = (plan.cover, *plan.illustrations)
    targets: list[_AssetTarget] = []
    resolved_outputs: set[str] = set()

    for item in requests:
        logical_path = article_directory.joinpath(*PurePosixPath(item.output_path).parts)
        _validate_logical_output_location(article_directory, item.output_path)
        resolved_path = resolve_in_roots(logical_path, roots, must_exist=False)
        if not resolved_path.is_relative_to(article_directory):
            raise ValueError(
                f"asset output is outside the article directory: {item.output_path}"
            )
        _validate_output_file_location(resolved_path, item.output_path)

        collision_key = str(resolved_path).casefold()
        if collision_key in resolved_outputs:
            raise AssetPlanError(
                f"asset output paths resolve to the same file: {item.output_path!r}"
            )
        resolved_outputs.add(collision_key)
        targets.append(
            _AssetTarget(
                output_path=item.output_path,
                prompt=item.prompt,
                aspect_ratio=item.aspect_ratio,
            )
        )

    return tuple(targets)


def _validate_logical_output_location(
    article_directory: Path,
    output_path: str,
) -> None:
    parts = PurePosixPath(output_path).parts
    current = article_directory
    for index, part in enumerate(parts):
        current = current / part
        try:
            status = current.stat(follow_symlinks=False)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ValueError(
                f"asset output path could not be inspected: {output_path}"
            ) from exc
        if stat.S_ISLNK(status.st_mode):
            raise ValueError(
                f"asset output path must not contain a symbolic link: {output_path}"
            )
        if index < len(parts) - 1:
            if not stat.S_ISDIR(status.st_mode):
                raise ValueError(
                    f"asset output parent is not a directory: {output_path}"
                )
        elif not stat.S_ISREG(status.st_mode):
            raise ValueError(f"asset output is not a regular file: {output_path}")


def _validate_output_file_location(path: Path, output_path: str) -> None:
    if path.exists() and not path.is_file():
        raise ValueError(f"asset output is not a regular file: {output_path}")

    existing_parent = path.parent
    while not existing_parent.exists():
        parent = existing_parent.parent
        if parent == existing_parent:
            break
        existing_parent = parent
    if not existing_parent.is_dir():
        raise ValueError(f"asset output parent is not a directory: {output_path}")


def _validate_manifest_path(
    article_directory: Path,
    roots: tuple[Path, ...],
) -> Path:
    manifest_path = article_directory / _MANIFEST_NAME
    resolved_manifest = resolve_in_roots(manifest_path, roots, must_exist=False)
    if not resolved_manifest.is_relative_to(article_directory):
        raise ValueError("assets manifest is outside the article directory")
    if manifest_path.is_symlink():
        raise ValueError("assets manifest must not be a symbolic link")
    if resolved_manifest.exists() and not resolved_manifest.is_file():
        raise ValueError("assets manifest is not a regular file")
    return manifest_path


def _load_manifest(directory: _BoundDirectory) -> AssetManifest | None:
    try:
        raw = json.loads(directory.read_text(_MANIFEST_NAME))
    except (
        FileNotFoundError,
        IsADirectoryError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ContentPathError,
    ):
        return None
    if not isinstance(raw, Mapping) or set(raw) != _MANIFEST_FIELDS:
        return None
    if not all(
        isinstance(raw[field], str)
        for field in ("reviewed_hash", "plan_hash", "provider", "model")
    ):
        return None
    if not raw["provider"].strip() or not raw["model"].strip():
        return None
    if not isinstance(raw["assets"], list):
        return None
    try:
        return AssetManifest(
            reviewed_hash=raw["reviewed_hash"],
            plan_hash=raw["plan_hash"],
            provider=raw["provider"],
            model=raw["model"],
            assets=tuple(raw["assets"]),
        )
    except (AssetPlanError, TypeError):
        return None


def _manifest_is_reusable(
    manifest: AssetManifest,
    *,
    reviewed_hash: str,
    plan_hash: str,
    targets: tuple[_AssetTarget, ...],
    directory: _BoundDirectory,
) -> bool:
    if manifest.reviewed_hash != reviewed_hash or manifest.plan_hash != plan_hash:
        return False
    if [asset["output_path"] for asset in manifest.assets] != [
        target.output_path for target in targets
    ]:
        return False

    for asset, target in zip(manifest.assets, targets, strict=True):
        try:
            with directory.open_binary(target.output_path) as source:
                actual_hash = _sha256_stream(source)
        except (FileNotFoundError, OSError, ContentPathError):
            return False
        if not hmac.compare_digest(actual_hash, asset["sha256"]):
            return False
    return True


def _safe_asset_failure(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, ImageGenerationError):
        return type(exc).__name__, str(exc)
    if isinstance(exc, _AssetValidationError):
        return "ValueError", str(exc)
    if isinstance(exc, ContentPathError):
        return type(exc).__name__, "asset output path changed after validation"
    return "ImageGenerationError", "image generation failed"


@contextmanager
def _open_staged_output(path: Path) -> Iterator[BinaryIO]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
    )
    descriptor = -1
    try:
        try:
            descriptor = os.open(path, flags)
            status = os.fstat(descriptor)
            current = path.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(status.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or (status.st_dev, status.st_ino)
                != (current.st_dev, current.st_ino)
            ):
                raise _AssetValidationError(
                    "image provider did not create a regular staging file"
                )
            source = os.fdopen(descriptor, mode="rb")
            descriptor = -1
        except _AssetValidationError:
            raise
        except (OSError, TypeError) as exc:
            raise _AssetValidationError(
                "image provider did not create a readable staging file"
            ) from exc

        with source:
            yield source
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _copy_staged_output(
    source: BinaryIO,
    destination: BinaryIO,
    claimed_hash: str,
) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(chunk)
        destination.write(chunk)
    actual_hash = digest.hexdigest()
    if not hmac.compare_digest(actual_hash, claimed_hash):
        raise _AssetValidationError(
            "image provider returned a sha256 that does not match its output"
        )
    return actual_hash


def _validated_generated_asset(
    result: Any,
    target: _AssetTarget,
    directory: _BoundDirectory,
    staging_path: Path,
    expected_provider: str | None,
    expected_model: str | None,
) -> tuple[dict[str, str], str, str]:
    try:
        requested_path = staging_path.resolve(strict=True)
        result_path = Path(result.path).resolve(strict=True)
    except (AttributeError, OSError, RuntimeError, TypeError) as exc:
        raise _AssetValidationError(
            "image provider did not return its requested staging path"
        ) from exc
    if result_path != requested_path:
        raise _AssetValidationError(
            "image provider returned a different output path"
        )

    claimed_hash = getattr(result, "sha256", None)
    if not isinstance(claimed_hash, str):
        raise _AssetValidationError("image provider returned an invalid sha256")

    provider = getattr(result, "provider", None)
    model = getattr(result, "model", None)
    if not isinstance(provider, str) or not provider.strip():
        raise _AssetValidationError(
            "image provider returned an invalid provider name"
        )
    if not isinstance(model, str) or not model.strip():
        raise _AssetValidationError(
            "image provider returned an invalid image identifier"
        )
    if expected_provider is not None and (
        provider != expected_provider or model != expected_model
    ):
        raise _AssetValidationError(
            "image provider identity changed during the asset batch"
        )

    with _open_staged_output(staging_path) as source:
        actual_hash = directory.atomic_write(
            target.output_path,
            lambda destination: _copy_staged_output(
                source,
                destination,
                claimed_hash,
            ),
        )
    return (
        {"output_path": target.output_path, "sha256": actual_hash},
        provider,
        model,
    )


def _sha256_stream(source: BinaryIO) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _discard_manifest(directory: _BoundDirectory) -> None:
    directory.unlink(_MANIFEST_NAME, missing_ok=True)


def _atomic_write_manifest(
    directory: _BoundDirectory,
    manifest: AssetManifest,
) -> None:
    payload = json.dumps(
        {
            "reviewed_hash": manifest.reviewed_hash,
            "plan_hash": manifest.plan_hash,
            "provider": manifest.provider,
            "model": manifest.model,
            "assets": list(manifest.assets),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    directory.atomic_write_text(_MANIFEST_NAME, payload)


def _completed_result(manifest: AssetManifest, *, reused: bool) -> dict[str, Any]:
    return {
        "ok": True,
        "status": "completed",
        "reused": reused,
        "reviewed_hash": manifest.reviewed_hash,
        "plan_hash": manifest.plan_hash,
        "provider": manifest.provider,
        "model": manifest.model,
        "assets": [dict(asset) for asset in manifest.assets],
    }


ContentTools.prepare_article_review.__coworker_schema__ = _PREPARE_ARTICLE_REVIEW_SCHEMA
ContentTools.prepare_article_review.__aisuite_tool_metadata__ = ai.ToolMetadata(
    name="prepare_article_review",
    category="content-review",
    risk_level="medium",
    capabilities=["article-review"],
    requires_approval=True,
)
ContentTools.generate_article_assets.__coworker_schema__ = (
    _GENERATE_ARTICLE_ASSETS_SCHEMA
)
ContentTools.generate_article_assets.__aisuite_tool_metadata__ = ai.ToolMetadata(
    name="generate_article_assets",
    category="content-generation",
    risk_level="medium",
    capabilities=["article-image-generation"],
    requires_approval=True,
)


def make_content_tools(
    roots: RootSource,
    image_provider: ImageProviderSource,
) -> ContentTools:
    """Bind content tools to allowed roots and a direct or lazily built provider."""

    return ContentTools(roots=roots, image_provider=image_provider)


__all__ = ["ContentTools", "ReviewChangedError", "make_content_tools"]
