from __future__ import annotations

import html
import os
import stat
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token

from .article import _article_from_text
from .hashing import article_text_hash
from .paths import ContentPathError, resolve_in_roots


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


@dataclass(frozen=True)
class _PathIdentity:
    device: int
    inode: int


def _snapshot_path(path: Path, *, directory: bool) -> _PathIdentity:
    try:
        status = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ContentPathError(f"content path changed during validation: {path}") from exc

    expected_kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_kind(status.st_mode):
        kind = "directory" if directory else "file"
        raise ContentPathError(f"content path is not a regular {kind}: {path}")
    return _PathIdentity(status.st_dev, status.st_ino)


def _matches_identity(status: os.stat_result, expected: _PathIdentity) -> bool:
    return (status.st_dev, status.st_ino) == (expected.device, expected.inode)


_HAS_POSIX_DIR_FD = (
    os.name == "posix"
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.open in os.supports_dir_fd
    and os.rename in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
)


def _open_posix_directory(path: Path, expected: _PathIdentity) -> int:
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ContentPathError(
            f"content directory changed after validation: {path}"
        ) from exc

    if not _matches_identity(os.fstat(descriptor), expected):
        os.close(descriptor)
        raise ContentPathError(f"content directory changed after validation: {path}")
    return descriptor


def _open_windows_directory(path: Path, expected: _PathIdentity) -> int:
    import ctypes
    from ctypes import wintypes

    file_list_directory = 0x0001
    file_read_attributes = 0x0080
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    open_existing = 3
    file_attribute_directory = 0x00000010
    file_attribute_reparse_point = 0x00000400
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    get_file_information = kernel32.GetFileInformationByHandle
    get_file_information.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    get_file_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    raw_path = str(path)
    if raw_path.startswith("\\\\?\\"):
        windows_path = raw_path
    elif raw_path.startswith("\\\\"):
        windows_path = f"\\\\?\\UNC\\{raw_path[2:]}"
    else:
        windows_path = f"\\\\?\\{raw_path}"
    handle = create_file(
        windows_path,
        file_list_directory | file_read_attributes,
        file_share_read | file_share_write,
        None,
        open_existing,
        file_flag_backup_semantics | file_flag_open_reparse_point,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        raise ContentPathError(
            f"content directory could not be locked after validation: {path}"
        ) from ctypes.WinError(ctypes.get_last_error())

    information = _ByHandleFileInformation()
    if not get_file_information(handle, ctypes.byref(information)):
        error = ctypes.WinError(ctypes.get_last_error())
        close_handle(handle)
        raise ContentPathError(
            f"content directory could not be inspected after validation: {path}"
        ) from error

    attributes = information.file_attributes
    try:
        locked_status = path.stat(follow_symlinks=False)
    except OSError as exc:
        close_handle(handle)
        raise ContentPathError(
            f"content directory changed after validation: {path}"
        ) from exc

    if (
        not attributes & file_attribute_directory
        or attributes & file_attribute_reparse_point
        or not _matches_identity(locked_status, expected)
    ):
        close_handle(handle)
        raise ContentPathError(f"content directory changed after validation: {path}")
    return int(handle)


def _close_windows_directory(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    close_handle = ctypes.WinDLL(
        "kernel32", use_last_error=True
    ).CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    close_handle(handle)


class _BoundDirectory:
    def __init__(self, path: Path, expected: _PathIdentity) -> None:
        self.path = path
        self.expected = expected
        self.dir_fd: int | None = None
        self.windows_handle: int | None = None

    def __enter__(self) -> _BoundDirectory:
        if _HAS_POSIX_DIR_FD:
            self.dir_fd = _open_posix_directory(self.path, self.expected)
        elif os.name == "nt":
            self.windows_handle = _open_windows_directory(self.path, self.expected)
        else:
            raise ContentPathError(
                "secure content directory operations are unavailable on this platform"
            )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        if self.dir_fd is not None:
            os.close(self.dir_fd)
            self.dir_fd = None
        if self.windows_handle is not None:
            _close_windows_directory(self.windows_handle)
            self.windows_handle = None

    def read_text(self, name: str, expected: _PathIdentity) -> str:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        if self.dir_fd is not None:
            flags |= os.O_NOFOLLOW
            try:
                descriptor = os.open(name, flags, dir_fd=self.dir_fd)
            except OSError as exc:
                raise ContentPathError(
                    "article changed after path validation"
                ) from exc
        else:
            flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
            try:
                descriptor = os.open(self.path / name, flags)
            except OSError as exc:
                raise ContentPathError(
                    "article changed after path validation"
                ) from exc

        if not _matches_identity(os.fstat(descriptor), expected):
            os.close(descriptor)
            raise ContentPathError("article changed after path validation")

        try:
            source = os.fdopen(descriptor, mode="r", encoding="utf-8")
        except BaseException:
            os.close(descriptor)
            raise
        with source:
            return source.read()

    def atomic_write_text(self, name: str, content: str) -> None:
        temporary_name = f".{name}.{uuid.uuid4().hex}.tmp"
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOINHERIT", 0)
        )
        descriptor = -1
        created = False
        try:
            if self.dir_fd is not None:
                descriptor = os.open(
                    temporary_name,
                    flags,
                    0o600,
                    dir_fd=self.dir_fd,
                )
            else:
                descriptor = os.open(
                    self.path / temporary_name,
                    flags,
                    0o600,
                )
            created = True
            try:
                destination = os.fdopen(
                    descriptor,
                    mode="w",
                    encoding="utf-8",
                    newline="\n",
                )
            except BaseException:
                os.close(descriptor)
                descriptor = -1
                raise
            descriptor = -1
            with destination:
                destination.write(content)

            if self.dir_fd is not None:
                os.replace(
                    temporary_name,
                    name,
                    src_dir_fd=self.dir_fd,
                    dst_dir_fd=self.dir_fd,
                )
            else:
                os.replace(
                    self.path / temporary_name,
                    self.path / name,
                )
            created = False
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            if created:
                try:
                    if self.dir_fd is not None:
                        os.unlink(temporary_name, dir_fd=self.dir_fd)
                    else:
                        (self.path / temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass
            raise


def _read_article_text(
    directory: _BoundDirectory,
    article_name: str,
    expected: _PathIdentity,
) -> str:
    return directory.read_text(article_name, expected)


def _atomic_write_text(
    directory: _BoundDirectory,
    target_name: str,
    content: str,
) -> None:
    directory.atomic_write_text(target_name, content)


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
    parent_path = resolved_article_path.parent
    parent_identity = _snapshot_path(parent_path, directory=True)
    article_identity = _snapshot_path(resolved_article_path, directory=False)
    preview_path = parent_path / "review.html"
    resolve_in_roots(preview_path, root_paths, must_exist=False)

    with _BoundDirectory(parent_path, parent_identity) as directory:
        article_text = _read_article_text(
            directory,
            resolved_article_path.name,
            article_identity,
        )
        article = _article_from_text(resolved_article_path, article_text)
        reviewed_hash = article_text_hash(article)
        rendered = _render_review_html(
            article.meta.title,
            article.meta.summary,
            article.body,
        )
        _atomic_write_text(directory, preview_path.name, rendered)

    return ArticleReview(
        title=article.meta.title,
        summary=article.meta.summary,
        article_path=resolved_article_path,
        preview_path=preview_path,
        reviewed_hash=reviewed_hash,
    )
