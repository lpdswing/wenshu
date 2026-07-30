from __future__ import annotations

from contextlib import contextmanager
import html
import os
import stat
import uuid
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, BinaryIO, TypeVar

from markdown_it import MarkdownIt
from markdown_it.token import Token

from .article import parse_article_text
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
    and os.mkdir in os.supports_dir_fd
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


def _open_windows_directory(
    path: Path,
    expected: _PathIdentity,
) -> tuple[int, Path]:
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
    get_final_path_name = kernel32.GetFinalPathNameByHandleW
    get_final_path_name.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    get_final_path_name.restype = wintypes.DWORD
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

    required_length = get_final_path_name(handle, None, 0, 0)
    if required_length == 0:
        error = ctypes.WinError(ctypes.get_last_error())
        close_handle(handle)
        raise ContentPathError(
            f"content directory final path is unavailable: {path}"
        ) from error
    final_path_buffer = ctypes.create_unicode_buffer(required_length)
    written_length = get_final_path_name(
        handle,
        final_path_buffer,
        required_length,
        0,
    )
    if written_length == 0 or written_length >= required_length:
        error = ctypes.WinError(ctypes.get_last_error())
        close_handle(handle)
        raise ContentPathError(
            f"content directory final path is unavailable: {path}"
        ) from error
    return int(handle), Path(final_path_buffer.value)


def _close_windows_directory(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    close_handle = ctypes.WinDLL(
        "kernel32", use_last_error=True
    ).CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    close_handle(handle)


_T = TypeVar("_T")


def _relative_parts(relative_path: str | Path) -> tuple[str, ...]:
    raw_path = os.fspath(relative_path)
    if not raw_path or "\x00" in raw_path:
        raise ContentPathError("content output path must be a non-empty relative path")

    windows_path = PureWindowsPath(raw_path)
    normalized_path = PurePosixPath(raw_path.replace("\\", "/"))
    if (
        windows_path.is_absolute()
        or windows_path.drive
        or normalized_path.is_absolute()
    ):
        raise ContentPathError("content output path must be relative")

    parts = normalized_path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ContentPathError("content output path contains an unsafe component")
    return parts


def _ensure_child_directory(path: Path, *, create: bool) -> _PathIdentity:
    try:
        status = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        if not create:
            raise
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        try:
            status = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise ContentPathError(
                "content output parent changed while it was being created"
            ) from exc
    except OSError as exc:
        raise ContentPathError("content output parent could not be inspected") from exc

    if not stat.S_ISDIR(status.st_mode):
        raise ContentPathError(
            "content output parent changed to a non-directory or symbolic link"
        )
    return _PathIdentity(status.st_dev, status.st_ino)


@dataclass(frozen=True)
class _BoundParent:
    dir_fd: int | None
    path: Path | None
    checks: tuple[tuple[Path, _PathIdentity], ...] = ()

    def validate(self) -> None:
        for path, expected in self.checks:
            try:
                status = path.stat(follow_symlinks=False)
            except OSError as exc:
                raise ContentPathError(
                    "content output parent changed after validation"
                ) from exc
            if not stat.S_ISDIR(status.st_mode) or not _matches_identity(
                status,
                expected,
            ):
                raise ContentPathError(
                    "content output parent changed after validation"
                )


class _BoundDirectory:
    def __init__(self, path: Path, expected: _PathIdentity) -> None:
        self.path = path
        self.expected = expected
        self.dir_fd: int | None = None
        self.windows_handle: int | None = None
        self.bound_path: Path | None = None

    def __enter__(self) -> _BoundDirectory:
        if (
            self.dir_fd is not None
            or self.windows_handle is not None
            or self.bound_path is not None
        ):
            raise RuntimeError("content directory is already bound")
        if _HAS_POSIX_DIR_FD:
            self.dir_fd = _open_posix_directory(self.path, self.expected)
        elif os.name == "nt":
            self.windows_handle, self.bound_path = _open_windows_directory(
                self.path,
                self.expected,
            )
        else:
            raise ContentPathError(
                "secure content directory binding is unavailable on this platform"
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
        self.bound_path = None

    @contextmanager
    def _open_parent(
        self,
        parts: tuple[str, ...],
        *,
        create: bool,
    ) -> Iterator[_BoundParent]:
        if self.dir_fd is not None:
            descriptor = os.dup(self.dir_fd)
            try:
                current_path = self.path
                checks: list[tuple[Path, _PathIdentity]] = [
                    (current_path, self.expected)
                ]
                flags = (
                    os.O_RDONLY
                    | os.O_DIRECTORY
                    | os.O_NOFOLLOW
                    | getattr(os, "O_CLOEXEC", 0)
                )
                for part in parts:
                    try:
                        next_descriptor = os.open(
                            part,
                            flags,
                            dir_fd=descriptor,
                        )
                    except FileNotFoundError:
                        if not create:
                            raise
                        try:
                            os.mkdir(part, mode=0o700, dir_fd=descriptor)
                        except FileExistsError:
                            pass
                        try:
                            next_descriptor = os.open(
                                part,
                                flags,
                                dir_fd=descriptor,
                            )
                        except OSError as exc:
                            raise ContentPathError(
                                "content output parent changed while it was being created"
                            ) from exc
                    except OSError as exc:
                        raise ContentPathError(
                            "content output parent is not a safe directory"
                        ) from exc
                    next_status = os.fstat(next_descriptor)
                    if not stat.S_ISDIR(next_status.st_mode):
                        os.close(next_descriptor)
                        raise ContentPathError(
                            "content output parent is not a directory"
                        )
                    os.close(descriptor)
                    descriptor = next_descriptor
                    current_path = current_path / part
                    checks.append(
                        (
                            current_path,
                            _PathIdentity(next_status.st_dev, next_status.st_ino),
                        )
                    )
                yield _BoundParent(
                    dir_fd=descriptor,
                    path=None,
                    checks=tuple(checks),
                )
            finally:
                os.close(descriptor)
            return

        if self.windows_handle is not None:
            if self.bound_path is None:  # pragma: no cover - guarded by __enter__
                raise RuntimeError("content directory has no bound Windows path")
            current_path = self.bound_path
            handles: list[int] = []
            try:
                for part in parts:
                    candidate = current_path / part
                    expected = _ensure_child_directory(candidate, create=create)
                    handle, current_path = _open_windows_directory(
                        candidate,
                        expected,
                    )
                    handles.append(handle)
                yield _BoundParent(dir_fd=None, path=current_path)
            finally:
                for handle in reversed(handles):
                    _close_windows_directory(handle)
            return


    @contextmanager
    def open_binary(
        self,
        relative_path: str | Path,
        expected: _PathIdentity | None = None,
    ) -> Iterator[BinaryIO]:
        parts = _relative_parts(relative_path)
        descriptor = -1
        with self._open_parent(parts[:-1], create=False) as parent:
            parent.validate()
            flags = (
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOINHERIT", 0)
            )
            file_path: Path | None = None
            try:
                if parent.dir_fd is not None:
                    descriptor = os.open(
                        parts[-1],
                        flags,
                        dir_fd=parent.dir_fd,
                    )
                else:
                    if parent.path is None:  # pragma: no cover - internal invariant
                        raise RuntimeError("content output parent is unavailable")
                    file_path = parent.path / parts[-1]
                    before = file_path.stat(follow_symlinks=False)
                    if not stat.S_ISREG(before.st_mode):
                        raise ContentPathError(
                            "content file is not a regular file"
                        )
                    descriptor = os.open(file_path, flags)
            except FileNotFoundError:
                raise
            except ContentPathError:
                raise
            except OSError as exc:
                raise ContentPathError(
                    "content file changed after path validation"
                ) from exc

            status = os.fstat(descriptor)
            if not stat.S_ISREG(status.st_mode) or (
                expected is not None and not _matches_identity(status, expected)
            ):
                os.close(descriptor)
                descriptor = -1
                raise ContentPathError(
                    "content file changed after path validation"
                )
            if file_path is not None:
                try:
                    current = file_path.stat(follow_symlinks=False)
                except OSError as exc:
                    os.close(descriptor)
                    descriptor = -1
                    raise ContentPathError(
                        "content file changed after path validation"
                    ) from exc
                if not stat.S_ISREG(current.st_mode) or not _matches_identity(
                    current,
                    _PathIdentity(status.st_dev, status.st_ino),
                ):
                    os.close(descriptor)
                    descriptor = -1
                    raise ContentPathError(
                        "content file changed after path validation"
                    )
                parent.validate()

            try:
                source = os.fdopen(descriptor, mode="rb")
            except BaseException:
                os.close(descriptor)
                descriptor = -1
                raise
            descriptor = -1
            with source:
                yield source

    def read_text(
        self,
        name: str,
        expected: _PathIdentity | None = None,
    ) -> str:
        with self.open_binary(name, expected) as source:
            return source.read().decode("utf-8")

    def atomic_write(
        self,
        relative_path: str | Path,
        writer: Callable[[BinaryIO], _T],
    ) -> _T:
        parts = _relative_parts(relative_path)
        temporary_name = f".coworker-{uuid.uuid4().hex}.tmp"
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
        with self._open_parent(parts[:-1], create=True) as parent:
            try:
                parent.validate()
                if parent.dir_fd is not None:
                    descriptor = os.open(
                        temporary_name,
                        flags,
                        0o600,
                        dir_fd=parent.dir_fd,
                    )
                else:
                    if parent.path is None:  # pragma: no cover - internal invariant
                        raise RuntimeError("content output parent is unavailable")
                    descriptor = os.open(
                        parent.path / temporary_name,
                        flags,
                        0o600,
                    )
                created = True
                try:
                    destination = os.fdopen(descriptor, mode="wb")
                except BaseException:
                    os.close(descriptor)
                    descriptor = -1
                    raise
                descriptor = -1
                with destination:
                    result = writer(destination)
                    destination.flush()
                    os.fsync(destination.fileno())

                parent.validate()
                if parent.dir_fd is not None:
                    os.replace(
                        temporary_name,
                        parts[-1],
                        src_dir_fd=parent.dir_fd,
                        dst_dir_fd=parent.dir_fd,
                    )
                else:
                    if parent.path is None:  # pragma: no cover - internal invariant
                        raise RuntimeError("content output parent is unavailable")
                    os.replace(
                        parent.path / temporary_name,
                        parent.path / parts[-1],
                    )
                created = False
                return result
            except BaseException:
                if descriptor >= 0:
                    os.close(descriptor)
                if created:
                    try:
                        if parent.dir_fd is not None:
                            os.unlink(temporary_name, dir_fd=parent.dir_fd)
                        elif parent.path is not None:
                            (parent.path / temporary_name).unlink(missing_ok=True)
                    except OSError:
                        pass
                raise

    def atomic_write_text(self, name: str, content: str) -> None:
        encoded = content.encode("utf-8")
        self.atomic_write(name, lambda destination: destination.write(encoded))

    def unlink(self, relative_path: str | Path, *, missing_ok: bool = False) -> None:
        parts = _relative_parts(relative_path)
        try:
            with self._open_parent(parts[:-1], create=False) as parent:
                parent.validate()
                if parent.dir_fd is not None:
                    os.unlink(parts[-1], dir_fd=parent.dir_fd)
                else:
                    if parent.path is None:  # pragma: no cover - internal invariant
                        raise RuntimeError("content output parent is unavailable")
                    (parent.path / parts[-1]).unlink()
        except FileNotFoundError:
            if not missing_ok:
                raise


def bind_directory(path: str | Path) -> _BoundDirectory:
    directory_path = Path(path)
    return _BoundDirectory(
        directory_path,
        _snapshot_path(directory_path, directory=True),
    )


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
        article = parse_article_text(resolved_article_path, article_text)
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
