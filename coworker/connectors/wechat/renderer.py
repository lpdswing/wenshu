from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token

from coworker.content import ArticleDocument


_MAX_DIGEST_LENGTH = 120
_COLOR_PATTERN = re.compile(r"#[0-9A-Fa-f]{6}\Z")
_WINDOWS_RESERVED_NAME = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.I)


@dataclass(frozen=True)
class RenderedArticle:
    title: str
    author: str
    digest: str
    html: str
    image_refs: tuple[str, ...]


@dataclass(frozen=True)
class _ThemeData:
    root: str
    h2: str
    h3: str
    h4: str
    blockquote: str
    strong: str


# Theme-specific presentation lives here so supported themes cannot silently drift into
# scattered conditionals. All values are trusted constants; only the validated color is
# interpolated into them.
_THEMES: Mapping[str, _ThemeData] = {
    "default": _ThemeData(
        root=(
            "margin:0;padding:0;color:#333333;font-family:-apple-system,BlinkMacSystemFont,"
            "'Segoe UI',sans-serif;font-size:16px;line-height:1.75;word-wrap:break-word;"
        ),
        h2=(
            "margin:32px 0 16px;padding:0 0 0 12px;border-left:4px solid {color};"
            "color:#202020;font-size:22px;line-height:1.4;font-weight:700;"
        ),
        h3=(
            "margin:26px 0 12px;padding:0;color:{color};font-size:19px;"
            "line-height:1.45;font-weight:700;"
        ),
        h4=(
            "margin:22px 0 10px;padding:0;color:#333333;font-size:17px;"
            "line-height:1.5;font-weight:700;"
        ),
        blockquote=(
            "margin:18px 0;padding:12px 16px;border-left:3px solid {color};"
            "color:#666666;background:#F7F7F7;line-height:1.75;"
        ),
        strong="color:{color};font-weight:700;",
    ),
    "grace": _ThemeData(
        root=(
            "margin:0;padding:0;color:#4A443F;font-family:Georgia,'Songti SC',serif;"
            "font-size:16px;line-height:1.9;letter-spacing:0.02em;word-wrap:break-word;"
        ),
        h2=(
            "margin:34px 0 18px;padding:0 0 8px;border-bottom:1px solid {color};"
            "color:{color};font-size:23px;line-height:1.45;font-weight:600;"
        ),
        h3=(
            "margin:28px 0 14px;padding:0;color:#3F3934;font-size:19px;"
            "line-height:1.5;font-weight:600;"
        ),
        h4=(
            "margin:24px 0 12px;padding:0;color:{color};font-size:17px;"
            "line-height:1.55;font-weight:600;"
        ),
        blockquote=(
            "margin:20px 0;padding:14px 18px;border-left:2px solid {color};"
            "color:#70675F;background:#FAF8F4;line-height:1.85;"
        ),
        strong="color:#3F3934;font-weight:700;",
    ),
    "simple": _ThemeData(
        root=(
            "margin:0;padding:0;color:#222222;font-family:Arial,'PingFang SC',sans-serif;"
            "font-size:16px;line-height:1.7;word-wrap:break-word;"
        ),
        h2=(
            "margin:28px 0 14px;padding:0;color:{color};font-size:21px;"
            "line-height:1.4;font-weight:700;"
        ),
        h3=(
            "margin:24px 0 12px;padding:0;color:#222222;font-size:18px;"
            "line-height:1.45;font-weight:700;"
        ),
        h4=(
            "margin:20px 0 10px;padding:0;color:#555555;font-size:16px;"
            "line-height:1.5;font-weight:700;"
        ),
        blockquote=(
            "margin:16px 0;padding:10px 14px;border-left:2px solid {color};"
            "color:#666666;background:#F5F5F5;line-height:1.7;"
        ),
        strong="color:#222222;font-weight:700;",
    ),
    "modern": _ThemeData(
        root=(
            "margin:0;padding:0;color:#1F2937;font-family:Inter,-apple-system,"
            "BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:16px;line-height:1.75;"
            "word-wrap:break-word;"
        ),
        h2=(
            "margin:32px 0 16px;padding:8px 12px;color:#FFFFFF;background:{color};"
            "font-size:22px;line-height:1.4;font-weight:700;border-radius:4px;"
        ),
        h3=(
            "margin:26px 0 12px;padding:0 0 6px;border-bottom:2px solid {color};"
            "color:#111827;font-size:19px;line-height:1.45;font-weight:700;"
        ),
        h4=(
            "margin:22px 0 10px;padding:0;color:{color};font-size:17px;"
            "line-height:1.5;font-weight:700;"
        ),
        blockquote=(
            "margin:18px 0;padding:12px 16px;border-left:4px solid {color};"
            "color:#4B5563;background:#F3F4F6;line-height:1.75;border-radius:3px;"
        ),
        strong="color:{color};font-weight:700;",
    ),
}

_COMMON_STYLES: Mapping[str, str] = {
    "p": "margin:14px 0;padding:0;line-height:1.75;",
    "ul": "margin:14px 0;padding:0 0 0 24px;line-height:1.75;",
    "ol": "margin:14px 0;padding:0 0 0 24px;line-height:1.75;",
    "li": "margin:6px 0;padding:0;line-height:1.75;",
    "pre": (
        "margin:18px 0;padding:14px 16px;overflow-wrap:anywhere;color:#E5E7EB;"
        "background:#1F2937;border-radius:4px;line-height:1.6;white-space:pre-wrap;"
    ),
    "code": (
        "margin:0;padding:2px 5px;color:#B42318;background:#F2F4F7;"
        "font-family:Menlo,Consolas,monospace;font-size:0.9em;border-radius:3px;"
    ),
    "code_block": (
        "margin:0;padding:0;color:#E5E7EB;background:transparent;"
        "font-family:Menlo,Consolas,monospace;font-size:14px;line-height:1.6;"
        "white-space:pre-wrap;"
    ),
    "table": (
        "width:100%;margin:18px 0;border-collapse:collapse;border-spacing:0;"
        "font-size:14px;line-height:1.6;"
    ),
    "thead": "margin:0;padding:0;background:#F3F4F6;",
    "tbody": "margin:0;padding:0;",
    "tr": "margin:0;padding:0;",
    "th": "padding:8px 10px;border:1px solid #D1D5DB;text-align:left;font-weight:700;",
    "td": "padding:8px 10px;border:1px solid #D1D5DB;text-align:left;",
    "em": "font-style:italic;",
    "image": "display:block;max-width:100%;height:auto;margin:18px auto;",
    "br": "line-height:1.75;",
    "hr": "height:1px;margin:28px 0;border:0;background:#E5E7EB;",
    "reference_section": "margin:32px 0 0;padding:18px 0 0;border-top:1px solid #E5E7EB;",
    "reference_list": "margin:10px 0 0;padding:0 0 0 24px;line-height:1.65;",
    "reference_item": "margin:6px 0;padding:0;line-height:1.65;word-break:break-all;",
    "reference_link": "color:{color};text-decoration:underline;word-break:break-all;",
}

_BLOCK_OPEN = {
    "paragraph_open": ("p", "p"),
    "bullet_list_open": ("ul", "ul"),
    "ordered_list_open": ("ol", "ol"),
    "list_item_open": ("li", "li"),
    "blockquote_open": ("blockquote", "blockquote"),
    "table_open": ("table", "table"),
    "thead_open": ("thead", "thead"),
    "tbody_open": ("tbody", "tbody"),
    "tr_open": ("tr", "tr"),
    "th_open": ("th", "th"),
    "td_open": ("td", "td"),
}

_BLOCK_CLOSE = {
    "paragraph_close": "p",
    "bullet_list_close": "ul",
    "ordered_list_close": "ol",
    "list_item_close": "li",
    "blockquote_close": "blockquote",
    "table_close": "table",
    "thead_close": "thead",
    "tbody_close": "tbody",
    "tr_close": "tr",
    "th_close": "th",
    "td_close": "td",
}

_MARKDOWN = MarkdownIt("commonmark", {"html": True}).enable("table")


def _escape_attribute(value: str) -> str:
    return html.escape(value, quote=True)


def _safe_external_url(value: str) -> str | None:
    if not value or value.strip() != value:
        return None
    if any(character.isspace() or ord(character) < 0x20 for character in value):
        return None
    if "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.hostname is None or parsed.username is not None or parsed.password is not None:
        return None
    return value


def _portable_image_path(value: str | None) -> str:
    if value is None or not value or value.strip() != value:
        raise ValueError("image path must be a non-empty article-relative path")
    if any(ord(character) < 0x20 for character in value):
        raise ValueError("image path must be portable")

    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ValueError("image path must be portable") from exc
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("image path must be local and article-relative")

    decoded = unquote(value)
    if any(ord(character) < 0x20 for character in decoded):
        raise ValueError("image path must be portable")
    if unquote(decoded) != decoded:
        raise ValueError("image path must not contain nested URL encoding")
    if "\\" in decoded or any(character in '<>:"|?*' for character in decoded):
        raise ValueError("image path must be portable")
    if decoded.startswith("/") or PurePosixPath(decoded).is_absolute():
        raise ValueError("image path must be article-relative")
    if PureWindowsPath(decoded).drive:
        raise ValueError("image path must be article-relative")

    components = decoded.split("/")
    if any(component in {"", ".", ".."} for component in components):
        raise ValueError("image path must not traverse its article directory")
    if any(component.endswith((" ", ".")) for component in components):
        raise ValueError("image path must be portable")
    if any(_WINDOWS_RESERVED_NAME.fullmatch(component) for component in components):
        raise ValueError("image path must be portable")
    return decoded


class _TokenRenderer:
    def __init__(self, theme: _ThemeData, color: str) -> None:
        self._theme = theme
        self._color = color
        self._references: list[str] = []
        self._reference_numbers: dict[str, int] = {}
        self._image_refs: list[str] = []
        self._seen_images: set[str] = set()

    @property
    def image_refs(self) -> tuple[str, ...]:
        return tuple(self._image_refs)

    def _style(self, name: str) -> str:
        theme_style = getattr(self._theme, name, None)
        template = theme_style if theme_style is not None else _COMMON_STYLES[name]
        return template.format(color=self._color)

    def _open(
        self,
        tag: str,
        style_name: str,
        attrs: Sequence[tuple[str, str]] = (),
    ) -> str:
        rendered_attrs = "".join(
            f' {name}="{_escape_attribute(value)}"' for name, value in attrs
        )
        return f'<{tag} style="{_escape_attribute(self._style(style_name))}"{rendered_attrs}>'

    def _reference_number(self, url: str) -> int:
        number = self._reference_numbers.get(url)
        if number is not None:
            return number
        self._references.append(url)
        number = len(self._references)
        self._reference_numbers[url] = number
        return number

    def _render_inline(self, tokens: Sequence[Token]) -> str:
        output: list[str] = []
        links: list[int | None] = []
        for token in tokens:
            token_type = token.type
            if token_type == "text":
                output.append(html.escape(token.content, quote=False))
            elif token_type in {"softbreak", "hardbreak"}:
                output.append(self._open("br", "br")[:-1] + "/>")
            elif token_type == "code_inline":
                output.extend(
                    (
                        self._open("code", "code"),
                        html.escape(token.content, quote=False),
                        "</code>",
                    )
                )
            elif token_type == "strong_open":
                output.append(self._open("strong", "strong"))
            elif token_type == "strong_close":
                output.append("</strong>")
            elif token_type == "em_open":
                output.append(self._open("em", "em"))
            elif token_type == "em_close":
                output.append("</em>")
            elif token_type == "link_open":
                raw_url = token.attrGet("href")
                safe_url = _safe_external_url(raw_url) if isinstance(raw_url, str) else None
                links.append(
                    self._reference_number(safe_url) if safe_url is not None else None
                )
            elif token_type == "link_close":
                number = links.pop() if links else None
                if number is not None:
                    output.append(f"[{number}]")
            elif token_type == "image":
                raw_path = token.attrGet("src")
                path = _portable_image_path(raw_path if isinstance(raw_path, str) else None)
                if path not in self._seen_images:
                    self._seen_images.add(path)
                    self._image_refs.append(path)
                output.append(
                    self._open(
                        "img",
                        "image",
                        (
                            ("data-wenshu-image", path),
                            ("alt", token.content),
                        ),
                    )[:-1]
                    + "/>"
                )
            elif token_type == "html_inline":
                output.append(html.escape(token.content, quote=False))
            elif token.children:
                output.append(self._render_inline(token.children))
            elif token.content:
                output.append(html.escape(token.content, quote=False))
        return "".join(output)

    def _render_code_block(self, content: str) -> str:
        return "".join(
            (
                self._open("pre", "pre"),
                self._open("code", "code_block"),
                html.escape(content, quote=False),
                "</code></pre>",
            )
        )

    def _render_raw_html_block(self, content: str) -> str:
        escaped = html.escape(content.rstrip("\n"), quote=False)
        line_break = self._open("br", "br")[:-1] + "/>"
        return self._open("p", "p") + escaped.replace("\n", line_break) + "</p>"

    def _render_references(self) -> str:
        if not self._references:
            return ""
        output = [
            self._open("section", "reference_section"),
            self._open("h4", "h4"),
            "参考链接",
            "</h4>",
            self._open("ol", "reference_list"),
        ]
        for url in self._references:
            output.extend(
                (
                    self._open("li", "reference_item"),
                    self._open("a", "reference_link", (("href", url),)),
                    html.escape(url, quote=False),
                    "</a></li>",
                )
            )
        output.append("</ol></section>")
        return "".join(output)

    def render(self, tokens: Sequence[Token]) -> str:
        output = [self._open("section", "root")]
        suppress_h1 = False

        for token in tokens:
            token_type = token.type
            if suppress_h1:
                if token_type == "heading_close" and token.tag == "h1":
                    suppress_h1 = False
                continue

            if token_type == "heading_open":
                if token.tag == "h1":
                    suppress_h1 = True
                elif token.tag in {"h2", "h3", "h4"}:
                    output.append(self._open(token.tag, token.tag))
                else:
                    output.append(self._open("h4", "h4"))
            elif token_type == "heading_close":
                output.append(
                    f"</{token.tag}>"
                    if token.tag in {"h2", "h3", "h4"}
                    else "</h4>"
                )
            elif token_type == "inline":
                output.append(self._render_inline(token.children or ()))
            elif token_type in {"fence", "code_block"}:
                output.append(self._render_code_block(token.content))
            elif token_type == "html_block":
                output.append(self._render_raw_html_block(token.content))
            elif token_type == "hr":
                output.append(self._open("hr", "hr")[:-1] + "/>")
            elif token_type in _BLOCK_OPEN:
                if token.hidden:
                    continue
                tag, style_name = _BLOCK_OPEN[token_type]
                attrs: tuple[tuple[str, str], ...] = ()
                if token_type == "ordered_list_open":
                    start: Any = token.attrGet("start")
                    if start is not None and str(start).isdigit() and int(start) > 1:
                        attrs = (("start", str(start)),)
                output.append(self._open(tag, style_name, attrs))
            elif token_type in _BLOCK_CLOSE:
                if not token.hidden:
                    output.append(f"</{_BLOCK_CLOSE[token_type]}>")
            elif token.content:
                output.append(html.escape(token.content, quote=False))

        output.append(self._render_references())
        output.append("</section>")
        return "".join(output)


def render_wechat_article(
    article: ArticleDocument,
    theme: str = "default",
    color: str = "#07C160",
) -> RenderedArticle:
    """Render Markdown tokens into inline-styled, WeChat-safe article HTML.

    Image paths are validated and represented as placeholders only. This renderer never
    reads an image or article file and never performs network access.
    """

    if not isinstance(theme, str) or theme not in _THEMES:
        raise ValueError(f"unknown WeChat article theme: {theme!r}")
    if not isinstance(color, str) or _COLOR_PATTERN.fullmatch(color) is None:
        raise ValueError("theme color must use canonical #RRGGBB form")
    if len(article.meta.summary) > _MAX_DIGEST_LENGTH:
        raise ValueError(
            f"article digest must not exceed {_MAX_DIGEST_LENGTH} characters"
        )

    canonical_color = color.upper()
    renderer = _TokenRenderer(_THEMES[theme], canonical_color)
    tokens = _MARKDOWN.parse(article.body)
    rendered_html = renderer.render(tokens)
    return RenderedArticle(
        title=article.meta.title,
        author=article.meta.author,
        digest=article.meta.summary,
        html=rendered_html,
        image_refs=renderer.image_refs,
    )


__all__ = ["RenderedArticle", "render_wechat_article"]
