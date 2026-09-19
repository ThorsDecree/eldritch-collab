from __future__ import annotations

import pytest

from vestigia_mcp.porchlight import build_snapshot, is_latest_path


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
