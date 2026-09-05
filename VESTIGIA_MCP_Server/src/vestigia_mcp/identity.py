from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from .adapters.archive import ArchiveError, ArchiveSource
from .config import Settings
from .policy import PolicyEngine


ANCHOR_FINGERPRINT_PATHS = (
    "manifest.md",
    "00_Bootloader/house_index.json",
)


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _path(value: Path | None) -> str | None:
    return str(value) if value is not None else None


def config_fingerprint(settings: Settings) -> str:
    """Fingerprint effective non-secret MCP configuration without exposing it inline."""
    return _digest(
        {
            "live_archive_root": _path(settings.live_archive_root),
            "snapshot_archive_root": _path(settings.snapshot_archive_root),
            "state_dir": _path(settings.state_dir),
            "deployment_id": settings.deployment_id,
            "archive_text_max_bytes": settings.archive_text_max_bytes,
            "runtime_home": _path(settings.runtime_home),
            "runtime_env_file": _path(settings.runtime_env_file),
        }
    )


def policy_fingerprint(policy: PolicyEngine) -> str:
    return _digest(
        [
            {
                "name": capability.name,
                "effect": capability.effect.value,
                "default": capability.default.value,
                "description": capability.description,
            }
            for capability in policy.capabilities()
        ]
    )


def archive_witness(source: ArchiveSource) -> dict[str, object]:
    stats = source.stats()
    anchors: list[dict[str, object]] = []
    for path in ANCHOR_FINGERPRINT_PATHS:
        try:
            entry = source.entry(path)
        except ArchiveError as exc:
            anchors.append({"path": path, "available": False, "error": str(exc)})
            continue
        anchors.append(
            {
                "path": path,
                "available": entry is not None,
                "size": entry.size if entry is not None else None,
                "sha256": entry.sha256 if entry is not None else None,
            }
        )
    witness_material = {
        "kind": stats.kind,
        "file_count": stats.file_count,
        "total_bytes": stats.total_bytes,
        "excluded_paths": list(stats.excluded_paths),
        "anchors": anchors,
    }
    return {
        **asdict(stats),
        "anchor_fingerprint_scope": list(ANCHOR_FINGERPRINT_PATHS),
        "anchors": anchors,
        "witness_digest_sha256": _digest(witness_material),
        "whole_archive_digest": None,
        "whole_archive_digest_computed": False,
    }


def system_identity(
    *,
    server_version: str,
    settings: Settings,
    policy: PolicyEngine,
    source_for: Callable[[str], ArchiveSource],
    runtime_status: Callable[[], dict[str, object]],
) -> dict[str, object]:
    archives: dict[str, object] = {}
    for name in ("live", "snapshot"):
        try:
            archives[name] = {"available": True, **archive_witness(source_for(name))}
        except ArchiveError as exc:
            archives[name] = {
                "available": False,
                "configured": (
                    settings.live_archive_root is not None
                    if name == "live"
                    else settings.snapshot_archive_root is not None
                ),
                "error": str(exc),
            }

    source_commit = os.getenv("VESTIGIA_MCP_SOURCE_COMMIT", "").strip() or None
    source_state = os.getenv("VESTIGIA_MCP_SOURCE_STATE", "").strip() or None
    source_known = source_commit is not None or source_state is not None
    development = ".dev" in server_version

    return {
        "schema_version": "vestigia.system-identity.v0.1",
        "server": {
            "name": "VESTIGIA MCP",
            "package_version": server_version,
            "deployment_id": settings.deployment_id,
        },
        "source_revision": {
            "commit": source_commit,
            "state": source_state,
            "known": source_known,
            "evidence": (
                "operator/build supplied environment metadata"
                if source_known
                else "not embedded; no git subprocess is invoked by system.identity"
            ),
        },
        "configuration": {
            "fingerprint_sha256": config_fingerprint(settings),
            "secret_values_included": False,
            "note": (
                "The digest includes configured local paths and deployment settings but the "
                "endpoint does not serialize credential values into the fingerprint material."
            ),
        },
        "capability_registry": {
            "authority": "VESTIGIA MCP executable PolicyEngine",
            "capability_count": len(policy.capabilities()),
            "digest_sha256": policy_fingerprint(policy),
        },
        "archive": archives,
        "runtime": runtime_status(),
        "qualification": {
            "status": "development_unqualified" if development else "release_unverified",
            "basis": "package_version_only",
            "ci_status_embedded": False,
            "note": (
                "Identity locates the deployment; it does not turn package identity or a past "
                "test run into proof of current qualification."
            ),
        },
        "invariants": {
            "identity_is_qualification": False,
            "qualification_is_authority": False,
            "receipt_is_memory": False,
        },
    }
