from __future__ import annotations

import multiprocessing
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import stat
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from coworker.connectors.wechat import (
    DraftPreview,
    DraftReceipt,
    PreviewImage,
    ReceiptStore,
    WeChatHTTPError,
    WeChatTransportError,
    classify_wechat_error,
    create_draft,
)
from coworker.connectors.wechat.hashing import preview_hash
from coworker.connectors.wechat.renderer import render_wechat_article
from coworker.content.article import parse_article_text

_ACCOUNT_ID = "sha256:0123456789abcdef"
_ACCESS_TOKEN_MARKER = "test-access-token-that-must-not-survive"


class _Client:
    account_id = _ACCOUNT_ID

    def __init__(self, failure: str | None = None) -> None:
        self.failure = failure
        self.calls: list[dict[str, object]] = []

    def request_json(self, method, path, params=None, json=None, files=None):
        self.calls.append(
            {"method": method, "path": path, "params": params, "json": json, "files": files}
        )
        if path == "/cgi-bin/media/uploadimg":
            if self.failure == "body":
                raise WeChatTransportError("pre_send")
            return {"url": "https://mmbiz.qpic.cn/body.png?x=1&y=2"}
        if path == "/cgi-bin/material/add_material":
            if self.failure == "cover":
                raise WeChatTransportError("pre_send")
            return {"media_id": "cover-media-id"}
        if path == "/cgi-bin/draft/add":
            if self.failure == "api":
                raise classify_wechat_error(48001, "api unauthorized")
            if self.failure == "pre_send":
                raise WeChatTransportError("pre_send")
            if self.failure == "post_send":
                raise WeChatTransportError("post_send")
            if self.failure == "http_400":
                raise WeChatHTTPError(400)
            if self.failure == "http_502":
                raise WeChatHTTPError(502)
            if self.failure == "invalid_response":
                return {}
            if self.failure == "invalid_media_id":
                return {"media_id": "draft-id\ninjected"}
            return {"media_id": "draft-media-id"}
        raise AssertionError(f"unexpected API path: {path}")


class _ProcessClient:
    account_id = _ACCOUNT_ID

    def __init__(self, draft_calls) -> None:
        self.draft_calls = draft_calls

    def request_json(self, method, path, params=None, json=None, files=None):
        if path == "/cgi-bin/media/uploadimg":
            return {"url": "https://mmbiz.qpic.cn/body.png"}
        if path == "/cgi-bin/material/add_material":
            return {"media_id": "cover-media-id"}
        if path == "/cgi-bin/draft/add":
            with self.draft_calls.get_lock():
                self.draft_calls.value += 1
            time.sleep(0.2)
            return {"media_id": "draft-media-id"}
        raise AssertionError(f"unexpected API path: {path}")


def _submit_from_process(preview, directory, start, draft_calls, results) -> None:
    try:
        start.wait(timeout=10)
        result = create_draft(
            preview,
            _ProcessClient(draft_calls),
            ReceiptStore(directory),
        )
        media_id = None if result.receipt is None else result.receipt.media_id
        results.put((result.status, media_id))
    except BaseException as exc:
        results.put(("error", type(exc).__name__))


def _exit_while_holding_lock(directory, acquired) -> None:
    with ReceiptStore(directory).transaction():
        acquired.set()
        os._exit(0)


def _acquire_lock_and_report(directory, acquired) -> None:
    with ReceiptStore(directory).transaction():
        acquired.set()


def _write_png(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (24, 16), color).save(path, format="PNG")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_preview(
    directory: Path,
    *,
    source_url: str | None = "https://example.test/source",
) -> DraftPreview:
    body_path = directory / "images" / "body.png"
    cover_path = directory / "cover.png"
    _write_png(body_path, (12, 34, 56))
    _write_png(cover_path, (78, 90, 123))
    source_line = "" if source_url is None else f"sourceUrl: {json.dumps(source_url)}\n"
    article_path = directory / "article.md"
    article_text = (
        "---\n"
        "title: 测试标题\n"
        "author: 测试作者\n"
        "summary: 测试摘要\n"
        "coverImage: cover.png\n"
        f"{source_line}"
        "---\n\n"
        "正文 ![示例](images/body.png)\n"
    )
    article_path.write_text(article_text, encoding="utf-8")
    article = parse_article_text(article_path, article_text)
    rendered = render_wechat_article(article, "default", "#07C160")
    body_hash = _sha256(body_path)
    cover_hash = _sha256(cover_path)
    digest = preview_hash(
        article=article,
        rendered_html=rendered.html,
        images=(("images/body.png", body_hash),),
        cover_path="cover.png",
        cover_sha256=cover_hash,
        theme="default",
        color="#07C160",
        need_open_comment=True,
        only_fans_can_comment=True,
    )
    return DraftPreview(
        article=article,
        rendered=rendered,
        cover_path="cover.png",
        cover_sha256=cover_hash,
        images=(
            PreviewImage(
                path="images/body.png",
                sha256=body_hash,
                media_type="image/png",
                width=24,
                height=16,
            ),
        ),
        theme="default",
        color="#07C160",
        need_open_comment=True,
        only_fans_can_comment=True,
        preview_path=directory / "article.html",
        preview_hash=digest,
    )


def _draft_calls(client: _Client) -> list[dict[str, object]]:
    return [call for call in client.calls if call["path"] == "/cgi-bin/draft/add"]


def test_success_uses_deterministic_order_payload_and_private_receipt(tmp_path):
    preview = _make_preview(tmp_path)
    client = _Client()
    store = ReceiptStore(tmp_path)

    result = create_draft(preview, client, store)

    assert result.status == "success"
    assert result.receipt is not None
    assert result.receipt.media_id == "draft-media-id"
    assert result.uploaded_assets == ("body:images/body.png", "cover:cover.png")
    assert [call["path"] for call in client.calls] == [
        "/cgi-bin/media/uploadimg",
        "/cgi-bin/material/add_material",
        "/cgi-bin/draft/add",
    ]
    payload = _draft_calls(client)[0]["json"]
    article = payload["articles"][0]
    assert set(article) == {
        "title",
        "author",
        "digest",
        "content",
        "thumb_media_id",
        "need_open_comment",
        "only_fans_can_comment",
        "content_source_url",
    }
    assert article["title"] == "测试标题"
    assert article["author"] == "测试作者"
    assert article["digest"] == "测试摘要"
    assert article["thumb_media_id"] == "cover-media-id"
    assert article["need_open_comment"] == 1
    assert article["only_fans_can_comment"] == 1
    assert article["content_source_url"] == "https://example.test/source"
    assert "data-wenshu-image" not in article["content"]
    assert 'src="https://mmbiz.qpic.cn/body.png?x=1&amp;y=2"' in article["content"]

    receipt_text = store.receipt_path.read_text(encoding="utf-8")
    receipt_payload = json.loads(receipt_text)
    assert receipt_payload == {
        "account_id": _ACCOUNT_ID,
        "media_id": "draft-media-id",
        "preview_hash": preview.preview_hash,
        "submitted_at": result.receipt.submitted_at,
        "title": "测试标题",
    }
    assert stat.S_IMODE(store.receipt_path.stat().st_mode) == 0o600
    assert str(tmp_path) not in receipt_text
    assert _ACCESS_TOKEN_MARKER not in receipt_text


def test_second_identical_submission_returns_receipt_without_network(tmp_path):
    preview = _make_preview(tmp_path)
    store = ReceiptStore(tmp_path)
    first_client = _Client()
    first = create_draft(preview, first_client, store)
    second_client = _Client(failure="post_send")

    second = create_draft(preview, second_client, store)

    assert first.status == "success"
    assert second.status == "duplicate"
    assert second.receipt == first.receipt
    assert second_client.calls == []


def test_two_store_instances_serialize_same_hash_submission(tmp_path):
    preview = _make_preview(tmp_path)
    stores = (ReceiptStore(tmp_path), ReceiptStore(tmp_path))
    start = threading.Barrier(2)

    class SlowClient(_Client):
        def request_json(self, method, path, params=None, json=None, files=None):
            if path == "/cgi-bin/draft/add":
                time.sleep(0.05)
            return super().request_json(method, path, params, json, files)

    client = SlowClient()

    def submit(store):
        start.wait()
        return create_draft(preview, client, store)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, stores))

    assert sorted(result.status for result in results) == ["duplicate", "success"]
    assert len(_draft_calls(client)) == 1
    assert [call["path"] for call in client.calls].count(
        "/cgi-bin/media/uploadimg"
    ) == 1
    assert [call["path"] for call in client.calls].count(
        "/cgi-bin/material/add_material"
    ) == 1
    lock_path = tmp_path / ".wenshu-wechat-draft.lock"
    assert stat.S_ISREG(lock_path.stat(follow_symlinks=False).st_mode)
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


def test_same_thread_nested_store_transactions_share_advisory_lock(tmp_path):
    first = ReceiptStore(tmp_path)
    second = ReceiptStore(tmp_path)

    with first.transaction():
        with second.transaction():
            assert (tmp_path / ".wenshu-wechat-draft.lock").is_file()


def test_different_processes_serialize_same_hash_submission(tmp_path):
    preview = _make_preview(tmp_path)
    context = multiprocessing.get_context("spawn")
    start = context.Barrier(2)
    draft_calls = context.Value("i", 0)
    results = context.Queue()
    processes = [
        context.Process(
            target=_submit_from_process,
            args=(preview, tmp_path, start, draft_calls, results),
        )
        for _ in range(2)
    ]

    try:
        for process in processes:
            process.start()
        outcomes = [results.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(timeout=10)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=10)

    assert [process.exitcode for process in processes] == [0, 0]
    assert sorted(status for status, _media_id in outcomes) == [
        "duplicate",
        "success",
    ]
    assert {media_id for _status, media_id in outcomes} == {"draft-media-id"}
    assert draft_calls.value == 1


def test_process_exit_releases_advisory_lock(tmp_path):
    context = multiprocessing.get_context("spawn")
    first_acquired = context.Event()
    crashing = context.Process(
        target=_exit_while_holding_lock,
        args=(tmp_path, first_acquired),
    )
    try:
        crashing.start()
        assert first_acquired.wait(timeout=10)
        crashing.join(timeout=10)
    finally:
        if crashing.is_alive():
            crashing.terminate()
            crashing.join(timeout=10)
    assert crashing.exitcode == 0

    second_acquired = context.Event()
    successor = context.Process(
        target=_acquire_lock_and_report,
        args=(tmp_path, second_acquired),
    )
    try:
        successor.start()
        assert second_acquired.wait(timeout=10)
        successor.join(timeout=10)
    finally:
        if successor.is_alive():
            successor.terminate()
            successor.join(timeout=10)

    assert successor.exitcode == 0


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_kind"),
    [
        ("api", "failed", "permission_denied"),
        ("http_400", "failed", "http"),
        ("http_502", "unknown", "http"),
        ("pre_send", "failed", "transport"),
        ("post_send", "unknown", "transport"),
        ("invalid_response", "unknown", "invalid_response"),
        ("invalid_media_id", "unknown", "invalid_response"),
    ],
)
def test_draft_add_outcomes_are_classified_without_retry(
    tmp_path, failure, expected_status, expected_kind
):
    preview = _make_preview(tmp_path)
    client = _Client(failure=failure)

    result = create_draft(preview, client, ReceiptStore(tmp_path))

    assert result.status == expected_status
    assert result.receipt is None
    assert result.error_kind == expected_kind
    assert result.uploaded_assets == ("body:images/body.png", "cover:cover.png")
    assert len(_draft_calls(client)) == 1
    assert not (tmp_path / "receipt.json").exists()


@pytest.mark.parametrize(
    ("failure", "uploaded"),
    [
        ("body", ()),
        ("cover", ("body:images/body.png",)),
    ],
)
def test_upload_failure_lists_only_irreversible_assets(tmp_path, failure, uploaded):
    preview = _make_preview(tmp_path)
    client = _Client(failure=failure)

    result = create_draft(preview, client, ReceiptStore(tmp_path))

    assert result.status == "failed"
    assert result.error_kind == "transport"
    assert result.uploaded_assets == uploaded
    assert _draft_calls(client) == []
    assert not (tmp_path / "receipt.json").exists()
    assert all(not os.path.isabs(asset.split(":", 1)[-1]) for asset in result.uploaded_assets)
    assert _ACCESS_TOKEN_MARKER not in repr(result)


@pytest.mark.parametrize(
    "receipt_text",
    [
        "not-json",
        "[]",
        "{}",
        json.dumps(
            {
                "preview_hash": "0" * 64,
                "media_id": "draft-id",
                "title": "title",
                "submitted_at": "2026-07-29T00:00:00Z",
                "account_id": _ACCOUNT_ID,
                "extra": "not allowed",
            }
        ),
    ],
)
def test_corrupt_receipt_fails_closed_without_network(tmp_path, receipt_text):
    preview = _make_preview(tmp_path)
    (tmp_path / "receipt.json").write_text(receipt_text, encoding="utf-8")
    client = _Client()

    result = create_draft(preview, client, ReceiptStore(tmp_path))

    assert result.status == "failed"
    assert result.error_kind == "receipt_invalid"
    assert client.calls == []


def test_receipt_for_a_different_hash_does_not_suppress_submission(tmp_path):
    preview = _make_preview(tmp_path)
    store = ReceiptStore(tmp_path)
    store.save(
        DraftReceipt(
            preview_hash="0" * 64,
            media_id="old-draft-id",
            title="旧标题",
            submitted_at="2026-07-28T00:00:00Z",
            account_id=_ACCOUNT_ID,
        )
    )
    client = _Client()

    result = create_draft(preview, client, store)

    assert result.status == "success"
    assert result.receipt is not None
    assert result.receipt.preview_hash == preview.preview_hash
    assert len(_draft_calls(client)) == 1


def test_same_hash_receipt_with_wrong_account_is_not_trusted(tmp_path):
    preview = _make_preview(tmp_path)
    store = ReceiptStore(tmp_path)
    store.save(
        DraftReceipt(
            preview_hash=preview.preview_hash,
            media_id="old-draft-id",
            title=preview.title,
            submitted_at="2026-07-28T00:00:00Z",
            account_id="sha256:ffffffffffffffff",
        )
    )
    client = _Client()

    result = create_draft(preview, client, store)

    assert result.status == "failed"
    assert result.error_kind == "receipt_invalid"
    assert client.calls == []


def test_changed_article_rejects_stale_preview_hash_without_network(tmp_path):
    preview = _make_preview(tmp_path)
    (tmp_path / "article.md").write_text(
        (tmp_path / "article.md").read_text(encoding="utf-8") + "已修改\n",
        encoding="utf-8",
    )
    client = _Client()

    result = create_draft(preview, client, ReceiptStore(tmp_path))

    assert result.status == "failed"
    assert result.error_kind == "preview_changed"
    assert client.calls == []
    assert not (tmp_path / "receipt.json").exists()


def test_changed_image_rejects_stale_preview_hash_without_network(tmp_path):
    preview = _make_preview(tmp_path)
    _write_png(tmp_path / "images" / "body.png", (210, 20, 20))
    client = _Client()

    result = create_draft(preview, client, ReceiptStore(tmp_path))

    assert result.status == "failed"
    assert result.error_kind == "preview_changed"
    assert client.calls == []
    assert not (tmp_path / "receipt.json").exists()


def test_asset_changed_after_hash_check_is_not_uploaded(tmp_path):
    preview = _make_preview(tmp_path)
    store = ReceiptStore(tmp_path)
    original_load = store.load

    def load_then_change_asset():
        receipt = original_load()
        _write_png(tmp_path / "images" / "body.png", (200, 10, 10))
        return receipt

    store.load = load_then_change_asset
    client = _Client()

    result = create_draft(preview, client, store)

    assert result.status == "failed"
    assert result.error_kind == "receipt_invalid"
    assert result.uploaded_assets == ()
    assert client.calls == []
    assert not (tmp_path / "receipt.json").exists()


def test_receipt_store_must_be_bound_to_preview_article_directory(tmp_path):
    article_dir = tmp_path / "article"
    article_dir.mkdir()
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    preview = _make_preview(article_dir)
    client = _Client()

    result = create_draft(preview, client, ReceiptStore(other_dir))

    assert result.status == "failed"
    assert result.error_kind == "preview_changed"
    assert client.calls == []
    assert not (other_dir / "receipt.json").exists()


def test_symlink_receipt_is_not_followed_or_overwritten(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks are unavailable")
    preview = _make_preview(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_text("outside", encoding="utf-8")
    (tmp_path / "receipt.json").symlink_to(outside)
    client = _Client()

    result = create_draft(preview, client, ReceiptStore(tmp_path))

    assert result.status == "failed"
    assert result.error_kind == "receipt_invalid"
    assert client.calls == []
    assert outside.read_text(encoding="utf-8") == "outside"


def test_symlink_lock_file_is_not_followed(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks are unavailable")
    preview = _make_preview(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.lock"
    outside.write_text("outside", encoding="utf-8")
    (tmp_path / ".wenshu-wechat-draft.lock").symlink_to(outside)
    client = _Client()

    result = create_draft(preview, client, ReceiptStore(tmp_path))

    assert result.status == "failed"
    assert result.error_kind == "receipt_invalid"
    assert client.calls == []
    assert outside.read_text(encoding="utf-8") == "outside"


def test_non_regular_lock_file_is_rejected_without_network(tmp_path):
    preview = _make_preview(tmp_path)
    (tmp_path / ".wenshu-wechat-draft.lock").mkdir()
    client = _Client()

    result = create_draft(preview, client, ReceiptStore(tmp_path))

    assert result.status == "failed"
    assert result.error_kind == "receipt_invalid"
    assert client.calls == []


def test_hard_link_lock_file_is_rejected_without_mutating_target(tmp_path):
    if not hasattr(os, "link"):
        pytest.skip("hard links are unavailable")
    preview = _make_preview(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-hard-link.lock"
    outside.write_text("outside", encoding="utf-8")
    os.link(outside, tmp_path / ".wenshu-wechat-draft.lock")
    client = _Client()

    result = create_draft(preview, client, ReceiptStore(tmp_path))

    assert result.status == "failed"
    assert result.error_kind == "receipt_invalid"
    assert client.calls == []
    assert outside.read_text(encoding="utf-8") == "outside"


def test_existing_lock_file_permissions_are_restricted(tmp_path):
    preview = _make_preview(tmp_path)
    lock_path = tmp_path / ".wenshu-wechat-draft.lock"
    lock_path.write_bytes(b"")
    lock_path.chmod(0o666)

    result = create_draft(preview, _Client(), ReceiptStore(tmp_path))

    assert result.status == "success"
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


def test_replaced_receipt_directory_is_rejected_without_network(tmp_path):
    article_directory = tmp_path / "article"
    article_directory.mkdir()
    preview = _make_preview(article_directory)
    store = ReceiptStore(article_directory)
    moved_directory = tmp_path / "moved"
    article_directory.rename(moved_directory)
    article_directory.mkdir()
    client = _Client()

    result = create_draft(preview, client, store)

    assert result.status == "failed"
    assert result.error_kind == "receipt_invalid"
    assert client.calls == []
    assert not (article_directory / ".wenshu-wechat-draft.lock").exists()


@pytest.mark.parametrize(
    "source_url",
    [
        "javascript:alert(1)",
        "https://user:password@example.test/source",
        "http://user@example.test/source",
        "https://example.test:not-a-port/source",
    ],
)
def test_non_http_or_credential_source_url_is_omitted_from_wechat_payload(
    tmp_path, source_url
):
    preview = _make_preview(tmp_path, source_url=source_url)
    client = _Client()

    result = create_draft(preview, client, ReceiptStore(tmp_path))

    assert result.status == "success"
    article = _draft_calls(client)[0]["json"]["articles"][0]
    assert "content_source_url" not in article


def test_preview_object_hash_tampering_is_rejected_before_receipt_lookup(tmp_path):
    preview = _make_preview(tmp_path)
    (tmp_path / "receipt.json").write_text("not-json", encoding="utf-8")
    tampered = replace(preview, preview_hash="f" * 64)
    client = _Client()

    result = create_draft(tampered, client, ReceiptStore(tmp_path))

    assert result.status == "failed"
    assert result.error_kind == "preview_changed"
    assert client.calls == []
