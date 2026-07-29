from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from coworker.content.images import AssetPlanError
from coworker.image_generation import (
    ImageGenerationError,
    ImageRequest,
    ImageResult,
)

ARTICLE = """---
title: 文枢内容流水线
author: 文枢
summary: 先审文字，再生成配图。
sources:
  - https://example.com/source
---
# 第一节

这是需要审阅的正文。
"""

VALID_COVER_REQUEST = {
    "prompt": "冷色调知识网络，中心留出标题空间",
    "output_path": "cover.png",
    "aspect_ratio": "2.35:1",
}
VALID_ILLUSTRATION_PLAN = [
    {
        "heading": "第一节",
        "prompt": "知识节点之间的连接示意图",
        "output_path": "images/section-1.png",
        "aspect_ratio": "16:9",
    }
]


def write_article(path: Path, text: str = ARTICLE) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


class SpyImageProvider:
    model = "gpt-image-2"

    def __init__(self, *, fail_on: int | None = None) -> None:
        self.calls: list[ImageRequest] = []
        self.fail_on = fail_on

    async def generate(self, request: ImageRequest) -> ImageResult:
        self.calls.append(request)
        if self.fail_on == len(self.calls):
            raise ImageGenerationError("controlled image failure")
        payload = f"generated:{request.output_path.name}".encode()
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(payload)
        return ImageResult(
            path=request.output_path,
            provider="openai",
            model=self.model,
            sha256=hashlib.sha256(payload).hexdigest(),
        )


class ParentSwapImageProvider(SpyImageProvider):
    def __init__(self, article_directory: Path, outside_directory: Path) -> None:
        super().__init__()
        self.article_directory = article_directory
        self.outside_directory = outside_directory

    async def generate(self, request: ImageRequest) -> ImageResult:
        result = await super().generate(request)
        if len(self.calls) == 2:
            (self.article_directory / "images").symlink_to(
                self.outside_directory,
                target_is_directory=True,
            )
        return result


def make_tools(tmp_path: Path, provider):
    from coworker.content.tools import make_content_tools

    return make_content_tools([tmp_path], image_provider=provider)


def test_prepare_article_review_returns_review_artifact_and_hash(tmp_path: Path) -> None:
    tools = make_tools(tmp_path, SpyImageProvider())
    article_path = write_article(tmp_path / "article.md")

    result = tools.prepare_article_review(str(article_path))

    assert result["title"] == "文枢内容流水线"
    assert result["summary"] == "先审文字，再生成配图。"
    assert result["article_path"] == str(article_path.resolve())
    assert result["preview_path"] == str((tmp_path / "review.html").resolve())
    assert len(result["reviewed_hash"]) == 64
    assert (tmp_path / "review.html").is_file()


@pytest.mark.asyncio
async def test_changed_article_rejects_before_provider_call(tmp_path: Path) -> None:
    from coworker.content.tools import ReviewChangedError

    spy = SpyImageProvider()
    tools = make_tools(tmp_path, spy)
    article_path = write_article(tmp_path / "article.md")
    review = tools.prepare_article_review(str(article_path))
    article_path.write_text(ARTICLE + "\n修改", encoding="utf-8")

    with pytest.raises(ReviewChangedError, match="changed"):
        await tools.generate_article_assets(
            str(article_path),
            review["reviewed_hash"],
            VALID_COVER_REQUEST,
            VALID_ILLUSTRATION_PLAN,
        )

    assert spy.calls == []
    assert not (tmp_path / "assets.manifest.json").exists()


@pytest.mark.asyncio
async def test_wrong_review_hash_rejects_before_lazy_provider_build(tmp_path: Path) -> None:
    from coworker.content.tools import ReviewChangedError, make_content_tools

    spy = SpyImageProvider()
    built = []

    def provider_factory():
        built.append(True)
        return spy

    article_path = write_article(tmp_path / "article.md")
    tools = make_content_tools([tmp_path], image_provider=provider_factory)

    with pytest.raises(ReviewChangedError, match="hash"):
        await tools.generate_article_assets(
            str(article_path),
            "0" * 64,
            VALID_COVER_REQUEST,
            VALID_ILLUSTRATION_PLAN,
        )

    assert built == []
    assert spy.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cover", "illustrations"),
    [
        ({**VALID_COVER_REQUEST, "output_path": "../cover.png"}, VALID_ILLUSTRATION_PLAN),
        (
            VALID_COVER_REQUEST,
            [
                {**VALID_ILLUSTRATION_PLAN[0], "output_path": "cover.png"},
            ],
        ),
        (
            VALID_COVER_REQUEST,
            [
                {
                    "heading": f"第 {index} 节",
                    "prompt": "图",
                    "output_path": f"images/{index}.png",
                }
                for index in range(9)
            ],
        ),
    ],
)
async def test_invalid_asset_plan_rejects_before_provider_call(
    tmp_path: Path,
    cover: dict,
    illustrations: list[dict],
) -> None:
    spy = SpyImageProvider()
    tools = make_tools(tmp_path, spy)
    article_path = write_article(tmp_path / "article.md")
    review = tools.prepare_article_review(str(article_path))

    with pytest.raises(AssetPlanError):
        await tools.generate_article_assets(
            str(article_path),
            review["reviewed_hash"],
            cover,
            illustrations,
        )

    assert spy.calls == []


@pytest.mark.asyncio
async def test_existing_symlink_output_escape_rejects_before_provider_call(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside-assets"
    outside.mkdir(exist_ok=True)
    (tmp_path / "images").symlink_to(outside, target_is_directory=True)
    spy = SpyImageProvider()
    tools = make_tools(tmp_path, spy)
    article_path = write_article(tmp_path / "article.md")
    review = tools.prepare_article_review(str(article_path))

    with pytest.raises(ValueError, match="outside"):
        await tools.generate_article_assets(
            str(article_path),
            review["reviewed_hash"],
            VALID_COVER_REQUEST,
            VALID_ILLUSTRATION_PLAN,
        )

    assert spy.calls == []


@pytest.mark.asyncio
async def test_success_writes_prompt_free_manifest_after_serial_generation(
    tmp_path: Path,
) -> None:
    spy = SpyImageProvider()
    tools = make_tools(tmp_path, spy)
    article_path = write_article(tmp_path / "article.md")
    review = tools.prepare_article_review(str(article_path))

    result = await tools.generate_article_assets(
        str(article_path),
        review["reviewed_hash"],
        VALID_COVER_REQUEST,
        VALID_ILLUSTRATION_PLAN,
    )

    final_paths = [
        tmp_path / "cover.png",
        tmp_path / "images/section-1.png",
    ]
    assert [call.output_path.name for call in spy.calls] == [
        "cover.png",
        "section-1.png",
    ]
    assert all(
        not call.output_path.is_relative_to(tmp_path.resolve()) for call in spy.calls
    )
    assert result["ok"] is True
    assert result["status"] == "completed"
    assert result["reused"] is False
    assert result["provider"] == "openai"
    assert result["model"] == "gpt-image-2"
    assert len(result["plan_hash"]) == 64
    assert result["assets"] == [
        {
            "output_path": "cover.png",
            "sha256": hashlib.sha256(final_paths[0].read_bytes()).hexdigest(),
        },
        {
            "output_path": "images/section-1.png",
            "sha256": hashlib.sha256(final_paths[1].read_bytes()).hexdigest(),
        },
    ]
    assert [path.read_bytes() for path in final_paths] == [
        b"generated:cover.png",
        b"generated:section-1.png",
    ]
    assert all(
        not call.output_path.exists() and not call.output_path.parent.exists()
        for call in spy.calls
    )

    manifest_path = tmp_path / "assets.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest == {
        "reviewed_hash": review["reviewed_hash"],
        "plan_hash": result["plan_hash"],
        "provider": "openai",
        "model": "gpt-image-2",
        "assets": result["assets"],
    }
    assert "prompt" not in manifest_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_matching_verified_manifest_reuses_assets_without_provider_call(
    tmp_path: Path,
) -> None:
    spy = SpyImageProvider()
    tools = make_tools(tmp_path, spy)
    article_path = write_article(tmp_path / "article.md")
    review = tools.prepare_article_review(str(article_path))

    first = await tools.generate_article_assets(
        str(article_path),
        review["reviewed_hash"],
        VALID_COVER_REQUEST,
        VALID_ILLUSTRATION_PLAN,
    )
    spy.calls.clear()
    second = await tools.generate_article_assets(
        str(article_path),
        review["reviewed_hash"],
        VALID_COVER_REQUEST,
        VALID_ILLUSTRATION_PLAN,
    )

    assert spy.calls == []
    assert second == {**first, "reused": True}


@pytest.mark.asyncio
async def test_tampered_manifest_asset_is_not_reused(tmp_path: Path) -> None:
    spy = SpyImageProvider()
    tools = make_tools(tmp_path, spy)
    article_path = write_article(tmp_path / "article.md")
    review = tools.prepare_article_review(str(article_path))
    await tools.generate_article_assets(
        str(article_path),
        review["reviewed_hash"],
        VALID_COVER_REQUEST,
        VALID_ILLUSTRATION_PLAN,
    )
    spy.calls.clear()
    (tmp_path / "cover.png").write_bytes(b"tampered")

    result = await tools.generate_article_assets(
        str(article_path),
        review["reviewed_hash"],
        VALID_COVER_REQUEST,
        VALID_ILLUSTRATION_PLAN,
    )

    assert result["reused"] is False
    assert len(spy.calls) == 2


@pytest.mark.asyncio
async def test_article_parent_swap_is_bound_before_generation_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coworker.content import review as review_module
    from coworker.content.paths import ContentPathError

    allowed = tmp_path / "allowed"
    article_directory = allowed / "draft"
    article_directory.mkdir(parents=True)
    article_path = write_article(article_directory / "article.md")
    outside = tmp_path / "outside"
    outside.mkdir()
    write_article(outside / "article.md", ARTICLE.replace("文枢内容流水线", "外部机密"))

    spy = SpyImageProvider()
    tools = make_tools(allowed, spy)
    review = tools.prepare_article_review(str(article_path))
    validated_directory = allowed / "validated-draft"
    original_read_text = review_module._BoundDirectory.read_text
    swapped = False

    def swap_then_read(directory, name, expected=None):
        nonlocal swapped
        if name == article_path.name and not swapped:
            article_directory.rename(validated_directory)
            article_directory.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_read_text(directory, name, expected)

    monkeypatch.setattr(review_module._BoundDirectory, "read_text", swap_then_read)

    with pytest.raises(ContentPathError):
        await tools.generate_article_assets(
            str(article_path),
            review["reviewed_hash"],
            VALID_COVER_REQUEST,
            VALID_ILLUSTRATION_PLAN,
        )

    assert swapped
    assert spy.calls == []
    assert not (outside / "cover.png").exists()
    assert not (outside / "assets.manifest.json").exists()


@pytest.mark.asyncio
async def test_parent_symlink_swap_cannot_escape_staging_commit(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside-assets"
    outside.mkdir()
    provider = ParentSwapImageProvider(tmp_path, outside)
    tools = make_tools(tmp_path, provider)
    article_path = write_article(tmp_path / "article.md")
    review = tools.prepare_article_review(str(article_path))

    result = await tools.generate_article_assets(
        str(article_path),
        review["reviewed_hash"],
        VALID_COVER_REQUEST,
        VALID_ILLUSTRATION_PLAN,
    )

    cover_path = tmp_path / "cover.png"
    assert result["ok"] is False
    assert result["status"] == "partial"
    assert result["assets"] == [
        {
            "output_path": "cover.png",
            "sha256": hashlib.sha256(cover_path.read_bytes()).hexdigest(),
        }
    ]
    assert result["failed_asset"]["output_path"] == "images/section-1.png"
    assert result["failed_asset"]["error_type"] == "ContentPathError"
    assert list(outside.iterdir()) == []
    assert all(
        not call.output_path.exists() and not call.output_path.parent.exists()
        for call in provider.calls
    )
    assert not (tmp_path / "assets.manifest.json").exists()


@pytest.mark.asyncio
async def test_provider_failure_stops_batch_and_reports_generated_and_pending(
    tmp_path: Path,
) -> None:
    spy = SpyImageProvider(fail_on=2)
    tools = make_tools(tmp_path, spy)
    article_path = write_article(tmp_path / "article.md")
    review = tools.prepare_article_review(str(article_path))
    illustrations = [
        VALID_ILLUSTRATION_PLAN[0],
        {
            "heading": "第二节",
            "prompt": "第二张图",
            "output_path": "images/section-2.png",
        },
    ]

    result = await tools.generate_article_assets(
        str(article_path),
        review["reviewed_hash"],
        VALID_COVER_REQUEST,
        illustrations,
    )

    assert len(spy.calls) == 2
    assert result["ok"] is False
    assert result["status"] == "partial"
    assert result["assets"] == [
        {
            "output_path": "cover.png",
            "sha256": hashlib.sha256((tmp_path / "cover.png").read_bytes()).hexdigest(),
        }
    ]
    assert result["failed_asset"] == {
        "output_path": "images/section-1.png",
        "error_type": "ImageGenerationError",
        "error": "controlled image failure",
    }
    assert result["pending_assets"] == ["images/section-2.png"]
    assert not (tmp_path / "assets.manifest.json").exists()


def test_content_tool_schemas_and_metadata_contain_no_provider_credentials(
    tmp_path: Path,
) -> None:
    from coworker.content.tools import make_content_tools
    from coworker.tools import ToolRegistry

    tools = make_content_tools([tmp_path], image_provider=SpyImageProvider())
    registry = ToolRegistry()
    registry.register(tools.prepare_article_review)
    registry.register(tools.generate_article_assets)

    prepare = registry.get("prepare_article_review")
    generate = registry.get("generate_article_assets")
    assert prepare is not None and generate is not None
    assert set(prepare.schema["function"]["parameters"]["properties"]) == {
        "article_path"
    }
    assert set(generate.schema["function"]["parameters"]["properties"]) == {
        "article_path",
        "reviewed_hash",
        "cover_request",
        "illustration_plan",
    }
    assert generate.metadata.category == "content-generation"
    assert generate.metadata.risk_level == "medium"
    assert generate.metadata.requires_approval is True
    assert all(
        secret not in repr(generate.schema)
        for secret in ("api_key", "base_url", "image_model")
    )
