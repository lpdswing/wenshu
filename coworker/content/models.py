from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArticleFrontmatter:
    title: str
    author: str = ""
    summary: str = ""
    cover_image: str | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class ImageAsset:
    path: Path
    media_type: str
    width: int
    height: int
    sha256: str
    provider: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class ArticleDocument:
    path: Path
    meta: ArticleFrontmatter
    body: str
