from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


PORCHLIGHT_SCHEMA_VERSION = "vestigia.porchlight.snapshot.v0.1"
PORCHLIGHT_MODES = frozenset({"selection", "page", "update"})
PORCHLIGHT_MAX_BYTES = 1_000_000
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def canonical_source_url(url: str) -> str:
    """Return the stable, credential-free identity URL for one web source."""
    raw = str(url).strip()
    if not raw or len(raw) > 8192:
        raise ValueError("Porchlight URL must be non-empty and at most 8192 characters")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Porchlight URL must use http or https")
    if parsed.username or parsed.password:
        raise ValueError("Porchlight URL must not contain credentials")
    if not parsed.hostname:
        raise ValueError("Porchlight URL must include a hostname")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Porchlight URL has an invalid port") from exc
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower()
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{netloc}:{port}"
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)), doseq=True)
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, query, ""))


def source_key_for_url(url: str) -> str:
    """Return a path-safe source identity without exposing the source URL in paths."""
    return hashlib.sha256(canonical_source_url(url).encode("utf-8")).hexdigest()[:24]


def _capture_time(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    raw = str(value).strip()
    if not raw:
        raise ValueError("Porchlight captured_at must not be empty")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Porchlight captured_at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("Porchlight captured_at must include a timezone")
    return parsed.astimezone(UTC)


def _isoformat(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _validate_hash(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not _SHA256.fullmatch(normalized):
        raise ValueError(f"Porchlight {field} must be a lowercase SHA-256 digest")
    return normalized


@dataclass(frozen=True)
class SnapshotArtifact:
    source_key: str
    capture_id: str
    latest_path: str
    history_path: str
    receipt_path: str
    body: str
    receipt_body: str
    receipt: dict[str, object]


def build_snapshot(
    url: str,
    title: str,
    content: str,
    mode: str,
    captured_at: str | None = None,
    previous_snapshot_sha256: str | None = None,
    path_prefix: str = "Porchlight",
) -> SnapshotArtifact:
    source_url = canonical_source_url(url)
    normalized_mode = str(mode).strip().lower()
    if normalized_mode not in PORCHLIGHT_MODES:
        raise ValueError(f"Porchlight mode must be one of: {', '.join(sorted(PORCHLIGHT_MODES))}")
    body = str(content).strip("\n")
    if not body:
        raise ValueError("Porchlight content must not be empty")
    if "\x00" in body:
        raise ValueError("Porchlight content must not contain NUL characters")
    if len(body.encode("utf-8")) > PORCHLIGHT_MAX_BYTES:
        raise ValueError(f"Porchlight content exceeds byte ceiling ({PORCHLIGHT_MAX_BYTES} bytes)")
    if len(str(title)) > 1000:
        raise ValueError("Porchlight title must be at most 1000 characters")
    captured = _capture_time(captured_at)
    previous = _validate_hash(previous_snapshot_sha256, "previous_snapshot_sha256")
    content_sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()
    source_key = source_key_for_url(source_url)
    capture_id = f"pl_{captured.strftime('%Y%m%dT%H%M%SZ')}_{content_sha256[:16]}"
    normalized_prefix = str(path_prefix).strip("/")
    if not normalized_prefix or ".." in normalized_prefix.split("/"):
        raise ValueError("Porchlight path_prefix is invalid")
    receipt: dict[str, object] = {
        "schema_version": PORCHLIGHT_SCHEMA_VERSION,
        "capture_id": capture_id,
        "source_key": source_key,
        "source_url": source_url,
        "title": str(title).strip(),
        "mode": normalized_mode,
        "captured_at": _isoformat(captured),
        "content_sha256": content_sha256,
        "content_bytes": len(body.encode("utf-8")),
        "previous_snapshot_sha256": previous,
    }
    receipt_body = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    return SnapshotArtifact(
        source_key=source_key,
        capture_id=capture_id,
        latest_path=f"{normalized_prefix}/latest/{source_key}.md",
        history_path=f"{normalized_prefix}/history/{source_key}_{capture_id}.md",
        receipt_path=f"{normalized_prefix}/receipts/{source_key}_{capture_id}.json",
        body=body,
        receipt_body=receipt_body,
        receipt=receipt,
    )


def is_latest_path(path: str) -> bool:
    normalized = str(path).replace("\\", "/")
    return normalized.startswith(("Porchlight/latest/", "Modules/Porchlight/latest/"))
