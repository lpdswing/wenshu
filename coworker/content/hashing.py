from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from .models import ArticleDocument


def article_text_hash(article: ArticleDocument) -> str:
    payload = {
        "frontmatter": asdict(article.meta),
        "body": article.body.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n",
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
