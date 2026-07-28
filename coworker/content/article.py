from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from .models import ArticleDocument, ArticleFrontmatter




class ArticleValidationError(ValueError):
    """An article cannot be parsed into the required content model."""


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _split_frontmatter(text: str) -> tuple[str, str]:
    normalized = _normalize_newlines(text)
    lines = normalized.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\n") != "---":
        raise ArticleValidationError("article must start with YAML frontmatter")

    closing_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.rstrip("\n") == "---"),
        None,
    )
    if closing_index is None:
        raise ArticleValidationError("article frontmatter is missing its closing delimiter")

    raw_frontmatter = "".join(lines[1:closing_index])
    body = "".join(lines[closing_index + 1 :])
    return raw_frontmatter, body


def _reject_additional_yaml_document(body: str) -> None:
    """Reject an adjacent second frontmatter-shaped YAML document.

    The first ``---`` after the opening marker is necessarily ambiguous: it is both the
    frontmatter terminator and YAML's next-document marker.  A mapping immediately after it
    and terminated by another marker is a second YAML document.  A blank line after the
    terminator establishes Markdown body context, so mapping-shaped prose followed by an
    ordinary thematic break remains valid.
    """

    if body.startswith("\n"):
        return
    lines = body.splitlines(keepends=True)
    delimiter_index = next(
        (index for index, line in enumerate(lines) if line.rstrip("\n") == "---"), None
    )
    if delimiter_index is None:
        return

    candidate = "".join(lines[:delimiter_index]).strip()
    if not candidate:
        return
    try:
        parsed = yaml.safe_load(candidate)
    except yaml.YAMLError:
        return
    if isinstance(parsed, Mapping):
        raise ArticleValidationError("frontmatter contains multiple YAML documents")


def _load_mapping(raw_frontmatter: str) -> Mapping[str, Any]:
    try:
        meta = yaml.safe_load(raw_frontmatter)
    except yaml.composer.ComposerError as exc:
        raise ArticleValidationError("frontmatter contains multiple YAML documents") from exc
    except yaml.YAMLError as exc:
        raise ArticleValidationError("invalid YAML frontmatter") from exc

    if not isinstance(meta, Mapping):
        raise ArticleValidationError("frontmatter must be a mapping of fields")
    return meta


def _required_title(meta: Mapping[str, Any]) -> str:
    value = meta.get("title")
    if value is None:
        raise ArticleValidationError("frontmatter field `title` is required")
    if not isinstance(value, str):
        raise ArticleValidationError("frontmatter field `title` must be a string")
    title = value.strip()
    if not title:
        raise ArticleValidationError("frontmatter field `title` must not be empty")
    return title


def _optional_string(meta: Mapping[str, Any], key: str, default: str | None) -> str | None:
    if key not in meta or meta[key] is None:
        return default
    value = meta[key]
    if not isinstance(value, str):
        raise ArticleValidationError(f"frontmatter field `{key}` must be a string")
    normalized = value.strip()
    if default is None and not normalized:
        return None
    return normalized


def _article_from_text(article_path: Path, text: str) -> ArticleDocument:
    raw_frontmatter, raw_body = _split_frontmatter(text)
    _reject_additional_yaml_document(raw_body)
    meta = _load_mapping(raw_frontmatter)
    body = raw_body.lstrip("\n")

    frontmatter = ArticleFrontmatter(
        title=_required_title(meta),
        author=_optional_string(meta, "author", ""),
        summary=_optional_string(meta, "summary", ""),
        cover_image=_optional_string(meta, "coverImage", None),
        source_url=_optional_string(meta, "sourceUrl", None),
    )
    return ArticleDocument(path=article_path, meta=frontmatter, body=body)


def load_article(path: str | Path) -> ArticleDocument:
    article_path = Path(path)
    return _article_from_text(
        article_path,
        article_path.read_text(encoding="utf-8"),
    )
