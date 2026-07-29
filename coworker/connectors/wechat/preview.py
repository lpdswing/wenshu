from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import warnings
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from PIL import Image, UnidentifiedImageError

from coworker.content import ArticleDocument, article_text_hash, resolve_in_roots
from coworker.content.article import parse_article_text
from coworker.content.paths import ContentPathError
from coworker.content.review import _BoundDirectory, _snapshot_path

from .hashing import preview_hash as calculate_preview_hash
from .renderer import RenderedArticle, _portable_image_path, render_wechat_article

_MANIFEST_NAME = "assets.manifest.json"
_MANIFEST_FIELDS = {"reviewed_hash", "plan_hash", "provider", "model", "assets"}
_SHA256_LENGTH = 64
_MAX_ASSETS = 9
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_MAX_IMAGE_PIXELS = 40_000_000
_MAX_IMAGE_DIMENSION = 16_384
_IMAGE_FORMATS = {
    "PNG": ("image/png", {".png"}),
    "JPEG": ("image/jpeg", {".jpg", ".jpeg"}),
    "WEBP": ("image/webp", {".webp"}),
}


class PreviewValidationError(ValueError):
    """The local article assets cannot produce a safe, reviewable draft preview."""


@dataclass(frozen=True, slots=True)
class PreviewImage:
    path: str
    sha256: str
    media_type: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class DraftPreview:
    article: ArticleDocument = field(repr=False)
    rendered: RenderedArticle = field(repr=False)
    cover_path: str
    cover_sha256: str = field(repr=False)
    images: tuple[PreviewImage, ...] = field(repr=False)
    theme: str
    color: str
    need_open_comment: bool
    only_fans_can_comment: bool
    preview_path: Path
    preview_hash: str

    @property
    def title(self) -> str:
        return self.rendered.title

    @property
    def author(self) -> str:
        return self.rendered.author

    @property
    def digest(self) -> str:
        return self.rendered.digest

    @property
    def image_refs(self) -> tuple[str, ...]:
        return tuple(image.path for image in self.images)

    @property
    def image_hashes(self) -> tuple[str, ...]:
        return tuple(image.sha256 for image in self.images)

    @property
    def image_count(self) -> int:
        return len(self.images)

    def to_tool_result(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "author": self.author,
            "digest": self.digest,
            "cover_path": self.cover_path,
            "image_count": self.image_count,
            "theme": self.theme,
            "color": self.color,
            "need_open_comment": self.need_open_comment,
            "only_fans_can_comment": self.only_fans_can_comment,
            "preview_path": str(self.preview_path),
            "preview_hash": self.preview_hash,
        }


@dataclass(frozen=True, slots=True)
class _ManifestAsset:
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class _ValidatedAsset:
    image: PreviewImage
    data: bytes | None = field(repr=False)


def _require_sha256(value: object, location: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise PreviewValidationError(f"{location} must be a lowercase sha256 hash")
    return value


def _load_manifest(directory: _BoundDirectory) -> tuple[str, tuple[_ManifestAsset, ...]]:
    try:
        manifest_text = directory.read_text(_MANIFEST_NAME)
    except FileNotFoundError as exc:
        raise PreviewValidationError("assets manifest is required for preview") from exc
    except (OSError, UnicodeError, ContentPathError) as exc:
        raise PreviewValidationError("assets manifest is not safely readable") from exc

    try:
        raw = json.loads(manifest_text)
    except json.JSONDecodeError as exc:
        raise PreviewValidationError("assets manifest is not valid JSON") from exc
    if not isinstance(raw, Mapping) or set(raw) != _MANIFEST_FIELDS:
        if isinstance(raw, Mapping) and "reviewed_hash" not in raw:
            raise PreviewValidationError("assets manifest reviewed_hash is missing")
        raise PreviewValidationError("assets manifest fields are invalid")

    reviewed_hash = _require_sha256(raw["reviewed_hash"], "assets manifest reviewed_hash")
    _require_sha256(raw["plan_hash"], "assets manifest plan_hash")
    if not isinstance(raw["provider"], str) or not raw["provider"].strip():
        raise PreviewValidationError("assets manifest provider is invalid")
    if not isinstance(raw["model"], str) or not raw["model"].strip():
        raise PreviewValidationError("assets manifest model is invalid")

    raw_assets = raw["assets"]
    if not isinstance(raw_assets, list) or len(raw_assets) > _MAX_ASSETS:
        raise PreviewValidationError("assets manifest assets are invalid")

    assets: list[_ManifestAsset] = []
    seen: set[str] = set()
    for index, raw_asset in enumerate(raw_assets):
        if not isinstance(raw_asset, Mapping) or set(raw_asset) != {"output_path", "sha256"}:
            raise PreviewValidationError(f"assets manifest assets[{index}] is invalid")
        raw_path = raw_asset["output_path"]
        if not isinstance(raw_path, str):
            raise PreviewValidationError(
                f"assets manifest assets[{index}].output_path is invalid"
            )
        try:
            portable_path = _portable_image_path(raw_path)
        except ValueError as exc:
            raise PreviewValidationError(
                f"assets manifest assets[{index}].output_path is not portable"
            ) from exc
        collision_key = portable_path.casefold()
        if collision_key in seen:
            raise PreviewValidationError("assets manifest contains duplicate image paths")
        seen.add(collision_key)
        assets.append(
            _ManifestAsset(
                path=portable_path,
                sha256=_require_sha256(
                    raw_asset["sha256"],
                    f"assets manifest assets[{index}].sha256",
                ),
            )
        )
    return reviewed_hash, tuple(assets)


def _stream_sha256(source: BinaryIO) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _validate_image(
    source: BinaryIO,
    asset: _ManifestAsset,
    *,
    retain_data: bool,
) -> _ValidatedAsset:
    source.seek(0, os.SEEK_END)
    size = source.tell()
    if size <= 0 or size > _MAX_IMAGE_BYTES:
        raise PreviewValidationError(f"image has an invalid size: {asset.path}")
    source.seek(0)

    actual_hash = _stream_sha256(source)
    if not hmac.compare_digest(actual_hash, asset.sha256):
        raise PreviewValidationError(f"image sha256 does not match manifest: {asset.path}")

    try:
        source.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as candidate:
                image_format = candidate.format
                width, height = candidate.size
                if image_format not in _IMAGE_FORMATS:
                    raise PreviewValidationError(
                        f"image type is not supported: {asset.path}"
                    )
                if (
                    width <= 0
                    or height <= 0
                    or width > _MAX_IMAGE_DIMENSION
                    or height > _MAX_IMAGE_DIMENSION
                    or width * height > _MAX_IMAGE_PIXELS
                ):
                    raise PreviewValidationError(
                        f"image dimensions are not supported: {asset.path}"
                    )
                candidate.verify()

            source.seek(0)
            with Image.open(source) as decoded:
                decoded.load()
    except PreviewValidationError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ) as exc:
        raise PreviewValidationError(f"image is damaged or invalid: {asset.path}") from exc

    media_type, suffixes = _IMAGE_FORMATS[image_format]
    if PurePosixPath(asset.path).suffix.casefold() not in suffixes:
        raise PreviewValidationError(
            f"image filename does not match its decoded type: {asset.path}"
        )

    data: bytes | None = None
    if retain_data:
        source.seek(0)
        data = source.read()
    return _ValidatedAsset(
        image=PreviewImage(
            path=asset.path,
            sha256=actual_hash,
            media_type=media_type,
            width=width,
            height=height,
        ),
        data=data,
    )


def _resolve_asset_path(
    article_directory: Path,
    relative_path: str,
    roots: tuple[str | Path, ...],
) -> None:
    logical_path = article_directory.joinpath(*PurePosixPath(relative_path).parts)
    try:
        resolved_path = resolve_in_roots(logical_path, roots, must_exist=True)
    except (ContentPathError, OSError) as exc:
        raise PreviewValidationError(f"image content path is invalid: {relative_path}") from exc
    if not resolved_path.is_relative_to(article_directory):
        raise PreviewValidationError(
            f"image must remain inside the article directory: {relative_path}"
        )


def _select_cover(
    explicit_cover: str | Path | None,
    article: ArticleDocument,
    rendered: RenderedArticle,
) -> str:
    candidate: str | Path | None = explicit_cover
    if candidate is None:
        candidate = article.meta.cover_image
    if candidate is None and rendered.image_refs:
        candidate = rendered.image_refs[0]
    if candidate is None:
        raise PreviewValidationError("a local cover image or body image is required")
    try:
        raw_path = os.fspath(candidate)
    except TypeError as exc:
        raise PreviewValidationError("cover image path must be portable") from exc
    if not isinstance(raw_path, str):
        raise PreviewValidationError("cover image path must be portable")
    try:
        return _portable_image_path(raw_path)
    except ValueError as exc:
        raise PreviewValidationError("cover image path must be local and portable") from exc


class _PlaceholderEmbedder(HTMLParser):
    def __init__(self, data_urls: Mapping[str, str]) -> None:
        super().__init__(convert_charrefs=False)
        self._data_urls = data_urls
        self._seen: set[str] = set()
        self._parts: list[str] = []

    def _start_tag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        raw_tag = self.get_starttag_text()
        if tag.casefold() != "img":
            self._parts.append(raw_tag)
            return
        image_ref = next(
            (value for name, value in attrs if name.casefold() == "data-wenshu-image"),
            None,
        )
        if image_ref is None:
            self._parts.append(raw_tag)
            return
        data_url = self._data_urls.get(image_ref)
        if data_url is None:
            raise PreviewValidationError(
                "rendered HTML contains an unknown image placeholder"
            )
        marker = f'data-wenshu-image="{html.escape(image_ref, quote=True)}"'
        if raw_tag.count(marker) != 1:
            raise PreviewValidationError("rendered HTML image placeholder is malformed")
        self._seen.add(image_ref)
        self._parts.append(raw_tag.replace(marker, f'src="{data_url}"'))

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._start_tag(tag, attrs)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._start_tag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        self._parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self._parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self._parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self._parts.append(f"<!{decl}>")

    def unknown_decl(self, data: str) -> None:
        self._parts.append(f"<![{data}]>")

    def result(self) -> str:
        missing = set(self._data_urls) - self._seen
        if missing:
            raise PreviewValidationError(
                "rendered HTML did not contain every expected image placeholder"
            )
        return "".join(self._parts)


def _embed_body_images(
    rendered_html: str,
    assets: Mapping[str, _ValidatedAsset],
    image_refs: tuple[str, ...],
) -> str:
    data_urls: dict[str, str] = {}
    for image_ref in image_refs:
        asset = assets[image_ref]
        if asset.data is None:
            raise PreviewValidationError("body image data is unavailable for preview")
        encoded = base64.b64encode(asset.data).decode("ascii")
        data_urls[image_ref] = f"data:{asset.image.media_type};base64,{encoded}"

    embedder = _PlaceholderEmbedder(data_urls)
    try:
        embedder.feed(rendered_html)
        embedder.close()
    except PreviewValidationError:
        raise
    except Exception as exc:
        raise PreviewValidationError("rendered HTML placeholders are invalid") from exc
    return embedder.result()


def _preview_document(rendered: RenderedArticle, body_html: str) -> str:
    title = html.escape(rendered.title, quote=False)
    author = html.escape(rendered.author, quote=False)
    digest = html.escape(rendered.digest, quote=False)
    byline = f'<p style="color:#666666;">{author}</p>' if author else ""
    summary = f'<p style="color:#666666;">{digest}</p>' if digest else ""
    return (
        "<!doctype html>\n"
        '<html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{title}</title></head><body>"
        f'<header><h1>{title}</h1>{byline}{summary}</header>{body_html}'
        "</body></html>\n"
    )


def prepare_preview(
    article_path: str | Path,
    theme: str,
    color: str,
    cover_path: str | Path | None,
    roots: Iterable[str | Path],
    *,
    need_open_comment: bool = False,
    only_fans_can_comment: bool = False,
) -> DraftPreview:
    """Validate local article assets and atomically write an offline final preview."""

    if type(need_open_comment) is not bool or type(only_fans_can_comment) is not bool:
        raise PreviewValidationError("comment settings must be booleans")
    if not need_open_comment:
        only_fans_can_comment = False

    root_paths = tuple(roots)
    resolved_article_path = resolve_in_roots(
        article_path,
        root_paths,
        must_exist=True,
    )
    article_directory = resolved_article_path.parent
    directory_identity = _snapshot_path(article_directory, directory=True)
    article_identity = _snapshot_path(resolved_article_path, directory=False)
    preview_path = article_directory / "article.html"
    resolved_preview_path = resolve_in_roots(preview_path, root_paths, must_exist=False)
    if resolved_preview_path.parent != article_directory:
        raise PreviewValidationError("preview output must remain in the article directory")

    with _BoundDirectory(article_directory, directory_identity) as directory:
        try:
            article_text = directory.read_text(
                resolved_article_path.name,
                article_identity,
            )
        except (OSError, UnicodeError, ContentPathError) as exc:
            raise PreviewValidationError("article is not safely readable") from exc
        article = parse_article_text(resolved_article_path, article_text)
        reviewed_hash, manifest_assets = _load_manifest(directory)
        current_hash = article_text_hash(article)
        if not hmac.compare_digest(reviewed_hash, current_hash):
            raise PreviewValidationError(
                "assets manifest reviewed_hash does not match the current article"
            )

        canonical_color = color.upper() if isinstance(color, str) else color
        rendered = render_wechat_article(article, theme, canonical_color)
        selected_cover = _select_cover(cover_path, article, rendered)
        required_paths = set(rendered.image_refs)
        required_paths.add(selected_cover)
        manifest_paths = {asset.path for asset in manifest_assets}
        missing_manifest_paths = required_paths - manifest_paths
        if missing_manifest_paths:
            missing = sorted(missing_manifest_paths)[0]
            raise PreviewValidationError(
                f"image is not recorded in assets manifest: {missing}"
            )

        validated_assets: dict[str, _ValidatedAsset] = {}
        for asset in manifest_assets:
            _resolve_asset_path(article_directory, asset.path, root_paths)
            try:
                with directory.open_binary(asset.path) as source:
                    validated_assets[asset.path] = _validate_image(
                        source,
                        asset,
                        retain_data=asset.path in rendered.image_refs,
                    )
            except PreviewValidationError:
                raise
            except (FileNotFoundError, OSError, ContentPathError) as exc:
                raise PreviewValidationError(
                    f"image is missing or unsafe: {asset.path}"
                ) from exc

        preview_images = tuple(
            validated_assets[image_ref].image for image_ref in rendered.image_refs
        )
        cover = validated_assets[selected_cover].image
        final_body = _embed_body_images(
            rendered.html,
            validated_assets,
            rendered.image_refs,
        )
        document = _preview_document(rendered, final_body)
        digest = calculate_preview_hash(
            article=article,
            rendered_html=rendered.html,
            images=tuple((image.path, image.sha256) for image in preview_images),
            cover_path=selected_cover,
            cover_sha256=cover.sha256,
            theme=theme,
            color=canonical_color,
            need_open_comment=need_open_comment,
            only_fans_can_comment=only_fans_can_comment,
        )
        directory.atomic_write_text(preview_path.name, document)

    return DraftPreview(
        article=article,
        rendered=rendered,
        cover_path=selected_cover,
        cover_sha256=cover.sha256,
        images=preview_images,
        theme=theme,
        color=canonical_color,
        need_open_comment=need_open_comment,
        only_fans_can_comment=only_fans_can_comment,
        preview_path=preview_path,
        preview_hash=digest,
    )


__all__ = [
    "DraftPreview",
    "PreviewImage",
    "PreviewValidationError",
    "prepare_preview",
]
