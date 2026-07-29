from __future__ import annotations

from dataclasses import FrozenInstanceError
from html.parser import HTMLParser
from pathlib import Path

import pytest

from coworker.connectors.wechat import RenderedArticle, render_wechat_article
from coworker.content import ArticleDocument, ArticleFrontmatter, load_article


RICH_BODY = """# 正文里的一级标题不会进入 HTML

## 二级标题

包含 **粗体**、*强调*、`inline()` 和 [外链](https://example.com/guide?x=1&y=2) 的段落。

### 三级标题

- 无序一
- 无序二

1. 有序一
2. 有序二

#### 四级标题

> 一段引用
>
> 引用的第二段

```python
if value < 3:
    print("safe")
```

| 名称 | 值 |
| --- | ---: |
| Alpha | 1 |
"""


class _StartTagCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.tags.append((tag, dict(attrs)))

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.tags.append((tag, dict(attrs)))


def _article(
    body: str,
    *,
    title: str = "字段标题",
    author: str = "字段作者",
    summary: str = "字段摘要",
) -> ArticleDocument:
    return ArticleDocument(
        path=Path("article.md"),
        meta=ArticleFrontmatter(title=title, author=author, summary=summary),
        body=body,
    )


def test_renders_rich_markdown_with_inline_wechat_html(tmp_path: Path) -> None:
    article_path = tmp_path / "article.md"
    article_path.write_text(
        "---\n"
        "title: 文枢项目介绍\n"
        "author: 文枢团队\n"
        "summary: 面向中文内容工作的本地 AI Worker\n"
        "---\n\n"
        + RICH_BODY,
        encoding="utf-8",
    )

    rendered = render_wechat_article(load_article(article_path), "default", "#07C160")

    assert rendered.title == "文枢项目介绍"
    assert rendered.author == "文枢团队"
    assert rendered.digest == "面向中文内容工作的本地 AI Worker"
    assert "<h1" not in rendered.html
    assert "正文里的一级标题" not in rendered.html
    for tag in (
        "<h2",
        "<h3",
        "<h4",
        "<p",
        "<ul",
        "<ol",
        "<li",
        "<blockquote",
        "<pre",
        "<code",
        "<table",
        "<thead",
        "<tbody",
        "<tr",
        "<th",
        "<td",
        "<strong",
        "<em",
    ):
        assert tag in rendered.html
    assert "if value &lt; 3:" in rendered.html
    assert "[1]" in rendered.html
    assert "参考链接" in rendered.html
    assert "https://example.com/guide?x=1&amp;y=2" in rendered.html
    assert rendered.image_refs == ()

    parsed = _StartTagCollector()
    parsed.feed(rendered.html)
    allowed_tags = {
        "a",
        "blockquote",
        "br",
        "code",
        "em",
        "h2",
        "h3",
        "h4",
        "hr",
        "img",
        "li",
        "ol",
        "p",
        "pre",
        "section",
        "strong",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "ul",
    }
    assert parsed.tags
    assert {tag for tag, _ in parsed.tags} <= allowed_tags
    for tag, attrs in parsed.tags:
        assert attrs.get("style"), tag
        assert "class" not in attrs
        assert not any(name.lower().startswith("on") for name in attrs)


def test_numbers_safe_external_links_and_deduplicates_references() -> None:
    rendered = render_wechat_article(
        _article(
            "[甲](https://example.com/a) 与 [重复](https://example.com/a)，"
            "再看 [乙](http://example.org/b)。\n"
        ),
        "default",
        "#07C160",
    )

    assert rendered.html.count("[1]") == 2
    assert rendered.html.count("[2]") == 1
    assert rendered.html.count('href="https://example.com/a"') == 1
    assert rendered.html.count('href="http://example.org/b"') == 1
    assert ">https://example.com/a</a>" in rendered.html
    assert ">http://example.org/b</a>" in rendered.html


def test_unsafe_and_relative_links_are_plain_text_not_clickable() -> None:
    rendered = render_wechat_article(
        _article(
            "[脚本](javascript:alert(1)) [数据](data:text/html,bad) "
            "[相对](guide/page.html) [邮件](mailto:test@example.com)\n"
        ),
        "default",
        "#07C160",
    )

    assert "脚本" in rendered.html
    assert "数据" in rendered.html
    assert "相对" in rendered.html
    assert "邮件" in rendered.html
    assert "href=" not in rendered.html
    assert "参考链接" not in rendered.html


def test_emits_relative_image_placeholders_and_stable_deduplicated_refs() -> None:
    rendered = render_wechat_article(
        _article(
            "![第一张](images/section-1.png)\n\n"
            "![重复](images/section-1.png)\n\n"
            "![第二张](assets/中文图.png)\n"
        ),
        "simple",
        "#07C160",
    )

    assert rendered.html.count('data-wenshu-image="images/section-1.png"') == 2
    assert rendered.html.count('data-wenshu-image="assets/中文图.png"') == 1
    assert 'alt="第一张"' in rendered.html
    assert " src=" not in rendered.html
    assert rendered.image_refs == (
        "images/section-1.png",
        "assets/中文图.png",
    )


@pytest.mark.parametrize(
    "image_markdown",
    [
        "![]()",
        "![](https://example.com/image.png)",
        "![](/var/tmp/image.png)",
        "![](C:/Users/name/image.png)",
        "![](../image.png)",
        "![](images/../image.png)",
        "![](%2e%2e/image.png)",
    ],
)
def test_rejects_missing_remote_absolute_and_traversing_image_paths(
    image_markdown: str,
) -> None:
    with pytest.raises(ValueError, match="image"):
        render_wechat_article(_article(image_markdown), "default", "#07C160")


def test_does_not_check_local_image_existence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_checked(self: Path) -> bool:
        raise AssertionError(f"renderer tried to inspect {self}")

    monkeypatch.setattr(Path, "exists", fail_if_checked)
    rendered = render_wechat_article(
        _article("![尚未落盘](images/not-created-yet.png)\n"),
        "default",
        "#07C160",
    )

    assert rendered.image_refs == ("images/not-created-yet.png",)


def test_raw_html_is_rendered_only_as_escaped_visible_text() -> None:
    rendered = render_wechat_article(
        _article(
            '<script>alert("x")</script>\n\n'
            '<img src="x" onerror="steal()" class="attack">\n\n'
            '<style>@import url("https://evil.example/x.css")</style>\n'
        ),
        "default",
        "#07C160",
    )

    assert "&lt;script&gt;" in rendered.html
    assert "&lt;img src=" in rendered.html
    assert "&lt;style&gt;" in rendered.html
    assert "<script" not in rendered.html
    assert "<style" not in rendered.html
    assert "<link" not in rendered.html

    parsed = _StartTagCollector()
    parsed.feed(rendered.html)
    assert all("class" not in attrs for _, attrs in parsed.tags)
    assert all(
        not any(name.lower().startswith("on") for name in attrs)
        for _, attrs in parsed.tags
    )


def test_all_four_themes_are_explicit_and_visibly_distinct() -> None:
    article = _article("## 主题标题\n\n> 主题引用\n")

    outputs = {
        theme: render_wechat_article(article, theme, "#12abEF").html
        for theme in ("default", "grace", "simple", "modern")
    }

    assert len(set(outputs.values())) == 4
    assert all("#12ABEF" in output for output in outputs.values())


def test_rejects_unknown_theme() -> None:
    with pytest.raises(ValueError, match="theme"):
        render_wechat_article(_article("正文\n"), "automatic", "#07C160")


@pytest.mark.parametrize(
    "color",
    ["07C160", "#fff", "#07C16", "#07C16000", "#GGGGGG", "red", "#07C16;"],
)
def test_rejects_noncanonical_theme_color(color: str) -> None:
    with pytest.raises(ValueError, match="color"):
        render_wechat_article(_article("正文\n"), "default", color)


def test_digest_accepts_120_characters_and_rejects_121() -> None:
    accepted = render_wechat_article(
        _article("正文\n", summary="摘" * 120), "default", "#07C160"
    )

    assert accepted.digest == "摘" * 120
    with pytest.raises(ValueError, match="digest"):
        render_wechat_article(
            _article("正文\n", summary="摘" * 121), "default", "#07C160"
        )


def test_rendered_article_is_frozen() -> None:
    rendered = render_wechat_article(_article("正文\n"), "default", "#07C160")

    assert isinstance(rendered, RenderedArticle)
    with pytest.raises(FrozenInstanceError):
        rendered.html = "被篡改"
