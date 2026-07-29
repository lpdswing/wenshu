from __future__ import annotations

import math
import os
import stat
import tempfile
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator
from urllib.parse import urlsplit

from PIL import Image, ImageOps, UnidentifiedImageError

from .client import WeChatClient
from .errors import WeChatError, WeChatResponseError


BODY_IMAGE_MAX_BYTES = 1 * 1024 * 1024
BODY_IMAGE_MAX_DIMENSION = 2048
COVER_IMAGE_MAX_BYTES = 10 * 1024 * 1024
COVER_IMAGE_MAX_DIMENSION = 10_000
DECOMPRESSION_BOMB_MAX_PIXELS = 40_000_000

_SUPPORTED_EXTENSIONS = frozenset({".gif", ".jpeg", ".jpg", ".png", ".webp"})
_SUPPORTED_INPUT_FORMATS = frozenset({"GIF", "JPEG", "PNG", "WEBP"})
_JPEG_QUALITIES = (90, 82, 74, 66, 58, 50, 42)
_MIN_RESIZE_FACTOR = 0.50
_MAX_RESIZE_FACTOR = 0.85


class WeChatImageError(WeChatError):
    """A safe, local validation or normalization failure."""


@dataclass(frozen=True, slots=True)
class _UploadPolicy:
    max_bytes: int
    max_dimension: int


@dataclass(frozen=True, slots=True)
class _PreparedUpload:
    path: Path
    filename: str
    content_type: str


_BODY_POLICY = _UploadPolicy(
    max_bytes=BODY_IMAGE_MAX_BYTES,
    max_dimension=BODY_IMAGE_MAX_DIMENSION,
)
_COVER_POLICY = _UploadPolicy(
    max_bytes=COVER_IMAGE_MAX_BYTES,
    max_dimension=COVER_IMAGE_MAX_DIMENSION,
)


def upload_body_image(client: WeChatClient, path: Path) -> str:
    """Upload a normalized article-body image and return its remote URL."""

    with _prepare_upload(path, _BODY_POLICY) as upload:
        with upload.path.open("rb") as media:
            payload = client.request_json(
                "POST",
                "/cgi-bin/media/uploadimg",
                files={
                    "media": (
                        upload.filename,
                        media,
                        upload.content_type,
                    )
                },
            )

    url = payload.get("url")
    if not isinstance(url, str):
        raise WeChatResponseError()
    url = url.strip()
    try:
        parsed = urlsplit(url)
        valid_host = parsed.hostname
    except ValueError:
        raise WeChatResponseError() from None
    if (
        not url
        or parsed.scheme.casefold() not in {"http", "https"}
        or not valid_host
    ):
        raise WeChatResponseError()
    return url


def upload_cover(client: WeChatClient, path: Path) -> str:
    """Upload a normalized permanent cover image and return its media id."""

    with _prepare_upload(path, _COVER_POLICY) as upload:
        with upload.path.open("rb") as media:
            payload = client.request_json(
                "POST",
                "/cgi-bin/material/add_material",
                params={"type": "image"},
                files={
                    "media": (
                        upload.filename,
                        media,
                        upload.content_type,
                    )
                },
            )

    media_id = payload.get("media_id")
    if not isinstance(media_id, str) or not media_id.strip():
        raise WeChatResponseError()
    return media_id.strip()


@contextmanager
def _prepare_upload(path: Path, policy: _UploadPolicy) -> Iterator[_PreparedUpload]:
    source_path = _validate_path(path)
    image, source_format = _load_verified_rgb(source_path)
    try:
        normalized = _fit_dimensions(image, policy.max_dimension)
        try:
            with tempfile.TemporaryDirectory(prefix="coworker-wechat-image-") as directory:
                with tempfile.NamedTemporaryFile(
                    prefix="upload-",
                    suffix=".image",
                    dir=directory,
                    delete=False,
                ) as temporary:
                    temporary_path = Path(temporary.name)
                os.chmod(temporary_path, 0o600)
                output_format = _encode_within_limit(
                    normalized,
                    source_format,
                    temporary_path,
                    policy.max_bytes,
                )
                suffix = ".png" if output_format == "PNG" else ".jpg"
                content_type = "image/png" if output_format == "PNG" else "image/jpeg"
                yield _PreparedUpload(
                    path=temporary_path,
                    filename=f"wechat-image{suffix}",
                    content_type=content_type,
                )
        finally:
            if normalized is not image:
                normalized.close()
    finally:
        image.close()


def _validate_path(path: Path) -> Path:
    try:
        candidate = Path(path)
    except (TypeError, ValueError) as error:
        raise WeChatImageError("图片文件无效") from None

    if candidate.suffix.casefold() not in _SUPPORTED_EXTENSIONS:
        raise WeChatImageError("图片格式不受支持")
    try:
        metadata = candidate.lstat()
    except OSError:
        raise WeChatImageError("图片文件无法读取") from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise WeChatImageError("图片必须是普通文件且不能是符号链接")
    if metadata.st_size <= 0:
        raise WeChatImageError("图片文件为空")
    return candidate


def _open_regular_file(path: Path) -> tuple[BinaryIO, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise WeChatImageError("图片文件无法读取") from None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
            raise WeChatImageError("图片必须是非空普通文件")
        return os.fdopen(descriptor, "rb"), metadata
    except BaseException:
        os.close(descriptor)
        raise


def _same_file(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        first.st_dev,
        first.st_ino,
        first.st_size,
        first.st_mtime_ns,
    ) == (
        second.st_dev,
        second.st_ino,
        second.st_size,
        second.st_mtime_ns,
    )


def _load_verified_rgb(path: Path) -> tuple[Image.Image, str]:
    try:
        first_file, first_metadata = _open_regular_file(path)
        with first_file:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(first_file) as probe:
                    source_format = probe.format
                    _validate_decoded_image(probe, source_format)
                    probe.verify()

        second_file, second_metadata = _open_regular_file(path)
        with second_file:
            if not _same_file(first_metadata, second_metadata):
                raise WeChatImageError("图片文件在处理期间发生变化")
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(second_file) as reopened:
                    _validate_decoded_image(reopened, reopened.format)
                    reopened.load()
                    transposed = ImageOps.exif_transpose(reopened)
                    try:
                        rgb = _flatten_to_rgb(transposed)
                    finally:
                        if transposed is not reopened:
                            transposed.close()
        return rgb, source_format
    except WeChatImageError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ):
        raise WeChatImageError("图片文件已损坏或不安全") from None


def _validate_decoded_image(image: Image.Image, image_format: str | None) -> None:
    if image_format not in _SUPPORTED_INPUT_FORMATS:
        raise WeChatImageError("图片格式不受支持")
    width, height = image.size
    if width <= 0 or height <= 0:
        raise WeChatImageError("图片像素尺寸无效")
    if width * height > DECOMPRESSION_BOMB_MAX_PIXELS:
        raise WeChatImageError("图片像素数量超过安全限制")


def _flatten_to_rgb(image: Image.Image) -> Image.Image:
    has_alpha = "A" in image.getbands() or "transparency" in image.info
    if not has_alpha:
        return image.convert("RGB")
    rgba = image.convert("RGBA")
    try:
        background = Image.new("RGBA", rgba.size, "white")
        try:
            background.alpha_composite(rgba)
            return background.convert("RGB")
        finally:
            background.close()
    finally:
        rgba.close()


def _fit_dimensions(image: Image.Image, maximum: int) -> Image.Image:
    width, height = image.size
    if width <= maximum and height <= maximum:
        return image
    scale = min(maximum / width, maximum / height)
    size = (
        max(1, math.floor(width * scale)),
        max(1, math.floor(height * scale)),
    )
    return image.resize(size, Image.Resampling.LANCZOS)


def _encode_within_limit(
    image: Image.Image,
    source_format: str,
    output_path: Path,
    max_bytes: int,
) -> str:
    if source_format in {"GIF", "PNG", "WEBP"}:
        _save_png(image, output_path)
        if _file_fits(output_path, max_bytes):
            return "PNG"

    current = image
    try:
        while True:
            last_size = 0
            for quality in _JPEG_QUALITIES:
                _save_jpeg(current, output_path, quality)
                last_size = output_path.stat().st_size
                if 0 < last_size <= max_bytes:
                    return "JPEG"

            width, height = current.size
            if width == 1 and height == 1:
                break
            estimated = math.sqrt(max_bytes / last_size) * 0.95 if last_size else _MIN_RESIZE_FACTOR
            factor = min(_MAX_RESIZE_FACTOR, max(_MIN_RESIZE_FACTOR, estimated))
            resized_size = (
                max(1, math.floor(width * factor)),
                max(1, math.floor(height * factor)),
            )
            if resized_size == current.size:
                resized_size = (max(1, width - 1), max(1, height - 1))
            resized = current.resize(resized_size, Image.Resampling.LANCZOS)
            if current is not image:
                current.close()
            current = resized
    finally:
        if current is not image:
            current.close()

    raise WeChatImageError("图片无法压缩到微信上传限制以内")


def _save_png(image: Image.Image, output_path: Path) -> None:
    try:
        image.save(
            output_path,
            format="PNG",
            optimize=True,
            compress_level=9,
        )
    except (OSError, ValueError):
        raise WeChatImageError("图片无法转换为微信支持的格式") from None


def _save_jpeg(image: Image.Image, output_path: Path, quality: int) -> None:
    try:
        image.save(
            output_path,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
            subsampling="4:2:0",
        )
    except (OSError, ValueError):
        raise WeChatImageError("图片无法转换为微信支持的格式") from None


def _file_fits(path: Path, maximum: int) -> bool:
    size = path.stat().st_size
    return 0 < size <= maximum
