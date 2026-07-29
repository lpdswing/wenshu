from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from coworker.content.tools import ReviewChangedError, make_content_tools
from coworker.image_generation import ImageRequest, ImageResult


ARTICLE = """---
title: 文枢端到端工作流
author: 文枢
summary: 先审文字，再批准生成图片。
sources:
  - https://example.com/source
---
# 第一节

这是等待用户审阅的初稿。
"""


class WorkflowImageProvider:
    def __init__(self) -> None:
        self.calls: list[ImageRequest] = []
        self.active = 0
        self.max_active = 0

    async def generate(self, request: ImageRequest) -> ImageResult:
        self.calls.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0)
            request.output_path.parent.mkdir(parents=True, exist_ok=True)
            color = "#172554" if len(self.calls) == 1 else "#0f766e"
            Image.new("RGB", (64, 36), color).save(request.output_path, format="PNG")
            payload = request.output_path.read_bytes()
            return ImageResult(
                path=request.output_path,
                provider="openai",
                model="gpt-image-2",
                sha256=hashlib.sha256(payload).hexdigest(),
            )
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_review_change_re_review_and_generate_complete_article_assets(
    tmp_path: Path,
) -> None:
    article_path = tmp_path / "article.md"
    article_path.write_text(ARTICLE, encoding="utf-8")
    provider = WorkflowImageProvider()
    tools = make_content_tools([tmp_path], image_provider=provider)

    first_review = tools.prepare_article_review(str(article_path))
    assert provider.calls == []
    assert Path(first_review["preview_path"]).is_file()

    article_path.write_text(
        ARTICLE.replace("等待用户审阅的初稿", "用户修改后的完整正文"),
        encoding="utf-8",
    )
    with pytest.raises(ReviewChangedError, match="changed"):
        await tools.generate_article_assets(
            str(article_path),
            first_review["reviewed_hash"],
            {
                "prompt": "深蓝色知识网络封面，保留标题空间",
                "output_path": "cover.png",
                "aspect_ratio": "16:9",
            },
            [
                {
                    "heading": "第一节",
                    "prompt": "青绿色内容工作流示意图",
                    "output_path": "images/section-1.png",
                    "aspect_ratio": "16:9",
                }
            ],
        )
    assert provider.calls == []
    assert not (tmp_path / "assets.manifest.json").exists()

    second_review = tools.prepare_article_review(str(article_path))
    assert second_review["reviewed_hash"] != first_review["reviewed_hash"]

    result = await tools.generate_article_assets(
        str(article_path),
        second_review["reviewed_hash"],
        {
            "prompt": "深蓝色知识网络封面，保留标题空间",
            "output_path": "cover.png",
            "aspect_ratio": "16:9",
        },
        [
            {
                "heading": "第一节",
                "prompt": "青绿色内容工作流示意图",
                "output_path": "images/section-1.png",
                "aspect_ratio": "16:9",
            }
        ],
    )

    assert result["ok"] is True
    assert result["status"] == "completed"
    assert result["reviewed_hash"] == second_review["reviewed_hash"]
    assert provider.max_active == 1
    assert len(provider.calls) == 2

    expected_files = [
        "article.md",
        "assets.manifest.json",
        "cover.png",
        "images/section-1.png",
        "review.html",
    ]
    assert sorted(
        str(path.relative_to(tmp_path))
        for path in tmp_path.rglob("*")
        if path.is_file()
    ) == expected_files

    for relative_path in ("cover.png", "images/section-1.png"):
        image_path = tmp_path / relative_path
        with Image.open(image_path) as image:
            assert image.format == "PNG"
            assert image.size == (64, 36)

    manifest_path = tmp_path / "assets.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest == {
        "reviewed_hash": second_review["reviewed_hash"],
        "plan_hash": result["plan_hash"],
        "provider": "openai",
        "model": "gpt-image-2",
        "assets": result["assets"],
    }
    assert "prompt" not in manifest_path.read_text(encoding="utf-8")
    for asset in manifest["assets"]:
        asset_path = tmp_path / asset["output_path"]
        assert hashlib.sha256(asset_path.read_bytes()).hexdigest() == asset["sha256"]
