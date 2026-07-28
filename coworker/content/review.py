from __future__ import annotations

import html
import os
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token

from .article import load_article
from .hashing import article_text_hash
from .paths import resolve_in_roots


@dataclass(frozen=True)
class ArticleReview:
    title: str
    summary: str
    article_path: Path
    preview_path: Path
    reviewed_hash: str


def _ignore_image_token(
    tokens: Sequence[Token],
    index: int,
    options: dict[str, Any],
    env: dict[str, Any],
) -> str:
    return ""


_MARKDOWN = MarkdownIt("commonmark", {"html": False})
_MARKDOWN.renderer.rules["image"] = _ignore_image_token

_STYLE = """
html {
  color-scheme: light;
  background: #f5f3ee;
}
body {
  box-sizing: border-box;
  max-width: 760px;
  margin: 0 auto;
  padding: 48px 24px 72px;
  color: #24211d;
  background: #fffdf8;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  font-size: 17px;
  line-height: 1.8;
}
h1, h2, h3, h4, h5, h6 {
  line-height: 1.35;
}
.review-title {
  margin-bottom: 8px;
  font-size: 2rem;
}
.review-summary {
  margin-top: 0;
  color: #625c52;
}
.review-body {
  margin-top: 40px;
}
pre, code {
  font-family: ui-monospace, "SFMono-Regular", Consolas, monospace;
}
pre {
  overflow-x: auto;
  padding: 16px;
  background: #f1eee7;
}
blockquote {
  margin-left: 0;
  padding-left: 18px;
  border-left: 4px solid #cbc3b6;
  color: #514b43;
}
""".strip()


def _render_review_html(title: str, summary: str, body: str) -> str:
    escaped_title = html.escape(title)
    summary_html = (
        f'<p class="review-summary">{html.escape(summary)}</p>' if summary else ""
    )
    body_html = _MARKDOWN.render(body)
    return (
        "<!doctype html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escaped_title}</title>\n"
        f"<style>\n{_STYLE}\n</style>\n"
        "</head>\n"
        "<body>\n"
        "<article>\n"
        f'<header><h1 class="review-title">{escaped_title}</h1>{summary_html}</header>\n'
        f'<section class="review-body">\n{body_html}</section>\n'
        "</article>\n"
        "</body>\n"
        "</html>\n"
    )


def _atomic_write_text(target: Path, content: str) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
        os.replace(temporary_path, target)
    except BaseException:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def prepare_article_review_file(
    article_path: str | Path,
    roots: Iterable[str | Path],
) -> ArticleReview:
    """Validate, render, and atomically write a text-only review beside an article."""

    root_paths = tuple(roots)
    resolved_article_path = resolve_in_roots(
        article_path,
        root_paths,
        must_exist=True,
    )
    preview_path = resolved_article_path.parent / "review.html"
    resolve_in_roots(preview_path, root_paths, must_exist=False)

    article = load_article(resolved_article_path)
    reviewed_hash = article_text_hash(article)
    rendered = _render_review_html(article.meta.title, article.meta.summary, article.body)
    _atomic_write_text(preview_path, rendered)

    return ArticleReview(
        title=article.meta.title,
        summary=article.meta.summary,
        article_path=resolved_article_path,
        preview_path=preview_path,
        reviewed_hash=reviewed_hash,
    )
