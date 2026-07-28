from __future__ import annotations

import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import httpx
import pytest

import coworker.content.review as review_module
from coworker.content import article_text_hash, load_article
from coworker.content.paths import ContentPathError, resolve_in_roots
from coworker.content.review import ArticleReview, prepare_article_review_file


ARTICLE = """---
title: 文枢项目介绍
author: 作者
summary: 面向中文内容工作的本地 AI Worker
coverImage: https://assets.example/cover.png
sourceUrl: https://example.com/source
---

# 正文标题

第一段完整正文。

![不应出现的图片替代文字](https://cdn.example/hero.png "远程图片")

第二段完整正文。
"""


def write_article(path: Path, text: str = ARTICLE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_outside_dir(root: Path) -> Path:
    outside = root.parent / f"{root.name}-outside"
    outside.mkdir()
    return outside


def test_resolve_in_roots_accepts_absolute_and_cwd_relative_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    article_path = write_article(tmp_path / "article.md")

    assert resolve_in_roots(article_path, [tmp_path], must_exist=True) == article_path.resolve()

    monkeypatch.chdir(tmp_path)
    assert resolve_in_roots("article.md", [Path(".")], must_exist=True) == article_path.resolve()


def test_resolve_in_roots_rejects_empty_roots(tmp_path: Path) -> None:
    article_path = write_article(tmp_path / "article.md")

    with pytest.raises(ContentPathError, match="root"):
        resolve_in_roots(article_path, [], must_exist=True)


def test_resolve_in_roots_honors_must_exist(tmp_path: Path) -> None:
    missing = tmp_path / "drafts" / "article.md"

    with pytest.raises(ContentPathError):
        resolve_in_roots(missing, [tmp_path], must_exist=True)

    assert resolve_in_roots(missing, [tmp_path], must_exist=False) == missing.resolve()


def test_resolve_in_roots_allows_normalized_parent_segment_inside_root(
    tmp_path: Path,
) -> None:
    article_path = write_article(tmp_path / "article.md")
    (tmp_path / "nested").mkdir()

    candidate = tmp_path / "nested" / ".." / "article.md"
    assert resolve_in_roots(candidate, [tmp_path], must_exist=True) == article_path.resolve()


def test_resolve_in_roots_rejects_parent_escape_and_prefix_collision(tmp_path: Path) -> None:
    outside = make_outside_dir(tmp_path)
    outside_article = write_article(outside / "article.md")
    traversing_path = tmp_path / ".." / outside.name / outside_article.name

    for candidate in (traversing_path, outside_article):
        with pytest.raises(ContentPathError):
            resolve_in_roots(candidate, [tmp_path], must_exist=True)


def test_resolve_in_roots_rejects_symlink_file_escape(tmp_path: Path) -> None:
    outside_article = write_article(make_outside_dir(tmp_path) / "secret.md")
    escape = tmp_path / "escape.md"
    escape.symlink_to(outside_article)

    with pytest.raises(ContentPathError):
        resolve_in_roots(escape, [tmp_path], must_exist=True)


def test_resolve_in_roots_rejects_symlink_parent_escape(tmp_path: Path) -> None:
    outside = make_outside_dir(tmp_path)
    outside_article = write_article(outside / "article.md")
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ContentPathError):
        resolve_in_roots(linked_parent / outside_article.name, [tmp_path], must_exist=True)


def test_review_renders_complete_text_without_images_or_remote_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    article_path = write_article(tmp_path / "article.md")
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: pytest.fail("network forbidden"),
    )

    result = prepare_article_review_file(article_path, [tmp_path])
    html = result.preview_path.read_text(encoding="utf-8")

    assert result.title == "文枢项目介绍"
    assert result.summary == "面向中文内容工作的本地 AI Worker"
    assert result.article_path == article_path.resolve()
    assert result.preview_path == (tmp_path / "review.html").resolve()
    assert "文枢项目介绍" in html
    assert "面向中文内容工作的本地 AI Worker" in html
    assert "正文标题" in html
    assert "第一段完整正文。" in html
    assert "第二段完整正文。" in html
    assert "不应出现的图片替代文字" not in html
    assert "cdn.example" not in html
    assert "<img" not in html.lower()
    assert "<link" not in html.lower()
    assert "<script" not in html.lower()
    assert "@import" not in html.lower()
    assert "url(" not in html.lower()
    assert '<meta charset="utf-8">' in html.lower()
    assert "<style>" in html.lower()
    assert result.reviewed_hash == article_text_hash(load_article(article_path))


def test_review_disables_raw_html_image_injection(tmp_path: Path) -> None:
    article_path = write_article(
        tmp_path / "article.md",
        """---
title: 安全审阅
summary: 完整摘要
---

<img src="https://evil.example/tracker.png" onerror="alert(1)">

<p>仍要显示的正文</p>
""",
    )

    result = prepare_article_review_file(article_path, [tmp_path])
    html = result.preview_path.read_text(encoding="utf-8")

    assert "<img" not in html.lower()
    assert "仍要显示的正文" in html
    assert "&lt;img" in html.lower()


def test_article_review_is_frozen(tmp_path: Path) -> None:
    result = prepare_article_review_file(write_article(tmp_path / "article.md"), [tmp_path])

    assert isinstance(result, ArticleReview)
    with pytest.raises(FrozenInstanceError):
        result.title = "篡改"  # type: ignore[misc]


def test_review_atomically_replaces_fixed_preview_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    article_path = write_article(tmp_path / "article.md")
    preview_path = tmp_path / "review.html"
    preview_path.write_text("旧预览", encoding="utf-8")
    real_replace = os.replace
    replacements: list[tuple[Path, Path]] = []

    def track_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        source_path = Path(source)
        target_path = Path(target)
        assert source_path.parent == preview_path.parent
        assert source_path != target_path
        assert source_path.is_file()
        replacements.append((source_path, target_path))
        real_replace(source_path, target_path)

    monkeypatch.setattr(review_module.os, "replace", track_replace)

    result = prepare_article_review_file(article_path, [tmp_path])

    assert replacements == [(replacements[0][0], preview_path.resolve())]
    assert result.preview_path == preview_path.resolve()
    assert "旧预览" not in preview_path.read_text(encoding="utf-8")
    assert not replacements[0][0].exists()


def test_review_cleans_temporary_file_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    article_path = write_article(tmp_path / "article.md")
    preview_path = tmp_path / "review.html"
    preview_path.write_text("旧预览", encoding="utf-8")
    attempted_sources: list[Path] = []

    def fail_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        attempted_sources.append(Path(source))
        raise OSError("replace failed")

    monkeypatch.setattr(review_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        prepare_article_review_file(article_path, [tmp_path])

    assert len(attempted_sources) == 1
    assert not attempted_sources[0].exists()
    assert preview_path.read_text(encoding="utf-8") == "旧预览"


def test_review_rejects_source_escape_before_read_write_or_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside_article = write_article(make_outside_dir(tmp_path) / "secret.md")

    monkeypatch.setattr(
        review_module,
        "load_article",
        lambda *args, **kwargs: pytest.fail("source read forbidden"),
    )
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: pytest.fail("network forbidden"),
    )

    with pytest.raises(ContentPathError):
        prepare_article_review_file(outside_article, [tmp_path])

    assert not (tmp_path / "review.html").exists()


def test_review_rejects_preview_symlink_escape_before_source_read_or_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    article_path = write_article(tmp_path / "article.md")
    outside_preview = make_outside_dir(tmp_path) / "review.html"
    preview_link = tmp_path / "review.html"
    preview_link.symlink_to(outside_preview)

    monkeypatch.setattr(
        review_module,
        "load_article",
        lambda *args, **kwargs: pytest.fail("source read forbidden"),
    )

    with pytest.raises(ContentPathError):
        prepare_article_review_file(article_path, [tmp_path])

    assert preview_link.is_symlink()
    assert not outside_preview.exists()
