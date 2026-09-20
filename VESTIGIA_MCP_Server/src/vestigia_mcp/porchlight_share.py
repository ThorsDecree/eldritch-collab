from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from .adapters.archive import ArchiveError
from .archive_mutation import ArchiveMutationStore, BundleEntry
from .porchlight import SnapshotArtifact, build_snapshot


PORCHLIGHT_DIRECT_PREFIX = "Modules/Porchlight"


@dataclass(frozen=True)
class PorchlightShareRequest:
    url: str
    title: str
    content: str
    mode: str
    captured_at: str | None = None
    previous_snapshot_sha256: str | None = None
    screenshot_png: bytes | None = None


class PorchlightShareService:
    """Explicit resident Porchlight shares backed by one Archive bundle."""

    def __init__(
        self,
        mutations: ArchiveMutationStore,
        latest_reader: Callable[[str], str | None] | None = None,
        *,
        screenshot_max_bytes: int = 2_000_000,
    ) -> None:
        self._mutations = mutations
        self._latest_reader = latest_reader
        self._screenshot_max_bytes = screenshot_max_bytes

    def share(self, request: PorchlightShareRequest) -> dict[str, object]:
        screenshot = request.screenshot_png
        if screenshot is not None and len(screenshot) > self._screenshot_max_bytes:
            raise ArchiveError(
                "Porchlight screenshot exceeds byte ceiling "
                f"({len(screenshot)} > {self._screenshot_max_bytes})"
            )

        previous = request.previous_snapshot_sha256
        artifact = self._build(request, previous)
        current_body = self._read_latest(artifact.latest_path)
        current_sha = (
            hashlib.sha256(current_body.encode("utf-8")).hexdigest()
            if current_body is not None
            else None
        )
        if current_body == artifact.body:
            return {
                "shared_directly": True,
                "unchanged": True,
                "canonical_changed": False,
                "consent_basis": "explicit_porchlight_action",
                "capture_id": artifact.capture_id,
                "source_key": artifact.source_key,
                "latest_sha256": artifact.receipt["content_sha256"],
                "latest_path": artifact.latest_path,
                "history_path": None,
                "receipt_path": None,
                "screenshot_path": None,
            }

        if request.mode.strip().lower() == "update" and previous is None:
            previous = current_sha
            artifact = self._build(request, previous)
        elif request.mode.strip().lower() == "update" and current_sha is None:
            raise ArchiveError("Porchlight update requires an existing latest snapshot")

        receipt = dict(artifact.receipt)
        bundle_entries = [
            BundleEntry(artifact.latest_path, artifact.body.encode("utf-8"), "text"),
            BundleEntry(
                artifact.history_path,
                artifact.body.encode("utf-8"),
                "text",
                expected_base_sha256="absent",
            ),
        ]
        screenshot_path: str | None = None
        if screenshot is not None:
            screenshot_path = (
                f"{PORCHLIGHT_DIRECT_PREFIX}/images/"
                f"{artifact.source_key}/{artifact.capture_id}.png"
            )
            screenshot_sha = hashlib.sha256(screenshot).hexdigest()
            receipt["screenshot"] = {
                "path": screenshot_path,
                "sha256": screenshot_sha,
                "bytes": len(screenshot),
                "mime_type": "image/png",
            }
            bundle_entries.append(BundleEntry(screenshot_path, screenshot, "png"))

        receipt_body = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        bundle_entries.append(
            BundleEntry(
                artifact.receipt_path,
                receipt_body.encode("utf-8"),
                "json",
                expected_base_sha256="absent",
            )
        )
        expected: dict[str, str] = {}
        if request.mode.strip().lower() == "update" and previous is not None:
            expected[artifact.latest_path] = previous
        result = self._mutations.share_bundle(
            bundle_entries,
            expected_base_sha256_by_path=expected,
            reason=f"Porchlight {artifact.capture_id} direct share",
            audit_metadata={
                "consent_basis": "explicit_porchlight_action",
                "source_key": artifact.source_key,
                "capture_id": artifact.capture_id,
            },
        )
        return {
            "shared_directly": True,
            "unchanged": False,
            "consent_basis": "explicit_porchlight_action",
            "capture_id": artifact.capture_id,
            "source_key": artifact.source_key,
            "latest_sha256": artifact.receipt["content_sha256"],
            "latest_path": artifact.latest_path,
            "history_path": artifact.history_path,
            "receipt_path": artifact.receipt_path,
            "screenshot_path": screenshot_path,
            "canonical_changed": result["canonical_changed"],
            "atomic_bundle": result["atomic_bundle"],
            "entries": result["entries"],
        }

    def _build(
        self, request: PorchlightShareRequest, previous: str | None
    ) -> SnapshotArtifact:
        try:
            return build_snapshot(
                request.url,
                request.title,
                request.content,
                request.mode,
                request.captured_at,
                previous,
                path_prefix=PORCHLIGHT_DIRECT_PREFIX,
            )
        except ValueError as exc:
            raise ArchiveError(str(exc)) from exc

    def _read_latest(self, path: str) -> str | None:
        if self._latest_reader is None:
            return None
        return self._latest_reader(path)
