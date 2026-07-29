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


def swap_article_parent_after_membership_checks(
    article_path: Path,
    outside_parent: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    original_resolve = review_module.resolve_in_roots
    original_parent = article_path.parent
    validated_parent = original_parent.with_name(f"{original_parent.name}-validated")
    resolve_calls = 0

    def resolve_then_swap(
        path: str | Path,
        roots: tuple[str | Path, ...],
        must_exist: bool,
    ) -> Path:
        nonlocal resolve_calls
        resolved = original_resolve(path, roots, must_exist)
        resolve_calls += 1
        if resolve_calls == 2:
            original_parent.rename(validated_parent)
            original_parent.symlink_to(outside_parent, target_is_directory=True)
        return resolved

    monkeypatch.setattr(review_module, "resolve_in_roots", resolve_then_swap)
    return validated_parent


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


def test_review_fails_closed_without_directory_binding_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "nt":
        pytest.skip("Windows uses its native directory-handle binding")
    article_path = write_article(tmp_path / "article.md")
    monkeypatch.setattr(review_module, "_HAS_POSIX_DIR_FD", False)

    with pytest.raises(ContentPathError, match="secure content directory binding"):
        prepare_article_review_file(article_path, [tmp_path])


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
    replacements: list[tuple[str | os.PathLike[str], str | os.PathLike[str]]] = []

    def track_replace(
        source: str | os.PathLike[str],
        target: str | os.PathLike[str],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        if src_dir_fd is None:
            source_path = Path(source)
            assert source_path.parent == preview_path.parent
            assert source_path.is_file()
        else:
            assert src_dir_fd == dst_dir_fd
            assert Path(source).parent == Path(".")
            assert Path(target) == Path("review.html")
        assert source != target
        replacements.append((source, target))
        real_replace(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(review_module.os, "replace", track_replace)

    result = prepare_article_review_file(article_path, [tmp_path])

    assert len(replacements) == 1
    assert result.preview_path == preview_path.resolve()
    assert "旧预览" not in preview_path.read_text(encoding="utf-8")
    assert list(tmp_path.glob(".review.html.*.tmp")) == []


def test_review_cleans_temporary_file_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    article_path = write_article(tmp_path / "article.md")
    preview_path = tmp_path / "review.html"
    preview_path.write_text("旧预览", encoding="utf-8")
    replace_attempts = 0

    def fail_replace(
        source: str | os.PathLike[str],
        target: str | os.PathLike[str],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal replace_attempts
        replace_attempts += 1
        raise OSError("replace failed")

    monkeypatch.setattr(review_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        prepare_article_review_file(article_path, [tmp_path])

    assert replace_attempts == 1
    assert list(tmp_path.glob(".review.html.*.tmp")) == []
    assert preview_path.read_text(encoding="utf-8") == "旧预览"


def test_review_rejects_source_escape_before_read_write_or_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside_article = write_article(make_outside_dir(tmp_path) / "secret.md")

    monkeypatch.setattr(
        review_module,
        "_read_article_text",
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
        "_read_article_text",
        lambda *args, **kwargs: pytest.fail("source read forbidden"),
    )

    with pytest.raises(ContentPathError):
        prepare_article_review_file(article_path, [tmp_path])

    assert preview_link.is_symlink()
    assert not outside_preview.exists()


def test_review_rejects_parent_swap_before_reading_outside_article(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed_root = tmp_path / "allowed"
    article_path = write_article(allowed_root / "draft" / "article.md")
    outside_parent = make_outside_dir(allowed_root)
    outside_article = outside_parent / "article.md"
    outside_article.write_bytes(b"\xffoutside article must not be read")
    real_path_open = Path.open

    def reject_outside_read(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> object:
        if path.resolve(strict=False).is_relative_to(outside_parent):
            pytest.fail("outside article read")
        return real_path_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", reject_outside_read)
    validated_parent = swap_article_parent_after_membership_checks(
        article_path,
        outside_parent,
        monkeypatch,
    )

    with pytest.raises(ContentPathError):
        prepare_article_review_file(article_path, [allowed_root])

    assert outside_article.stat().st_size == len(b"\xffoutside article must not be read")
    assert (validated_parent / "article.md").read_text(encoding="utf-8") == ARTICLE
    assert not (outside_parent / "review.html").exists()


def test_review_rejects_parent_swap_before_writing_outside_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed_root = tmp_path / "allowed"
    article_path = write_article(allowed_root / "draft" / "article.md")
    outside_parent = make_outside_dir(allowed_root)
    write_article(outside_parent / "article.md", ARTICLE.replace("文枢项目介绍", "外部机密"))
    outside_preview = outside_parent / "review.html"
    outside_preview.write_text("外部预览不得修改", encoding="utf-8")
    validated_parent = swap_article_parent_after_membership_checks(
        article_path,
        outside_parent,
        monkeypatch,
    )

    with pytest.raises(ContentPathError):
        prepare_article_review_file(article_path, [allowed_root])

    assert outside_preview.read_text(encoding="utf-8") == "外部预览不得修改"
    assert list(outside_parent.glob(".review.html.*.tmp")) == []
    assert not (validated_parent / "review.html").exists()
