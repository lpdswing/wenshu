from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


class ContentPathError(ValueError):
    """A content path cannot be used within the allowed roots."""


def _resolve(path: str | Path, *, must_exist: bool, kind: str) -> Path:
    try:
        return Path(path).resolve(strict=must_exist)
    except (OSError, RuntimeError) as exc:
        raise ContentPathError(f"unable to resolve content {kind}: {path}") from exc


def resolve_in_roots(
    path: str | Path,
    roots: Iterable[str | Path],
    must_exist: bool,
) -> Path:
    """Resolve ``path`` and require its resolved location to be under an allowed root."""

    root_inputs = tuple(roots)
    if not root_inputs:
        raise ContentPathError("at least one content root is required")

    resolved_roots = tuple(
        _resolve(root, must_exist=must_exist, kind="root") for root in root_inputs
    )
    candidate = _resolve(path, must_exist=must_exist, kind="path")
    if not any(candidate.is_relative_to(root) for root in resolved_roots):
        raise ContentPathError(f"content path is outside the allowed roots: {path}")
    return candidate
