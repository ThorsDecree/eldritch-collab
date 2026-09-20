from __future__ import annotations

import pytest
import hashlib
import json
from pathlib import Path

from vestigia_mcp.adapters.archive import ArchiveError
from vestigia_mcp.archive_mutation import ArchiveMutationStore
from vestigia_mcp.policy import DEFAULT_CAPABILITIES
from vestigia_mcp.porchlight import build_snapshot, is_latest_path
from vestigia_mcp.porchlight_share import PorchlightShareRequest, PorchlightShareService


def make_share_service(tmp_path: Path) -> tuple[PorchlightShareService, Path]:
    live = tmp_path / "live"
    for relative in (
        "Modules/Porchlight/latest",
        "Modules/Porchlight/history",
        "Modules/Porchlight/receipts",
        "Modules/Porchlight/images",
    ):
        (live / relative).mkdir(parents=True)
    store = ArchiveMutationStore(
        live,
        tmp_path / "state",
        "test-deployment",
        write_prefixes=("Modules/Porchlight",),
    )

    def reader(path: str) -> str | None:
        target = live / Path(*path.split("/"))
        return target.read_text(encoding="utf-8") if target.exists() else None

    return PorchlightShareService(store, reader), live


def test_snapshot_separates_control_metadata_from_searchable_body() -> None:
    artifact = build_snapshot(
        url="https://www.reddit.com/r/test/comments/abc/?b=2&a=1#reply",
        title="A thread",
        content="The reply that matters.",
        mode="page",
        captured_at="2026-09-19T12:00:00+00:00",
    )

    assert "The reply that matters." in artifact.body
    assert "reddit.com" not in artifact.body
    assert "A thread" not in artifact.body
    assert artifact.receipt["source_url"] == (
        "https://www.reddit.com/r/test/comments/abc?a=1&b=2"
    )


def test_snapshot_paths_keep_latest_stable_and_history_immutable() -> None:
    artifact = build_snapshot(
        url="https://example.test/page",
        title="Page",
        content="body",
        mode="selection",
        captured_at="2026-09-19T12:00:00+00:00",
    )

    assert artifact.latest_path.startswith("Porchlight/latest/")
    assert artifact.history_path.startswith("Porchlight/history/")
    assert artifact.receipt_path.startswith("Porchlight/receipts/")
    assert is_latest_path(artifact.latest_path)
    assert is_latest_path("Modules/Porchlight/latest/example.md")
    assert not is_latest_path(artifact.history_path)
    assert artifact.latest_path.endswith(".md")
    assert artifact.history_path.endswith(".md")
    assert artifact.receipt_path.endswith(".json")


def test_snapshot_rejects_invalid_mode_timestamp_and_body() -> None:
    with pytest.raises(ValueError):
        build_snapshot("https://example.test", "", "body", "raw", None, None)
    with pytest.raises(ValueError):
        build_snapshot(
            "https://example.test", "", "body", "page", "not-a-date", None
        )
    with pytest.raises(ValueError):
        build_snapshot("https://example.test", "", "", "page", None, None)
    with pytest.raises(ValueError):
        build_snapshot("https://example.test", "", "bad\x00body", "page", None, None)


def test_snapshot_rejects_oversize_body_and_bad_predecessor_hash() -> None:
    with pytest.raises(ValueError, match="byte ceiling"):
        build_snapshot("https://example.test", "", "x" * 1_000_001, "page")
    with pytest.raises(ValueError, match="previous_snapshot_sha256"):
        build_snapshot(
            "https://example.test", "", "body", "update", previous_snapshot_sha256="nope"
        )


def test_snapshot_rejects_non_web_urls() -> None:
    with pytest.raises(ValueError):
        build_snapshot("javascript:alert(1)", "", "body", "page", None, None)


def test_policy_catalog_exposes_porchlight_staging() -> None:
    names = {capability.name for capability in DEFAULT_CAPABILITIES}
    assert "archive.stage_porchlight" in names


def test_direct_share_writes_modules_namespace_and_optional_screenshot(
    tmp_path: Path,
) -> None:
    service, live = make_share_service(tmp_path)
    result = service.share(
        PorchlightShareRequest(
            url="https://example.test/page",
            title="Example",
            content="readable body",
            mode="page",
            captured_at="2026-09-20T12:00:00+00:00",
            screenshot_png=b"\x89PNG\r\n\x1a\nvisible",
        )
    )

    assert result["shared_directly"] is True
    assert result["consent_basis"] == "explicit_porchlight_action"
    assert result["latest_path"].startswith("Modules/Porchlight/latest/")
    assert result["history_path"].startswith("Modules/Porchlight/history/")
    assert result["receipt_path"].startswith("Modules/Porchlight/receipts/")
    assert result["screenshot_path"].startswith("Modules/Porchlight/images/")
    assert "stage_id" not in result
    assert "next_step" not in result

    receipt = json.loads(
        (live / Path(*str(result["receipt_path"]).split("/"))).read_text(
            encoding="utf-8"
        )
    )
    assert receipt["screenshot"]["mime_type"] == "image/png"
    assert "visible" not in json.dumps(receipt)


def test_direct_share_same_latest_is_an_unchanged_result(tmp_path: Path) -> None:
    service, _ = make_share_service(tmp_path)
    request = PorchlightShareRequest(
        "https://example.test/page", "Example", "same body", "page",
        "2026-09-20T12:00:00+00:00",
    )
    first = service.share(request)
    second = service.share(
        PorchlightShareRequest(
            request.url,
            request.title,
            request.content,
            request.mode,
            "2026-09-20T12:01:00+00:00",
        )
    )

    assert first["canonical_changed"] is True
    assert second["unchanged"] is True
    assert second["canonical_changed"] is False
    assert second["history_path"] is None


def test_direct_update_refuses_a_stale_latest_base(tmp_path: Path) -> None:
    service, _ = make_share_service(tmp_path)
    first = service.share(
        PorchlightShareRequest(
            "https://example.test/page",
            "Example",
            "first body",
            "page",
            "2026-09-20T12:00:00+00:00",
        )
    )
    first_sha = hashlib.sha256(b"first body").hexdigest()
    service.share(
        PorchlightShareRequest(
            "https://example.test/page",
            "Example",
            "second body",
            "update",
            "2026-09-20T12:01:00+00:00",
            first_sha,
        )
    )

    with pytest.raises(ArchiveError, match="expected_base_sha256"):
        service.share(
            PorchlightShareRequest(
                "https://example.test/page",
                "Example",
                "third body",
                "update",
                "2026-09-20T12:02:00+00:00",
                first_sha,
            )
        )
    assert first["latest_path"].startswith("Modules/Porchlight/latest/")
