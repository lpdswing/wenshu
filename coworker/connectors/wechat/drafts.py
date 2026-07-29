from __future__ import annotations

import errno
import hashlib
import hmac
import html
import json
import os
import re
import stat
import threading
import tempfile
import weakref
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from coworker.content.article import ArticleValidationError, parse_article_text
from coworker.content.paths import ContentPathError
from coworker.content.review import _BoundDirectory, _snapshot_path

from .client import WeChatClient
from .errors import (
    ReceiptStoreError,
    WeChatAPIError,
    WeChatCredentialError,
    WeChatHTTPError,
    WeChatImageError,
    WeChatResponseError,
    WeChatTransportError,
    wechat_failure_kind,
)
from .hashing import preview_hash as calculate_preview_hash
from .images import upload_body_image, upload_cover
from .preview import DraftPreview
from .renderer import (
    RenderedArticle,
    _portable_image_path,
    _safe_external_url,
    render_wechat_article,
)

_RECEIPT_NAME = "receipt.json"
_LOCK_NAME = ".wenshu-wechat-draft.lock"
_MAX_RECEIPT_BYTES = 16 * 1024
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_ACCOUNT_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{16}\Z")
_MEDIA_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")
_RECEIPT_FIELDS = {
    "preview_hash",
    "media_id",
    "title",
    "submitted_at",
    "account_id",
}
_MAX_STAGED_ASSET_BYTES = 20 * 1024 * 1024




@dataclass(frozen=True, slots=True)
class DraftReceipt:
    preview_hash: str
    media_id: str
    title: str
    submitted_at: str
    account_id: str


@dataclass(frozen=True, slots=True)
class DraftResult:
    status: Literal["success", "duplicate", "failed", "unknown"]
    receipt: DraftReceipt | None
    error_kind: str | None = None
    uploaded_assets: tuple[str, ...] = ()


def _lock_descriptor(descriptor: int) -> None:
    if os.name == "posix":
        import fcntl

        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                return
            except InterruptedError:
                continue
            except OSError as exc:
                raise ReceiptStoreError("receipt lock could not be acquired") from exc
    if os.name == "nt":
        import msvcrt

        while True:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
                return
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EDEADLK}:
                    raise ReceiptStoreError("receipt lock could not be acquired") from exc
    raise ReceiptStoreError("receipt locking is unavailable on this platform")


def _unlock_descriptor(descriptor: int) -> None:
    if os.name == "posix":
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)


def _open_windows_lock_file(path: Path) -> int:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_read = 0x80000000
    generic_write = 0x40000000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    open_always = 4
    file_attribute_directory = 0x00000010
    file_attribute_normal = 0x00000080
    file_attribute_reparse_point = 0x00000400
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

    handle = create_file(
        str(path),
        generic_read | generic_write,
        file_share_read | file_share_write,
        None,
        open_always,
        file_attribute_normal | file_flag_open_reparse_point,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        raise ctypes.WinError(ctypes.get_last_error())

    information = _ByHandleFileInformation()
    if not get_file_information(handle, ctypes.byref(information)):
        error = ctypes.WinError(ctypes.get_last_error())
        close_handle(handle)
        raise error
    if (
        information.file_attributes & file_attribute_directory
        or information.file_attributes & file_attribute_reparse_point
        or information.number_of_links != 1
    ):
        close_handle(handle)
        raise ReceiptStoreError("receipt lock file is not safe")

    flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    try:
        return msvcrt.open_osfhandle(int(handle), flags)
    except BaseException:
        close_handle(handle)
        raise


class _DirectoryLock:
    __slots__ = ("thread_lock", "thread_state", "__weakref__")

    def __init__(self) -> None:
        self.thread_lock = threading.RLock()
        self.thread_state = threading.local()


class ReceiptStore:
    """A receipt store permanently bound to one existing article directory."""

    _locks_guard = threading.Lock()
    _directory_locks: weakref.WeakValueDictionary[object, _DirectoryLock] = (
        weakref.WeakValueDictionary()
    )

    def __init__(self, article_directory: str | Path) -> None:
        try:
            directory = Path(article_directory).absolute()
            identity = _snapshot_path(directory, directory=True)
        except (OSError, TypeError, ValueError, ContentPathError) as exc:
            raise ReceiptStoreError("receipt directory is not safe") from exc
        self._directory = directory
        self._identity = identity
        with self._locks_guard:
            directory_lock = self._directory_locks.get(identity)
            if directory_lock is None:
                directory_lock = _DirectoryLock()
                self._directory_locks[identity] = directory_lock
            self._directory_lock = directory_lock

    @property
    def article_directory(self) -> Path:
        return self._directory

    @property
    def receipt_path(self) -> Path:
        return self._directory / _RECEIPT_NAME

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._directory_lock.thread_lock:
            state = self._directory_lock.thread_state
            depth = getattr(state, "depth", 0)
            if depth:
                state.depth = depth + 1
                try:
                    yield
                finally:
                    state.depth -= 1
                return

            descriptor = self._open_lock_file()
            locked = False
            try:
                _lock_descriptor(descriptor)
                locked = True
                state.depth = 1
                yield
            finally:
                if locked:
                    del state.depth
                try:
                    if locked:
                        _unlock_descriptor(descriptor)
                finally:
                    os.close(descriptor)

    def _open_lock_file(self) -> int:
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOINHERIT", 0)
        )
        descriptor = -1
        lock_path: Path | None = None
        try:
            with _BoundDirectory(self._directory, self._identity) as directory:
                if directory.dir_fd is not None:
                    descriptor = os.open(
                        _LOCK_NAME,
                        flags,
                        0o600,
                        dir_fd=directory.dir_fd,
                    )
                elif directory.bound_path is not None:
                    lock_path = directory.bound_path / _LOCK_NAME
                    if os.name == "nt":
                        descriptor = _open_windows_lock_file(lock_path)
                    else:  # pragma: no cover - secure binding supports POSIX and Windows
                        descriptor = os.open(lock_path, flags, 0o600)
                else:  # pragma: no cover - guarded by _BoundDirectory
                    raise ReceiptStoreError("receipt lock directory is unavailable")
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    raise ReceiptStoreError("receipt lock file is not safe")
                if lock_path is not None:
                    path_metadata = lock_path.stat(follow_symlinks=False)
                    if (
                        not stat.S_ISREG(path_metadata.st_mode)
                        or (path_metadata.st_dev, path_metadata.st_ino)
                        != (metadata.st_dev, metadata.st_ino)
                    ):
                        raise ReceiptStoreError("receipt lock file is not safe")
                if hasattr(os, "fchmod"):
                    os.fchmod(descriptor, 0o600)
                elif directory.bound_path is not None:
                    os.chmod(directory.bound_path / _LOCK_NAME, 0o600)
                if os.name == "nt":
                    os.ftruncate(descriptor, 1)
            return descriptor
        except ReceiptStoreError:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        except (OSError, ContentPathError) as exc:
            if descriptor >= 0:
                os.close(descriptor)
            raise ReceiptStoreError("receipt lock file is not safe") from exc

    def require_same_directory(self, path: str | Path) -> None:
        try:
            parent = Path(path).absolute()
            identity = _snapshot_path(parent, directory=True)
        except (OSError, TypeError, ValueError, ContentPathError) as exc:
            raise ReceiptStoreError("preview is not in the receipt directory") from exc
        if identity != self._identity:
            raise ReceiptStoreError("preview is not in the receipt directory")

    def read_text(self, relative_path: str) -> str:
        try:
            with _BoundDirectory(self._directory, self._identity) as directory:
                return directory.read_text(relative_path)
        except (FileNotFoundError, OSError, UnicodeError, ContentPathError) as exc:
            raise ReceiptStoreError("article input is not safely readable") from exc

    def asset_sha256(self, relative_path: str) -> str:
        try:
            portable = _portable_image_path(relative_path)
            digest = hashlib.sha256()
            with _BoundDirectory(self._directory, self._identity) as directory:
                with directory.open_binary(portable) as source:
                    for chunk in iter(lambda: source.read(128 * 1024), b""):
                        digest.update(chunk)
            return digest.hexdigest()
        except (FileNotFoundError, OSError, ValueError, ContentPathError) as exc:
            raise ReceiptStoreError("article asset is not safely readable") from exc

    @contextmanager
    def staged_asset(
        self,
        relative_path: str,
        expected_sha256: str,
    ) -> Iterator[Path]:
        try:
            portable = _portable_image_path(relative_path)
            suffix = Path(portable).suffix.casefold()
        except (TypeError, ValueError) as exc:
            raise ReceiptStoreError("article asset path is not safe") from exc

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".wenshu-wechat-",
                suffix=suffix,
                delete=False,
            ) as destination:
                temporary_path = Path(destination.name)
                os.chmod(temporary_path, 0o600)
                digest = hashlib.sha256()
                copied = 0
                with _BoundDirectory(self._directory, self._identity) as directory:
                    with directory.open_binary(portable) as source:
                        for chunk in iter(lambda: source.read(128 * 1024), b""):
                            copied += len(chunk)
                            if copied > _MAX_STAGED_ASSET_BYTES:
                                raise ReceiptStoreError("article asset exceeds safe size")
                            digest.update(chunk)
                            destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            if not hmac.compare_digest(digest.hexdigest(), expected_sha256):
                raise ReceiptStoreError("article asset changed after preview")
            yield temporary_path
        except ReceiptStoreError:
            raise
        except (FileNotFoundError, OSError, ContentPathError) as exc:
            raise ReceiptStoreError("article asset is not safely readable") from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def load(self) -> DraftReceipt | None:
        try:
            with _BoundDirectory(self._directory, self._identity) as directory:
                with directory.open_binary(_RECEIPT_NAME) as source:
                    encoded = source.read(_MAX_RECEIPT_BYTES + 1)
        except FileNotFoundError:
            return None
        except (OSError, ContentPathError) as exc:
            raise ReceiptStoreError("receipt is not safely readable") from exc

        if len(encoded) > _MAX_RECEIPT_BYTES:
            raise ReceiptStoreError("receipt is invalid")
        try:
            payload = json.loads(encoded.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ReceiptStoreError("receipt is invalid") from exc
        return _parse_receipt(payload)

    def save(self, receipt: DraftReceipt) -> None:
        receipt = _parse_receipt(asdict(receipt))
        payload = json.dumps(
            asdict(receipt),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
        try:
            with _BoundDirectory(self._directory, self._identity) as directory:
                directory.atomic_write_text(_RECEIPT_NAME, payload)
        except (OSError, ContentPathError) as exc:
            raise ReceiptStoreError("receipt could not be written safely") from exc


@dataclass(frozen=True, slots=True)
class _Submission:
    rendered: RenderedArticle
    body_assets: tuple[tuple[str, str], ...]
    cover_path: str
    cover_sha256: str
    preview_hash: str
    source_url: str | None


def _parse_receipt(payload: object) -> DraftReceipt:
    if not isinstance(payload, dict) or set(payload) != _RECEIPT_FIELDS:
        raise ReceiptStoreError("receipt is invalid")
    if not all(isinstance(payload[field], str) for field in _RECEIPT_FIELDS):
        raise ReceiptStoreError("receipt is invalid")

    preview_hash = payload["preview_hash"]
    media_id = payload["media_id"]
    title = payload["title"]
    submitted_at = payload["submitted_at"]
    account_id = payload["account_id"]
    if _SHA256_PATTERN.fullmatch(preview_hash) is None:
        raise ReceiptStoreError("receipt is invalid")
    if _MEDIA_ID_PATTERN.fullmatch(media_id) is None:
        raise ReceiptStoreError("receipt is invalid")
    if not title or len(title) > 512 or any(ord(character) < 0x20 for character in title):
        raise ReceiptStoreError("receipt is invalid")
    if _ACCOUNT_ID_PATTERN.fullmatch(account_id) is None:
        raise ReceiptStoreError("receipt is invalid")
    try:
        parsed_time = datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReceiptStoreError("receipt is invalid") from exc
    if parsed_time.tzinfo is None:
        raise ReceiptStoreError("receipt is invalid")
    return DraftReceipt(
        preview_hash=preview_hash,
        media_id=media_id,
        title=title,
        submitted_at=submitted_at,
        account_id=account_id,
    )


def _account_id(client: WeChatClient) -> str:
    value = getattr(client, "account_id", None)
    if not isinstance(value, str) or not value:
        value = type(client).__qualname__
    if _ACCOUNT_ID_PATTERN.fullmatch(value) is not None:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


def _recompute_submission(preview: DraftPreview, store: ReceiptStore) -> _Submission:
    if not isinstance(preview, DraftPreview):
        raise ReceiptStoreError("preview is invalid")
    store.require_same_directory(preview.preview_path.parent)
    article_path = Path(preview.article.path)
    store.require_same_directory(article_path.parent)
    if article_path.name in {"", ".", ".."}:
        raise ReceiptStoreError("article path is invalid")

    article_text = store.read_text(article_path.name)
    try:
        article = parse_article_text(article_path, article_text)
        rendered = render_wechat_article(article, preview.theme, preview.color)
    except (ArticleValidationError, ValueError) as exc:
        raise ReceiptStoreError("preview inputs are invalid") from exc

    body_paths = tuple(image.path for image in preview.images)
    if body_paths != rendered.image_refs or len(set(body_paths)) != len(body_paths):
        raise ReceiptStoreError("preview body images changed")
    image_hashes = tuple((path, store.asset_sha256(path)) for path in body_paths)
    try:
        cover_path = _portable_image_path(preview.cover_path)
    except (TypeError, ValueError) as exc:
        raise ReceiptStoreError("preview cover path changed") from exc
    cover_sha256 = store.asset_sha256(cover_path)
    recalculated = calculate_preview_hash(
        article=article,
        rendered_html=rendered.html,
        images=image_hashes,
        cover_path=cover_path,
        cover_sha256=cover_sha256,
        theme=preview.theme,
        color=preview.color,
        need_open_comment=preview.need_open_comment,
        only_fans_can_comment=preview.only_fans_can_comment,
    )
    if not isinstance(preview.preview_hash, str) or not hmac.compare_digest(
        recalculated, preview.preview_hash
    ):
        raise ReceiptStoreError("preview hash no longer matches article inputs")
    return _Submission(
        rendered,
        image_hashes,
        cover_path,
        cover_sha256,
        recalculated,
        article.meta.source_url,
    )


def _replace_image_placeholders(
    rendered_html: str,
    uploaded_urls: dict[str, str],
) -> str:
    content = rendered_html
    for path, url in uploaded_urls.items():
        placeholder = f'data-wenshu-image="{html.escape(path, quote=True)}"'
        if placeholder not in content:
            raise WeChatResponseError()
        content = content.replace(
            placeholder,
            f'src="{html.escape(url, quote=True)}"',
        )
    if "data-wenshu-image=" in content:
        raise WeChatResponseError()
    return content


def _source_url(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    return _safe_external_url(value)




def _failed(error: BaseException, uploaded: list[str]) -> DraftResult:
    return DraftResult(
        status="failed",
        receipt=None,
        error_kind=wechat_failure_kind(error),
        uploaded_assets=tuple(uploaded),
    )


def _asset_label(category: str, relative_path: str) -> str:
    return f"{category}:{_portable_image_path(relative_path)}"


def _create_draft(
    preview: DraftPreview,
    client: WeChatClient,
    receipt_store: ReceiptStore,
) -> DraftResult:
    """Create one WeChat draft, preserving safe idempotency and uncertain outcomes."""

    if not isinstance(receipt_store, ReceiptStore):
        return DraftResult("failed", None, "receipt_invalid")

    uploaded: list[str] = []
    with receipt_store.transaction():
        try:
            submission = _recompute_submission(preview, receipt_store)
        except ReceiptStoreError:
            return DraftResult("failed", None, "preview_changed")
        try:
            account_id = _account_id(client)
            existing = receipt_store.load()
        except ReceiptStoreError:
            return DraftResult("failed", None, "receipt_invalid")

        if existing is not None and hmac.compare_digest(
            existing.preview_hash, submission.preview_hash
        ):
            if existing.title != submission.rendered.title or existing.account_id != account_id:
                return DraftResult("failed", None, "receipt_invalid")
            return DraftResult("duplicate", existing)

        uploaded_urls: dict[str, str] = {}
        try:
            for path, expected_sha256 in submission.body_assets:
                with receipt_store.staged_asset(path, expected_sha256) as staged:
                    uploaded_urls[path] = upload_body_image(client, staged)
                uploaded.append(_asset_label("body", path))
            content = _replace_image_placeholders(
                submission.rendered.html,
                uploaded_urls,
            )
            with receipt_store.staged_asset(
                submission.cover_path,
                submission.cover_sha256,
            ) as staged:
                thumb_media_id = upload_cover(client, staged)
            uploaded.append(_asset_label("cover", submission.cover_path))
        except (
            OSError,
            ReceiptStoreError,
            WeChatAPIError,
            WeChatCredentialError,
            WeChatHTTPError,
            WeChatImageError,
            WeChatResponseError,
            WeChatTransportError,
        ) as exc:
            return _failed(exc, uploaded)

        article_payload: dict[str, Any] = {
            "title": submission.rendered.title,
            "author": submission.rendered.author,
            "digest": submission.rendered.digest,
            "content": content,
            "thumb_media_id": thumb_media_id,
            "need_open_comment": int(preview.need_open_comment),
            "only_fans_can_comment": int(preview.only_fans_can_comment),
        }
        source_url = _source_url(submission.source_url)
        if source_url is not None:
            article_payload["content_source_url"] = source_url

        try:
            response = client.request_json(
                "POST",
                "/cgi-bin/draft/add",
                json={"articles": [article_payload]},
            )
        except (WeChatAPIError, WeChatCredentialError) as exc:
            return _failed(exc, uploaded)
        except WeChatHTTPError as exc:
            if exc.status_code < 500:
                return _failed(exc, uploaded)
            return DraftResult(
                "unknown",
                None,
                "http",
                tuple(uploaded),
            )
        except WeChatTransportError as exc:
            if exc.phase == "pre_send":
                return _failed(exc, uploaded)
            return DraftResult(
                "unknown",
                None,
                "transport",
                tuple(uploaded),
            )
        except WeChatResponseError:
            return DraftResult(
                "unknown",
                None,
                "invalid_response",
                tuple(uploaded),
            )

        media_id = response.get("media_id")
        if not isinstance(media_id, str) or not media_id.strip():
            return DraftResult(
                "unknown",
                None,
                "invalid_response",
                tuple(uploaded),
            )
        try:
            receipt = _parse_receipt(
                asdict(
                    DraftReceipt(
                        preview_hash=submission.preview_hash,
                        media_id=media_id.strip(),
                        title=submission.rendered.title,
                        submitted_at=datetime.now(timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z"),
                        account_id=account_id,
                    )
                )
            )
        except ReceiptStoreError:
            return DraftResult(
                "unknown",
                None,
                "invalid_response",
                tuple(uploaded),
            )
        try:
            receipt_store.save(receipt)
        except ReceiptStoreError:
            return DraftResult(
                "unknown",
                None,
                "receipt_write",
                tuple(uploaded),
            )
        return DraftResult("success", receipt, uploaded_assets=tuple(uploaded))


def create_draft(
    preview: DraftPreview,
    client: WeChatClient,
    receipt_store: ReceiptStore,
) -> DraftResult:
    """Create one WeChat draft, preserving safe idempotency and uncertain outcomes."""

    try:
        return _create_draft(preview, client, receipt_store)
    except ReceiptStoreError:
        return DraftResult("failed", None, "receipt_invalid")


__all__ = [
    "DraftReceipt",
    "DraftResult",
    "ReceiptStore",
    "ReceiptStoreError",
    "create_draft",
]
