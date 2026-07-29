from __future__ import annotations

import hashlib
import io
import tempfile
from collections.abc import Callable
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

import httpx
import pytest
from PIL import Image

import coworker.connectors.wechat.images as wechat_images
from coworker.connectors.wechat import (
    WeChatAPIError,
    WeChatClient,
    WeChatCredentials,
    WeChatImageError,
    WeChatResponseError,
    upload_body_image,
    upload_cover,
)
from coworker.connectors.wechat.images import (
    BODY_IMAGE_MAX_BYTES,
    BODY_IMAGE_MAX_DIMENSION,
    COVER_IMAGE_MAX_BYTES,
    COVER_IMAGE_MAX_DIMENSION,
)


APP_ID = "wx-image-test"
APP_SECRET = "image-test-secret"
ACCESS_TOKEN = "image-test-token"


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> WeChatClient:
    return WeChatClient(
        WeChatCredentials(APP_ID, APP_SECRET),
        transport=httpx.MockTransport(handler),
    )


def _token_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={"access_token": ACCESS_TOKEN, "expires_in": 7200},
    )


def _multipart_media(request: httpx.Request) -> tuple[str, str, bytes]:
    message = BytesParser(policy=default).parsebytes(
        b"Content-Type: "
        + request.headers["content-type"].encode("ascii")
        + b"\r\nMIME-Version: 1.0\r\n\r\n"
        + request.content
    )
    media_parts = [
        part
        for part in message.iter_parts()
        if part.get_param("name", header="content-disposition") == "media"
    ]
    assert len(media_parts) == 1
    part = media_parts[0]
    filename = part.get_filename()
    assert filename is not None
    return filename, part.get_content_type(), part.get_payload(decode=True)


def _save_image(
    path: Path,
    image_format: str,
    *,
    size: tuple[int, int] = (8, 6),
    transparent: bool = False,
) -> Path:
    if transparent:
        image = Image.new("RGBA", size, (255, 0, 0, 0))
        image.putpixel((size[0] - 1, size[1] - 1), (0, 128, 255, 255))
        if image_format == "GIF":
            image = image.convert("P", palette=Image.Palette.ADAPTIVE)
            image.info["transparency"] = image.getpixel((0, 0))
    else:
        image = Image.new("RGB", size, (20, 80, 160))
    image.save(path, format=image_format)
    image.close()
    return path


def test_uploads_body_and_cover_to_distinct_endpoints_with_exact_multipart(tmp_path):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cgi-bin/token":
            return _token_response()
        requests.append(request)
        if request.url.path == "/cgi-bin/media/uploadimg":
            return httpx.Response(200, json={"url": "https://mmbiz.qpic.cn/body"})
        return httpx.Response(200, json={"media_id": "thumb-media-id"})

    client = _client(handler)
    try:
        body = upload_body_image(client, _save_image(tmp_path / "body.png", "PNG"))
        cover = upload_cover(client, _save_image(tmp_path / "cover.jpg", "JPEG"))
    finally:
        client.close()

    assert body == "https://mmbiz.qpic.cn/body"
    assert cover == "thumb-media-id"
    assert [(request.method, request.url.path) for request in requests] == [
        ("POST", "/cgi-bin/media/uploadimg"),
        ("POST", "/cgi-bin/material/add_material"),
    ]
    assert "type" not in requests[0].url.params
    assert requests[1].url.params["type"] == "image"
    for request in requests:
        filename, content_type, payload = _multipart_media(request)
        assert Path(filename).name == filename
        assert filename.startswith("wechat-image.")
        assert content_type in {"image/jpeg", "image/png"}
        with Image.open(io.BytesIO(payload)) as uploaded:
            uploaded.verify()


@pytest.mark.parametrize(
    ("suffix", "image_format"),
    [(".png", "PNG"), (".jpg", "JPEG"), (".jpeg", "JPEG"), (".webp", "WEBP"), (".gif", "GIF")],
)
def test_supported_inputs_are_normalized_to_openable_rgb_with_white_transparency(
    tmp_path, suffix, image_format
):
    uploaded_payload = b""
    uploaded_type = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal uploaded_payload, uploaded_type
        if request.url.path == "/cgi-bin/token":
            return _token_response()
        filename, uploaded_type, uploaded_payload = _multipart_media(request)
        assert filename.endswith(".png") or filename.endswith(".jpg")
        return httpx.Response(200, json={"url": "https://example.test/image"})

    source = _save_image(
        tmp_path / f"source{suffix}",
        image_format,
        transparent=image_format != "JPEG",
    )
    client = _client(handler)
    try:
        assert upload_body_image(client, source) == "https://example.test/image"
    finally:
        client.close()

    with Image.open(io.BytesIO(uploaded_payload)) as uploaded:
        uploaded.load()
        assert uploaded.format == ("JPEG" if uploaded_type == "image/jpeg" else "PNG")
        assert uploaded.mode == "RGB"
        red, green, blue = uploaded.getpixel((0, 0))
        if image_format == "JPEG":
            assert red < 50 and 50 <= green <= 110 and blue >= 130
        else:
            assert red >= 245 and green >= 245 and blue >= 245


def test_exif_orientation_is_transposed_before_upload(tmp_path):
    source = tmp_path / "oriented.jpg"
    image = Image.new("RGB", (2, 1), "red")
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, format="JPEG", exif=exif)
    image.close()
    uploaded_size: tuple[int, int] | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal uploaded_size
        if request.url.path == "/cgi-bin/token":
            return _token_response()
        _, _, payload = _multipart_media(request)
        with Image.open(io.BytesIO(payload)) as uploaded:
            uploaded_size = uploaded.size
        return httpx.Response(200, json={"url": "https://example.test/oriented"})

    client = _client(handler)
    try:
        upload_body_image(client, source)
    finally:
        client.close()

    assert uploaded_size == (1, 2)


@pytest.mark.parametrize(
    ("uploader", "maximum", "source_size"),
    [
        (upload_body_image, BODY_IMAGE_MAX_DIMENSION, (BODY_IMAGE_MAX_DIMENSION + 1, 1)),
        (upload_cover, COVER_IMAGE_MAX_DIMENSION, (COVER_IMAGE_MAX_DIMENSION + 1, 1)),
    ],
)
def test_pixel_dimension_boundary_is_enforced_without_upscaling(
    tmp_path, uploader, maximum, source_size
):
    uploaded_size: tuple[int, int] | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal uploaded_size
        if request.url.path == "/cgi-bin/token":
            return _token_response()
        _, _, payload = _multipart_media(request)
        with Image.open(io.BytesIO(payload)) as uploaded:
            uploaded_size = uploaded.size
        result = {"media_id": "cover-id"} if uploader is upload_cover else {"url": "https://example.test/body"}
        return httpx.Response(200, json=result)

    source = _save_image(tmp_path / "wide.png", "PNG", size=source_size)
    client = _client(handler)
    try:
        uploader(client, source)
    finally:
        client.close()

    assert max(uploaded_size or ()) == maximum


def test_body_volume_is_reduced_to_conservative_limit_and_source_is_unchanged(tmp_path):
    source = tmp_path / "noise.png"
    noise = Image.effect_noise((1400, 1400), 100).convert("RGB")
    noise.save(source, format="PNG", compress_level=0)
    noise.close()
    assert source.stat().st_size > BODY_IMAGE_MAX_BYTES
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    uploaded_payload = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal uploaded_payload
        if request.url.path == "/cgi-bin/token":
            return _token_response()
        _, _, uploaded_payload = _multipart_media(request)
        return httpx.Response(200, json={"url": "https://example.test/compressed"})

    client = _client(handler)
    try:
        upload_body_image(client, source)
    finally:
        client.close()

    assert 0 < len(uploaded_payload) <= BODY_IMAGE_MAX_BYTES
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    with Image.open(io.BytesIO(uploaded_payload)) as uploaded:
        uploaded.load()
        assert max(uploaded.size) <= BODY_IMAGE_MAX_DIMENSION


def test_declared_upload_limits_match_wechat_contract():
    assert BODY_IMAGE_MAX_BYTES == 1 * 1024 * 1024
    assert BODY_IMAGE_MAX_DIMENSION == 2048
    assert COVER_IMAGE_MAX_BYTES == 10 * 1024 * 1024
    assert COVER_IMAGE_MAX_DIMENSION == 10_000


def test_decompression_bomb_pixel_limit_is_inclusive_and_blocks_network(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(wechat_images, "DECOMPRESSION_BOMB_MAX_PIXELS", 100)
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/cgi-bin/token":
            return _token_response()
        return httpx.Response(200, json={"url": "https://example.test/safe"})

    client = _client(handler)
    try:
        safe = _save_image(tmp_path / "safe.png", "PNG", size=(10, 10))
        assert upload_body_image(client, safe) == "https://example.test/safe"
        unsafe = _save_image(tmp_path / "unsafe.png", "PNG", size=(101, 1))
        with pytest.raises(WeChatImageError, match="安全限制"):
            upload_body_image(client, unsafe)
    finally:
        client.close()

    assert paths == ["/cgi-bin/token", "/cgi-bin/media/uploadimg"]


@pytest.mark.parametrize("kind", ["empty", "damaged", "directory", "symlink", "extension"])
def test_invalid_local_inputs_are_rejected_before_any_network_request(tmp_path, kind):
    target = tmp_path / "input.png"
    if kind == "empty":
        target.touch()
    elif kind == "damaged":
        target.write_bytes(b"not an image")
    elif kind == "directory":
        target.mkdir()
    elif kind == "symlink":
        real = _save_image(tmp_path / "real.png", "PNG")
        target.symlink_to(real)
    else:
        target = _save_image(tmp_path / "input.bmp", "BMP")
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _token_response()

    client = _client(handler)
    try:
        with pytest.raises(WeChatImageError):
            upload_body_image(client, target)
    finally:
        client.close()

    assert calls == 0


@pytest.mark.parametrize("payload", [{}, {"url": ""}, {"url": "ftp://example.test/image"}, {"url": 7}])
def test_body_rejects_missing_empty_or_non_http_url(tmp_path, payload):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cgi-bin/token":
            return _token_response()
        return httpx.Response(200, json=payload)

    client = _client(handler)
    try:
        with pytest.raises(WeChatResponseError):
            upload_body_image(client, _save_image(tmp_path / "body.png", "PNG"))
    finally:
        client.close()


@pytest.mark.parametrize("payload", [{}, {"media_id": ""}, {"media_id": 7}])
def test_cover_rejects_missing_empty_or_non_string_media_id(tmp_path, payload):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cgi-bin/token":
            return _token_response()
        return httpx.Response(200, json=payload)

    client = _client(handler)
    try:
        with pytest.raises(WeChatResponseError):
            upload_cover(client, _save_image(tmp_path / "cover.png", "PNG"))
    finally:
        client.close()


def test_wechat_errcode_is_propagated_without_retry_or_later_draft_call(tmp_path):
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/cgi-bin/token":
            return _token_response()
        return httpx.Response(200, json={"errcode": 45009, "errmsg": "rate limit"})

    client = _client(handler)
    try:
        with pytest.raises(WeChatAPIError) as caught:
            upload_body_image(client, _save_image(tmp_path / "body.png", "PNG"))
    finally:
        client.close()

    assert caught.value.errcode == 45009
    assert paths == ["/cgi-bin/token", "/cgi-bin/media/uploadimg"]
    assert "/cgi-bin/draft/add" not in paths


@pytest.mark.parametrize("succeeds", [True, False])
def test_temporary_upload_files_are_private_and_cleaned_on_success_or_failure(
    tmp_path, monkeypatch, succeeds
):
    temp_root = tmp_path / "system-temp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    observed_modes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cgi-bin/token":
            return _token_response()
        candidates = [path for directory in temp_root.iterdir() for path in directory.iterdir()]
        assert len(candidates) == 1
        observed_modes.append(candidates[0].stat().st_mode & 0o777)
        if succeeds:
            return httpx.Response(200, json={"url": "https://example.test/body"})
        return httpx.Response(200, json={"errcode": 48001, "errmsg": "denied"})

    article_directory = tmp_path / "article"
    article_directory.mkdir()
    source = _save_image(article_directory / "body.png", "PNG")
    client = _client(handler)
    try:
        if succeeds:
            upload_body_image(client, source)
        else:
            with pytest.raises(WeChatAPIError):
                upload_body_image(client, source)
    finally:
        client.close()

    assert observed_modes == [0o600]
    assert list(temp_root.iterdir()) == []
