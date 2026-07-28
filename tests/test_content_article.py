from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from coworker.content import (
    ArticleDocument,
    ArticleFrontmatter,
    ArticleValidationError,
    ImageAsset,
    article_text_hash,
    load_article,
)


ARTICLE = """---
title: 文枢项目介绍
author: 作者
summary: 面向中文内容工作的本地 AI Worker
coverImage: cover.png
sourceUrl: https://example.com
---

# 文枢项目介绍

正文内容。
"""


def write_article(path: Path, text: str) -> Path:
    path.write_bytes(text.encode("utf-8"))
    return path


def test_loads_frontmatter_and_markdown_body(tmp_path: Path) -> None:
    path = write_article(tmp_path / "article.md", ARTICLE)

    article = load_article(path)

    assert article.path == path
    assert article.meta == ArticleFrontmatter(
        title="文枢项目介绍",
        author="作者",
        summary="面向中文内容工作的本地 AI Worker",
        cover_image="cover.png",
        source_url="https://example.com",
    )
    assert article.body.startswith("# 文枢项目介绍")
    assert article.body.endswith("正文内容。\n")


def test_optional_frontmatter_fields_have_empty_defaults(tmp_path: Path) -> None:
    path = write_article(tmp_path / "article.md", "---\ntitle: 标题\n---\n\n正文\n")

    article = load_article(path)

    assert article.meta.author == ""
    assert article.meta.summary == ""
    assert article.meta.cover_image is None
    assert article.meta.source_url is None


@pytest.mark.parametrize(
    ("frontmatter", "field"),
    [
        ("author: 作者", "title"),
        ("title: '   '", "title"),
        ("title: 123", "title"),
        ("title: 标题\nauthor: [作者]", "author"),
        ("title: 标题\nsummary: false", "summary"),
        ("title: 标题\ncoverImage: 12", "coverImage"),
        ("title: 标题\nsourceUrl: [https://example.com]", "sourceUrl"),
    ],
)
def test_rejects_missing_empty_or_non_string_fields_without_body_leak(
    tmp_path: Path, frontmatter: str, field: str
) -> None:
    secret_body = "这段完整正文不得出现在错误中 UNIQUE_BODY_SECRET"
    path = write_article(
        tmp_path / "article.md", f"---\n{frontmatter}\n---\n\n{secret_body}\n"
    )

    with pytest.raises(ArticleValidationError) as exc_info:
        load_article(path)

    message = str(exc_info.value)
    assert field in message
    assert secret_body not in message
    assert "UNIQUE_BODY_SECRET" not in message


def test_rejects_non_mapping_frontmatter_without_body_leak(tmp_path: Path) -> None:
    secret_body = "正文机密 UNIQUE_MAPPING_SECRET"
    path = write_article(
        tmp_path / "article.md", f"---\n- title\n- 标题\n---\n\n{secret_body}\n"
    )

    with pytest.raises(ArticleValidationError, match="frontmatter") as exc_info:
        load_article(path)

    assert "UNIQUE_MAPPING_SECRET" not in str(exc_info.value)


def test_rejects_multiple_yaml_documents_without_body_leak(tmp_path: Path) -> None:
    secret_body = "正文机密 UNIQUE_MULTIDOC_SECRET"
    path = write_article(
        tmp_path / "article.md",
        "---\ntitle: 第一篇\n---\ntitle: 第二篇\n---\n\n" + secret_body + "\n",
    )

    with pytest.raises(ArticleValidationError, match="multiple YAML documents") as exc_info:
        load_article(path)

    assert "UNIQUE_MULTIDOC_SECRET" not in str(exc_info.value)


def test_rejects_invalid_yaml_without_body_leak(tmp_path: Path) -> None:
    secret_body = "正文机密 UNIQUE_YAML_SECRET"
    path = write_article(
        tmp_path / "article.md", f"---\ntitle: [broken\n---\n\n{secret_body}\n"
    )

    with pytest.raises(ArticleValidationError, match="invalid YAML") as exc_info:
        load_article(path)

    assert "UNIQUE_YAML_SECRET" not in str(exc_info.value)


def test_hash_is_stable_across_crlf_key_order_path_and_mtime(tmp_path: Path) -> None:
    reordered = """---
sourceUrl: https://example.com
coverImage: cover.png
summary: 面向中文内容工作的本地 AI Worker
author: 作者
title: 文枢项目介绍
---

# 文枢项目介绍

正文内容。
"""
    first_path = write_article(tmp_path / "a.md", ARTICLE)
    second_path = write_article(tmp_path / "b.md", reordered.replace("\n", "\r\n"))
    first = load_article(first_path)
    second = load_article(second_path)
    first_path.touch()

    assert article_text_hash(first) == article_text_hash(second)


def test_hash_normalizes_outer_body_whitespace() -> None:
    meta = ArticleFrontmatter(title="标题")
    first = ArticleDocument(path=Path("a.md"), meta=meta, body="正文\r\n")
    second = ArticleDocument(path=Path("b.md"), meta=meta, body="\n正文\n\n")

    assert article_text_hash(first) == article_text_hash(second)


def test_article_models_are_frozen() -> None:
    meta = ArticleFrontmatter(title="标题")
    article = ArticleDocument(path=Path("article.md"), meta=meta, body="正文\n")
    image = ImageAsset(
        path=Path("cover.png"),
        media_type="image/png",
        width=900,
        height=383,
        sha256="a" * 64,
    )

    with pytest.raises(FrozenInstanceError):
        meta.title = "新标题"
    with pytest.raises(FrozenInstanceError):
        article.body = "新正文"
    with pytest.raises(FrozenInstanceError):
        image.provider = "openai"
