from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import tempfile
import warnings
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from PIL import Image, UnidentifiedImageError

from .base import (
    ImageAuthError,
    ImageGenerationError,
    ImageGenerationProvider,
    ImageGenerationTimeout,
    ImageRateLimitError,
    ImageRequest,
    ImageResponseError,
    ImageResult,
)


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_IMAGE_MODEL = "gpt-image-2"
_MAX_RESPONSE_BYTES = 36 * 1024 * 1024
_MAX_IMAGE_BYTES = 24 * 1024 * 1024
_MAX_IMAGE_PIXELS = 40_000_000
_TIMEOUT = httpx.Timeout(120.0, connect=15.0)


class OpenAIImageProvider(ImageGenerationProvider):
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_IMAGE_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: httpx.Timeout | float = _TIMEOUT,
    ) -> None:
        key = api_key.strip() if isinstance(api_key, str) else ""
        if not key:
            raise ImageAuthError("OpenAI API key is not configured")
        model_id = model.strip() if isinstance(model, str) else ""
        if not model_id:
            raise ValueError("image model must not be blank")
        normalized_base = base_url.strip().rstrip("/") if isinstance(base_url, str) else ""
        if not normalized_base:
            normalized_base = DEFAULT_BASE_URL
        parsed_base = urlsplit(normalized_base)
        if parsed_base.scheme not in {"http", "https"} or not parsed_base.hostname:
            raise ValueError("OpenAI image base URL must be an HTTP(S) URL")
        if parsed_base.scheme == "http" and parsed_base.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("OpenAI image base URL must use HTTPS unless it is loopback")

        self._api_key = key
        self._model = model_id
        self._base_url = normalized_base
        self._transport = transport
        self._timeout = timeout

    @property
    def model(self) -> str:
        return self._model

    @property
    def base_url(self) -> str:
        return self._base_url

    def __repr__(self) -> str:
        return f"OpenAIImageProvider(model={self._model!r}, base_url={self._base_url!r})"

    async def generate(self, request: ImageRequest) -> ImageResult:
        payload = {
            "model": self._model,
            "prompt": request.prompt,
            "size": _size_for(request.aspect_ratio),
            "quality": request.quality,
        }
        endpoint = f"{self._base_url}/images/generations"
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=self._timeout,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    endpoint,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                _raise_for_status(response)
                if len(response.content) > _MAX_RESPONSE_BYTES:
                    raise ImageResponseError("image provider response is too large")
                image_bytes = await _extract_image_bytes(response, client)
        except httpx.TimeoutException as exc:
            raise ImageGenerationTimeout("image generation timed out") from exc
        except httpx.RequestError as exc:
            raise ImageGenerationError("unable to reach the image provider") from exc

        encoded = _validated_output_bytes(image_bytes, request.output_path)
        _atomic_write(request.output_path, encoded)
        return ImageResult(
            path=request.output_path,
            provider="openai",
            model=self._model,
            sha256=hashlib.sha256(encoded).hexdigest(),
        )


def _raise_for_status(response: httpx.Response) -> None:
    status = response.status_code
    if status in {401, 403}:
        raise ImageAuthError("OpenAI image credentials were rejected")
    if status == 429:
        raise ImageRateLimitError("OpenAI image rate limit reached")
    if status >= 400:
        raise ImageGenerationError(f"OpenAI image request failed with HTTP {status}")


async def _extract_image_bytes(
    response: httpx.Response, client: httpx.AsyncClient
) -> bytes:
    try:
        payload: Any = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise ImageResponseError("image provider returned invalid JSON") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise ImageResponseError("image provider response has no image data")
    item = data[0]

    encoded = item.get("b64_json")
    if isinstance(encoded, str) and encoded:
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImageResponseError("image provider returned invalid base64 image data") from exc
        if len(image_bytes) > _MAX_IMAGE_BYTES:
            raise ImageResponseError("generated image is too large")
        return image_bytes

    download_url = item.get("url")
    if isinstance(download_url, str) and download_url:
        parsed = urlsplit(download_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ImageResponseError("generated image download URL must use HTTPS")
        return await _download_image(client, download_url)

    raise ImageResponseError("image provider response has neither image bytes nor URL")


async def _download_image(client: httpx.AsyncClient, url: str) -> bytes:
    async with client.stream("GET", url, headers={"Accept": "image/*"}) as response:
        _raise_for_status(response)
        length = response.headers.get("content-length")
        if length:
            try:
                if int(length) > _MAX_IMAGE_BYTES:
                    raise ImageResponseError("generated image is too large")
            except ValueError:
                raise ImageResponseError("generated image has invalid content length") from None
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > _MAX_IMAGE_BYTES:
                raise ImageResponseError("generated image is too large")
            chunks.append(chunk)
        return b"".join(chunks)


def _size_for(aspect_ratio: str) -> str:
    try:
        width_text, height_text = aspect_ratio.split(":", 1)
        ratio = float(width_text) / float(height_text)
    except (AttributeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError("invalid image aspect ratio") from exc
    if ratio > 1.1:
        return "1536x1024"
    if ratio < 0.9:
        return "1024x1536"
    return "1024x1024"


def _validated_output_bytes(image_bytes: bytes, output_path: Path) -> bytes:
    if not image_bytes or len(image_bytes) > _MAX_IMAGE_BYTES:
        raise ImageResponseError("generated image is empty or too large")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(image_bytes)) as probe:
                probe.verify()
            with Image.open(io.BytesIO(image_bytes)) as image:
                image.load()
                if image.width * image.height > _MAX_IMAGE_PIXELS:
                    raise ImageResponseError("generated image dimensions are too large")
                suffix = output_path.suffix.casefold()
                target = io.BytesIO()
                if suffix == ".png":
                    image.save(target, format="PNG")
                elif suffix in {".jpg", ".jpeg"}:
                    image.convert("RGB").save(target, format="JPEG", quality=95)
                elif suffix == ".webp":
                    image.save(target, format="WEBP", quality=95)
                else:
                    raise ImageResponseError("image output must end in .png, .jpg, or .webp")
                encoded = target.getvalue()
    except ImageResponseError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ImageResponseError("image provider did not return a valid image") from exc
    if not encoded or len(encoded) > _MAX_IMAGE_BYTES:
        raise ImageResponseError("validated image is empty or too large")
    return encoded


def _atomic_write(output_path: Path, content: bytes) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent, delete=False
        ) as handle:
            temporary = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output_path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


__all__ = ["DEFAULT_BASE_URL", "DEFAULT_IMAGE_MODEL", "OpenAIImageProvider"]
