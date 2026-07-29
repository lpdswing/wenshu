from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path

import pytest
from PIL import Image

from coworker.connectors.wechat import (
    DraftPreview,
    RenderedArticle,
    prepare_preview,
)
import coworker.connectors.wechat.preview as preview_module
from coworker.content import article_text_hash, load_article


def _write_png(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (3, 2), color).save(path, format="PNG")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _article_text(
    *,
    title: str = "公众号预览",
    author: str = "文枢团队",
    summary: str = "最终图文摘要",
    source_url: str = "https://example.com/source",
    cover_image: str | None = "frontmatter-cover.png",
    body: str = "## 正文\n\n第一张。\n\n![正文图](images/body.png)\n",
) -> str:
    fields = [
        "---",
        f"title: {title}",
        f"author: {author}",
        f"summary: {summary}",
        f"sourceUrl: {source_url}",
    ]
    if cover_image is not None:
        fields.append(f"coverImage: {cover_image}")
    return "\n".join((*fields, "---", "", body))


def _write_manifest(article_path: Path, asset_paths: list[str]) -> None:
    article = load_article(article_path)
    payload = {
        "reviewed_hash": article_text_hash(article),
        "plan_hash": "1" * 64,
        "provider": "fixture-provider",
        "model": "fixture-model",
        "assets": [
            {
                "output_path": relative_path,
                "sha256": _sha256(article_path.parent / relative_path),
            }
            for relative_path in asset_paths
        ],
    }
    (article_path.parent / "assets.manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _fixture(tmp_path: Path, *, directory: str = "article") -> dict[str, object]:
    article_dir = tmp_path / directory
    article_dir.mkdir()
    article_path = article_dir / "article.md"
    article_path.write_text(_article_text(), encoding="utf-8")
    _write_png(article_dir / "frontmatter-cover.png", (220, 20, 60))
    _write_png(article_dir / "explicit-cover.png", (20, 80, 220))
    _write_png(article_dir / "images/body.png", (20, 180, 80))
    _write_manifest(
        article_path,
        ["frontmatter-cover.png", "explicit-cover.png", "images/body.png"],
    )
    return {
        "article_path": article_path,
        "theme": "default",
        "color": "#07C160",
        "cover_path": "explicit-cover.png",
        "roots": [tmp_path],
        "need_open_comment": True,
        "only_fans_can_comment": False,
    }


def _refresh_manifest(arguments: dict[str, object]) -> None:
    article_path = arguments["article_path"]
    assert isinstance(article_path, Path)
    manifest_path = article_path.parent / "assets.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["reviewed_hash"] = article_text_hash(load_article(article_path))
    for asset in manifest["assets"]:
        asset["sha256"] = _sha256(article_path.parent / asset["output_path"])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


class _ReplacingStream(io.BytesIO):
    def __init__(self, initial: bytes, replacement: bytes) -> None:
        super().__init__(initial)
        self._replacement = replacement
        self._replace_on_seek = False

    def read(self, size: int = -1) -> bytes:
        data = super().read(size)
        if not data and self.tell() > 0:
            self._replace_on_seek = True
        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        if self._replace_on_seek and offset == 0 and whence == 0:
            self._replace_on_seek = False
            super().seek(0)
            self.truncate()
            super().write(self._replacement)
        return super().seek(offset, whence)


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (3, 2), color).save(output, format="PNG")
    return output.getvalue()


def test_preview_hash_and_embedded_data_share_one_image_snapshot() -> None:
    approved = _png_bytes((20, 180, 80))
    replacement = _png_bytes((220, 20, 60))
    source = _ReplacingStream(approved, replacement)
    asset = preview_module._ManifestAsset(
        path="images/body.png",
        sha256=hashlib.sha256(approved).hexdigest(),
    )

    validated = preview_module._validate_image(source, asset, retain_data=True)

    assert validated.image.sha256 == hashlib.sha256(approved).hexdigest()
    assert validated.data == approved


@pytest.mark.parametrize(
    "mutation",
    [
        "title",
        "body",
        "author",
        "summary",
        "source_url",
        "cover_bytes",
        "body_image_bytes",
        "theme",
        "color",
        "need_open_comment",
        "only_fans_can_comment",
    ],
)
def test_preview_hash_changes_for_every_submitted_input(
    tmp_path: Path,
    mutation: str,
) -> None:
    arguments = _fixture(tmp_path)
    first = prepare_preview(**arguments)
    article_path = arguments["article_path"]
    assert isinstance(article_path, Path)

    if mutation == "title":
        article_path.write_text(_article_text(title="另一个标题"), encoding="utf-8")
    elif mutation == "body":
        article_path.write_text(
            _article_text(body="## 改写正文\n\n内容已改变。\n\n![正文图](images/body.png)\n"),
            encoding="utf-8",
        )
    elif mutation == "author":
        article_path.write_text(_article_text(author="另一位作者"), encoding="utf-8")
    elif mutation == "summary":
        article_path.write_text(_article_text(summary="另一个摘要"), encoding="utf-8")
    elif mutation == "source_url":
        article_path.write_text(
            _article_text(source_url="https://example.org/changed"),
            encoding="utf-8",
        )
    elif mutation == "cover_bytes":
        _write_png(article_path.parent / "explicit-cover.png", (99, 12, 201))
    elif mutation == "body_image_bytes":
        _write_png(article_path.parent / "images/body.png", (12, 201, 99))
    elif mutation == "theme":
        arguments["theme"] = "modern"
    elif mutation == "color":
        arguments["color"] = "#123456"
    elif mutation == "need_open_comment":
        arguments["need_open_comment"] = False
    elif mutation == "only_fans_can_comment":
        arguments["only_fans_can_comment"] = True

    _refresh_manifest(arguments)
    second = prepare_preview(**arguments)
    assert second.preview_hash != first.preview_hash


def test_preview_hash_is_stable_across_repeated_calls_newlines_mtime_and_location(
    tmp_path: Path,
) -> None:
    first_arguments = _fixture(tmp_path, directory="first")
    first = prepare_preview(**first_arguments)
    repeated = prepare_preview(**first_arguments)
    assert repeated.preview_hash == first.preview_hash

    first_path = first_arguments["article_path"]
    assert isinstance(first_path, Path)
    first_path.write_bytes(first_path.read_bytes().replace(b"\n", b"\r\n"))
    os.utime(first_path, (1_900_000_000, 1_900_000_000))
    assert prepare_preview(**first_arguments).preview_hash == first.preview_hash

    second_arguments = _fixture(tmp_path, directory="second")
    assert prepare_preview(**second_arguments).preview_hash == first.preview_hash


def test_preview_embeds_all_body_images_and_returns_only_safe_summary(tmp_path: Path) -> None:
    arguments = _fixture(tmp_path)
    article_path = arguments["article_path"]
    assert isinstance(article_path, Path)
    before = {path.relative_to(article_path.parent) for path in article_path.parent.rglob("*")}

    preview = prepare_preview(**arguments)
    result = preview.to_tool_result()
    output = article_path.parent / "article.html"
    rendered = output.read_text(encoding="utf-8")
    after = {path.relative_to(article_path.parent) for path in article_path.parent.rglob("*")}

    assert isinstance(preview, DraftPreview)
    assert preview.article.path == article_path.resolve()
    assert preview.rendered.image_refs == ("images/body.png",)
    assert preview.images[0].path == "images/body.png"
    assert preview.images[0].sha256 == _sha256(article_path.parent / "images/body.png")
    assert "data:image/png;base64," in rendered
    assert "data-wenshu-image" not in rendered
    assert str(article_path.parent.resolve()) not in rendered
    assert after - before == {Path("article.html")}
    assert set(result) == {
        "title",
        "author",
        "digest",
        "cover_path",
        "image_count",
        "theme",
        "color",
        "need_open_comment",
        "only_fans_can_comment",
        "preview_path",
        "preview_hash",
    }
    assert result == {
        "title": "公众号预览",
        "author": "文枢团队",
        "digest": "最终图文摘要",
        "cover_path": "explicit-cover.png",
        "image_count": 1,
        "theme": "default",
        "color": "#07C160",
        "need_open_comment": True,
        "only_fans_can_comment": False,
        "preview_path": str(output.resolve()),
        "preview_hash": preview.preview_hash,
    }
    serialized = json.dumps(result, ensure_ascii=False)
    assert "data:image" not in serialized
    assert "<section" not in serialized
    assert "fixture-provider" not in serialized


def test_disabling_comments_normalizes_only_fans_setting(tmp_path: Path) -> None:
    arguments = _fixture(tmp_path)
    arguments["need_open_comment"] = False
    arguments["only_fans_can_comment"] = True

    normalized = prepare_preview(**arguments)
    arguments["only_fans_can_comment"] = False
    explicit_false = prepare_preview(**arguments)

    assert normalized.only_fans_can_comment is False
    assert normalized.preview_hash == explicit_false.preview_hash


@pytest.mark.parametrize("mode", ["mismatch", "missing"])
def test_rejects_manifest_reviewed_hash_mismatch_or_missing(
    tmp_path: Path,
    mode: str,
) -> None:
    arguments = _fixture(tmp_path)
    article_path = arguments["article_path"]
    assert isinstance(article_path, Path)
    manifest_path = article_path.parent / "assets.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mode == "mismatch":
        manifest["reviewed_hash"] = "0" * 64
    else:
        del manifest["reviewed_hash"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="reviewed_hash"):
        prepare_preview(**arguments)


def test_rejects_body_image_symlink_escape_from_article_directory(tmp_path: Path) -> None:
    arguments = _fixture(tmp_path)
    article_path = arguments["article_path"]
    assert isinstance(article_path, Path)
    outside = tmp_path / "outside.png"
    _write_png(outside, (1, 2, 3))
    body_image = article_path.parent / "images/body.png"
    body_image.unlink()
    body_image.symlink_to(outside)
    _refresh_manifest(arguments)

    with pytest.raises(ValueError, match="article directory|symbolic link|safe"):
        prepare_preview(**arguments)


@pytest.mark.parametrize("mode", ["missing", "damaged"])
def test_rejects_missing_or_damaged_body_image(tmp_path: Path, mode: str) -> None:
    arguments = _fixture(tmp_path)
    article_path = arguments["article_path"]
    assert isinstance(article_path, Path)
    body_image = article_path.parent / "images/body.png"
    if mode == "missing":
        body_image.unlink()
    else:
        body_image.write_bytes(b"not an image")
        _refresh_manifest(arguments)

    with pytest.raises((ValueError, FileNotFoundError), match="image|content path"):
        prepare_preview(**arguments)


@pytest.mark.parametrize("mode", ["unsupported_type", "excessive_dimensions"])
def test_rejects_unsupported_or_unreasonably_large_decoded_image(
    tmp_path: Path,
    mode: str,
) -> None:
    arguments = _fixture(tmp_path)
    article_path = arguments["article_path"]
    assert isinstance(article_path, Path)
    body_image = article_path.parent / "images/body.png"
    if mode == "unsupported_type":
        Image.new("RGB", (3, 2), (1, 2, 3)).save(body_image, format="GIF")
    else:
        Image.new("RGB", (16_385, 1), (1, 2, 3)).save(body_image, format="PNG")
    _refresh_manifest(arguments)

    with pytest.raises(ValueError, match="type|dimensions"):
        prepare_preview(**arguments)


def test_rejects_manifest_asset_hash_mismatch(tmp_path: Path) -> None:
    arguments = _fixture(tmp_path)
    article_path = arguments["article_path"]
    assert isinstance(article_path, Path)
    manifest_path = article_path.parent / "assets.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["assets"][0]["sha256"] = "f" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="sha256"):
        prepare_preview(**arguments)


def test_cover_selection_prefers_explicit_then_frontmatter_then_first_body_image(
    tmp_path: Path,
) -> None:
    arguments = _fixture(tmp_path)
    explicit = prepare_preview(**arguments)
    assert explicit.cover_path == "explicit-cover.png"

    arguments["cover_path"] = None
    frontmatter = prepare_preview(**arguments)
    assert frontmatter.cover_path == "frontmatter-cover.png"

    article_path = arguments["article_path"]
    assert isinstance(article_path, Path)
    article_path.write_text(_article_text(cover_image=None), encoding="utf-8")
    _refresh_manifest(arguments)
    fallback = prepare_preview(**arguments)
    assert fallback.cover_path == "images/body.png"


@pytest.mark.parametrize(
    "cover_image",
    ["https://example.com/cover.png", "/tmp/cover.png", "../cover.png"],
)
def test_frontmatter_cover_must_be_a_local_portable_article_path(
    tmp_path: Path,
    cover_image: str,
) -> None:
    arguments = _fixture(tmp_path)
    article_path = arguments["article_path"]
    assert isinstance(article_path, Path)
    article_path.write_text(_article_text(cover_image=cover_image), encoding="utf-8")
    _refresh_manifest(arguments)
    arguments["cover_path"] = None

    with pytest.raises(ValueError, match="cover image path"):
        prepare_preview(**arguments)


def test_rejects_preview_without_any_cover_or_body_image(tmp_path: Path) -> None:
    arguments = _fixture(tmp_path)
    article_path = arguments["article_path"]
    assert isinstance(article_path, Path)
    article_path.write_text(
        _article_text(cover_image=None, body="## 纯文字\n\n没有任何图片。\n"),
        encoding="utf-8",
    )
    manifest_path = article_path.parent / "assets.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["reviewed_hash"] = article_text_hash(load_article(article_path))
    manifest["assets"] = []
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    arguments["cover_path"] = None

    with pytest.raises(ValueError, match="cover"):
        prepare_preview(**arguments)


def test_rejects_renderer_output_with_an_unreplaced_placeholder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _fixture(tmp_path)

    def render_with_orphan_placeholder(*args: object, **kwargs: object) -> RenderedArticle:
        return RenderedArticle(
            title="公众号预览",
            author="文枢团队",
            digest="最终图文摘要",
            html='<img data-wenshu-image="images/orphan.png"/>',
            image_refs=(),
        )

    monkeypatch.setattr(
        "coworker.connectors.wechat.preview.render_wechat_article",
        render_with_orphan_placeholder,
    )
    with pytest.raises(ValueError, match="placeholder"):
        prepare_preview(**arguments)


def test_article_html_replacement_is_atomic_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _fixture(tmp_path)
    article_path = arguments["article_path"]
    assert isinstance(article_path, Path)
    output = article_path.parent / "article.html"
    output.write_text("previous complete preview", encoding="utf-8")

    def fail_replace(*args: object, **kwargs: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("coworker.content.review.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        prepare_preview(**arguments)

    assert output.read_text(encoding="utf-8") == "previous complete preview"
    assert not list(article_path.parent.glob(".coworker-*.tmp"))


def test_prepare_preview_performs_no_network_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _fixture(tmp_path)
    requests: list[object] = []

    def record_request(*args: object, **kwargs: object) -> None:
        requests.append((args, kwargs))
        raise AssertionError("preview attempted a network request")

    monkeypatch.setattr("httpx.Client.request", record_request)
    monkeypatch.setattr("httpx.AsyncClient.request", record_request)
    prepare_preview(**arguments)

    assert requests == []
