from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import httpx
import pytest

from coworker.image_generation import (
    ImageAuthError,
    ImageGenerationTimeout,
    ImageRateLimitError,
    ImageRequest,
    ImageResponseError,
    OpenAIImageProvider,
    build_image_provider,
    describe_image_provider,
)


TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.mark.asyncio
async def test_openai_images_saves_b64_response(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(TINY_PNG).decode()}]})

    provider = OpenAIImageProvider(
        api_key="sk-test",
        model="gpt-image-2",
        transport=httpx.MockTransport(handler),
    )
    output = tmp_path / "cover.png"
    result = await provider.generate(
        ImageRequest(prompt="水墨风文枢", output_path=output, aspect_ratio="2.35:1")
    )

    assert seen["url"] == "https://api.openai.com/v1/images/generations"
    assert seen["authorization"] == "Bearer sk-test"
    assert '"model":"gpt-image-2"' in str(seen["body"])
    assert '"size":"1536x1024"' in str(seen["body"])
    assert output.read_bytes().startswith(b"\x89PNG")
    assert result.path == output
    assert result.provider == "openai"
    assert result.model == "gpt-image-2"
    assert result.sha256 == hashlib.sha256(output.read_bytes()).hexdigest()


@pytest.mark.asyncio
async def test_openai_images_supports_https_url_response(tmp_path: Path) -> None:
    requests: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((str(request.url), request.headers.get("authorization")))
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example.test/image.png"}]})
        return httpx.Response(200, content=TINY_PNG, headers={"content-type": "image/png"})

    provider = OpenAIImageProvider(api_key="sk-test", transport=httpx.MockTransport(handler))
    output = tmp_path / "image.png"
    await provider.generate(ImageRequest(prompt="配图", output_path=output))

    assert requests == [
        ("https://api.openai.com/v1/images/generations", "Bearer sk-test"),
        ("https://cdn.example.test/image.png", None),
    ]
    assert output.read_bytes().startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_non_https_download_is_rejected_before_fetch(tmp_path: Path) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, json={"data": [{"url": "http://127.0.0.1/private"}]})

    provider = OpenAIImageProvider(api_key="sk-test", transport=httpx.MockTransport(handler))
    with pytest.raises(ImageResponseError, match="HTTPS"):
        await provider.generate(ImageRequest(prompt="配图", output_path=tmp_path / "image.png"))
    assert requests == ["https://api.openai.com/v1/images/generations"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_type"),
    [(401, ImageAuthError), (403, ImageAuthError), (429, ImageRateLimitError)],
)
async def test_http_errors_are_classified_and_redacted(
    tmp_path: Path, status: int, error_type: type[Exception]
) -> None:
    secret = "sk-never-leak"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=f"server echoed {secret}")

    provider = OpenAIImageProvider(api_key=secret, transport=httpx.MockTransport(handler))
    with pytest.raises(error_type) as raised:
        await provider.generate(ImageRequest(prompt="配图", output_path=tmp_path / "image.png"))
    assert secret not in str(raised.value)
    assert "server echoed" not in str(raised.value)


@pytest.mark.asyncio
async def test_timeout_is_classified_without_partial_file(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out with sk-secret", request=request)

    output = tmp_path / "image.png"
    provider = OpenAIImageProvider(api_key="sk-secret", transport=httpx.MockTransport(handler))
    with pytest.raises(ImageGenerationTimeout) as raised:
        await provider.generate(ImageRequest(prompt="配图", output_path=output))
    assert "sk-secret" not in str(raised.value)
    assert not output.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": []},
        {"data": [{}]},
        {"data": [{"b64_json": "not-base64"}]},
    ],
)
async def test_malformed_responses_are_rejected(tmp_path: Path, payload: dict) -> None:
    provider = OpenAIImageProvider(
        api_key="sk-test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )
    with pytest.raises(ImageResponseError):
        await provider.generate(ImageRequest(prompt="配图", output_path=tmp_path / "image.png"))


@pytest.mark.asyncio
async def test_invalid_image_does_not_replace_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "image.png"
    output.write_bytes(b"old")
    provider = OpenAIImageProvider(
        api_key="sk-test",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(b"not-an-image").decode()}]},
            )
        ),
    )
    with pytest.raises(ImageResponseError, match="valid image"):
        await provider.generate(ImageRequest(prompt="配图", output_path=output))
    assert output.read_bytes() == b"old"


def test_registry_reads_non_sensitive_openai_profile() -> None:
    class Secrets:
        def get(self, key: str) -> dict[str, str]:
            assert key == "provider:openai"
            return {
                "api_key": "sk-secret",
                "base_url": "https://proxy.example.test/v1/",
                "image_model": "gpt-image-custom",
            }

    provider = build_image_provider(Secrets())
    assert isinstance(provider, OpenAIImageProvider)
    assert provider.model == "gpt-image-custom"
    assert provider.base_url == "https://proxy.example.test/v1"
    assert "sk-secret" not in repr(provider)


def test_registry_describes_provider_without_constructing_it(monkeypatch) -> None:
    from coworker.image_generation import registry as image_registry

    class Secrets:
        def get(self, key: str) -> dict[str, str]:
            assert key == "provider:openai"
            return {
                "api_key": "sk-secret",
                "base_url": "https://proxy.example.test/v1/",
                "image_model": "gpt-image-custom",
            }

    monkeypatch.setattr(
        image_registry,
        "OpenAIImageProvider",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("provider must not be built for a description")
        ),
    )

    description = describe_image_provider(Secrets())

    assert description == {"provider": "OpenAI", "model": "gpt-image-custom"}
    assert "sk-secret" not in repr(description)
    assert "proxy.example.test" not in repr(description)


def test_registry_requires_key_and_known_profile() -> None:
    class EmptySecrets:
        def get(self, key: str) -> dict[str, str]:
            return {}

    with pytest.raises(ImageAuthError, match="OpenAI API key"):
        build_image_provider(EmptySecrets())
    with pytest.raises(ValueError, match="unsupported image provider"):
        build_image_provider(EmptySecrets(), profile="other")
