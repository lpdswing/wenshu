from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from coworker.content import ArticleDocument


def _normalized_body(body: str) -> str:
    return body.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"


def canonical_preview_inputs(
    *,
    article: ArticleDocument,
    rendered_html: str,
    images: Sequence[tuple[str, str]],
    cover_path: str,
    cover_sha256: str,
    theme: str,
    color: str,
    need_open_comment: bool,
    only_fans_can_comment: bool,
) -> dict[str, Any]:
    """Return the location-independent inputs that define a submitted WeChat draft."""

    frontmatter = {
        "title": article.meta.title,
        "author": article.meta.author,
        "summary": article.meta.summary,
        "cover_image": article.meta.cover_image,
        "source_url": article.meta.source_url,
    }
    return {
        "version": 1,
        "article": {
            "frontmatter": frontmatter,
            "body": _normalized_body(article.body),
        },
        "body_images": [
            {"path": path, "sha256": sha256} for path, sha256 in images
        ],
        "cover": {"path": cover_path, "sha256": cover_sha256},
        "render": {"theme": theme, "color": color},
        "draft_options": {
            "title": article.meta.title,
            "author": article.meta.author,
            "digest": article.meta.summary,
            "content_source_url": article.meta.source_url,
            "content_sha256": hashlib.sha256(
                rendered_html.encode("utf-8")
            ).hexdigest(),
            "need_open_comment": need_open_comment,
            "only_fans_can_comment": only_fans_can_comment,
        },
    }


def preview_hash(**inputs: Any) -> str:
    canonical = json.dumps(
        canonical_preview_inputs(**inputs),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = ["canonical_preview_inputs", "preview_hash"]
