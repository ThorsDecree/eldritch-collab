from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
import shutil
import subprocess
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol, Sequence

from .config import ResolvedConfig
from .db import ContinuityDB
from .models import ImageResult
from .utils import TokenCounter, new_id, sha256_file, sha256_text


IMAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS image_assets (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    artifact_id TEXT,
    content_hash TEXT NOT NULL,
    path TEXT NOT NULL,
    original_filename TEXT,
    media_type TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    source_kind TEXT NOT NULL,
    source_json TEXT NOT NULL DEFAULT '{}',
    privacy TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(resident_id, content_hash)
);

CREATE TABLE IF NOT EXISTS image_events (
    id TEXT PRIMARY KEY,
    image_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (image_id) REFERENCES image_assets(id)
);

CREATE TABLE IF NOT EXISTS image_interpretations (
    id TEXT PRIMARY KEY,
    image_id TEXT NOT NULL,
    resident_id TEXT NOT NULL,
    route TEXT NOT NULL,
    model TEXT NOT NULL,
    detail TEXT NOT NULL,
    question_category TEXT NOT NULL,
    question_hash TEXT NOT NULL,
    cache_key TEXT NOT NULL UNIQUE,
    result_text TEXT NOT NULL,
    result_hash TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (image_id) REFERENCES image_assets(id)
);

CREATE TABLE IF NOT EXISTS image_share_drafts (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    image_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_turn_id TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY (image_id) REFERENCES image_assets(id)
);

CREATE TABLE IF NOT EXISTS image_jobs (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    delivery_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    error_type TEXT,
    error_hash TEXT,
    created_turn_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    notified_at TEXT
);

CREATE TABLE IF NOT EXISTS image_cards (
    image_id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    alias TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    alt_text TEXT NOT NULL DEFAULT '',
    visible_text TEXT NOT NULL DEFAULT '',
    people_json TEXT NOT NULL DEFAULT '[]',
    places_json TEXT NOT NULL DEFAULT '[]',
    motifs_json TEXT NOT NULL DEFAULT '[]',
    moods_json TEXT NOT NULL DEFAULT '[]',
    uses_json TEXT NOT NULL DEFAULT '[]',
    avoid_when_json TEXT NOT NULL DEFAULT '[]',
    resident_note TEXT NOT NULL DEFAULT '',
    inherited_framing TEXT NOT NULL DEFAULT '',
    present_resonance TEXT NOT NULL DEFAULT '',
    adoption_state TEXT NOT NULL DEFAULT 'unreviewed',
    summary_provenance TEXT NOT NULL DEFAULT 'none',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (image_id) REFERENCES image_assets(id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS image_cards_fts USING fts5(
    image_id UNINDEXED,
    alias,
    summary,
    visible_text,
    people,
    places,
    motifs,
    moods,
    uses,
    resident_note,
    tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS image_pockets (
    resident_id TEXT NOT NULL,
    pocket TEXT NOT NULL,
    image_id TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (resident_id, pocket, image_id),
    FOREIGN KEY (image_id) REFERENCES image_assets(id)
);

CREATE INDEX IF NOT EXISTS idx_image_assets_resident
ON image_assets(resident_id, created_at);
CREATE INDEX IF NOT EXISTS idx_image_assets_artifact
ON image_assets(artifact_id);
CREATE INDEX IF NOT EXISTS idx_image_events_asset
ON image_events(image_id, created_at);
CREATE INDEX IF NOT EXISTS idx_image_interpretations_asset
ON image_interpretations(image_id, created_at);
CREATE INDEX IF NOT EXISTS idx_image_jobs_status
ON image_jobs(resident_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_image_cards_resident
ON image_cards(resident_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_image_pockets_image
ON image_pockets(resident_id, image_id);
"""


class ImageProvider(Protocol):
    name: str
    model: str

    def generate(self, prompt: str, *, count: int, size: str, quality: str) -> list[bytes]:
        ...

    def edit(
        self,
        prompt: str,
        *,
        source_images: Sequence[Path],
        count: int,
        size: str,
        quality: str,
    ) -> list[bytes]:
        ...


class VisionProvider(Protocol):
    name: str
    model: str

    def inspect(self, image: Path, *, question: str, detail: str) -> str:
        ...


class OpenAIImageProvider:
    name = "openai"

    def __init__(self, config: ResolvedConfig) -> None:
        key = config.secret("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is required for live image generation")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install project dependencies before using images") from exc
        kwargs = {"api_key": key}
        base_url = str(config.get("provider.base_url", "")).strip()
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)
        self.model = str(config.get("models.image"))

    def generate(self, prompt: str, *, count: int, size: str, quality: str) -> list[bytes]:
        kwargs = {"model": self.model, "prompt": prompt, "n": count}
        if size:
            kwargs["size"] = size
        if quality:
            kwargs["quality"] = quality
        response = self.client.images.generate(**kwargs)
        return [base64.b64decode(item.b64_json) for item in response.data if item.b64_json]

    def edit(
        self,
        prompt: str,
        *,
        source_images: Sequence[Path],
        count: int,
        size: str,
        quality: str,
    ) -> list[bytes]:
        kwargs = {"model": self.model, "prompt": prompt, "n": count}
        if size:
            kwargs["size"] = size
        if quality:
            kwargs["quality"] = quality
        with ExitStack() as stack:
            handles = [stack.enter_context(path.open("rb")) for path in source_images]
            kwargs["image"] = handles
            response = self.client.images.edit(**kwargs)
        return [base64.b64decode(item.b64_json) for item in response.data if item.b64_json]


class OpenAIVisionProvider:
    name = "openai"

    def __init__(self, config: ResolvedConfig) -> None:
        key = config.secret("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is required for live image interpretation")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install project dependencies before using vision") from exc
        kwargs = {"api_key": key}
        base_url = str(config.get("provider.base_url", "")).strip()
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)
        self.model = str(config.get("models.vision", "gpt-5-mini"))

    def inspect(self, image: Path, *, question: str, detail: str) -> str:
        media_type = mimetypes.guess_type(image.name)[0] or "image/png"
        encoded = base64.b64encode(image.read_bytes()).decode("ascii")
        response = self.client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": question},
                        {
                            "type": "input_image",
                            "image_url": f"data:{media_type};base64,{encoded}",
                            "detail": detail,
                        },
                    ],
                }
            ],
        )
        return str(response.output_text or "").strip()


class FakeImageProvider:
    name = "fake"
    model = "fake-image"
    _PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8A"
        "AQUBAScY42YAAAAASUVORK5CYII="
    )

    def generate(self, prompt: str, *, count: int, size: str, quality: str) -> list[bytes]:
        return [self._PNG for _ in range(count)]

    def edit(
        self,
        prompt: str,
        *,
        source_images: Sequence[Path],
        count: int,
        size: str,
        quality: str,
    ) -> list[bytes]:
        return [self._PNG for _ in range(count)]


class FakeVisionProvider:
    name = "fake"
    model = "fake-vision"

    def inspect(self, image: Path, *, question: str, detail: str) -> str:
        return f"[fake-vision:{detail}] {question.strip()}"


class ImageService:
    def __init__(
        self,
        config: ResolvedConfig,
        db: ContinuityDB,
        provider: ImageProvider | None = None,
        vision_provider: VisionProvider | None = None,
        *,
        fake: bool = False,
    ) -> None:
        self.config = config
        self.db = db
        self.home = config.home_path
        self.resident_id = str(config.get("resident.id"))
        self.room_id = str(config.get("room.id"))
        self.provider = provider
        self.vision_provider = vision_provider
        self.fake = fake
        self.counter = TokenCounter(str(config.get("models.image")))
        with self.db.connect() as connection:
            connection.executescript(IMAGE_SCHEMA)
            now = datetime.now(UTC).isoformat()
            connection.execute(
                """
                INSERT OR IGNORE INTO image_cards
                (image_id, resident_id, created_at, updated_at)
                SELECT id, resident_id, created_at, ?
                FROM image_assets WHERE resident_id=?
                """,
                (now, self.resident_id),
            )
            stale_before = (
                datetime.now(UTC)
                - timedelta(
                    seconds=max(
                        30,
                        int(self.config.get("images.job_stale_seconds", 900)),
                    )
                )
            ).isoformat()
            connection.execute(
                """
                UPDATE image_jobs SET status='queued', updated_at=?
                WHERE resident_id=? AND status='running' AND updated_at<?
                """,
                (datetime.now(UTC).isoformat(), self.resident_id, stale_before),
            )
        self._bootstrap_card_index()

    def _image_provider(self) -> ImageProvider:
        if self.provider is None:
            self.provider = FakeImageProvider() if self.fake else OpenAIImageProvider(self.config)
        return self.provider

    def _vision_provider(self) -> VisionProvider:
        if self.vision_provider is None:
            self.vision_provider = (
                FakeVisionProvider() if self.fake else OpenAIVisionProvider(self.config)
            )
        return self.vision_provider

    def generate(
        self,
        prompt: str,
        *,
        count: int = 1,
        confirmed: bool = False,
        turn_id: str | None = None,
    ) -> ImageResult:
        self._authorize(count, confirmed, editing=False)
        visual_prompt, visual_ids = self._visual_prompt(prompt)
        provider = self._image_provider()
        blobs = provider.generate(
            visual_prompt,
            count=count,
            size=str(self.config.get("images.default_size", "auto")),
            quality=str(self.config.get("images.default_quality", "auto")),
        )
        return self._save(
            blobs,
            operation="generate",
            prompt=visual_prompt,
            visual_ids=visual_ids,
            sources=[],
            turn_id=turn_id,
        )

    def edit(
        self,
        prompt: str,
        source_images: Sequence[str | Path],
        *,
        count: int = 1,
        confirmed: bool = False,
        turn_id: str | None = None,
    ) -> ImageResult:
        self._authorize(count, confirmed, editing=True)
        originals = self._preserve_sources([Path(item).resolve() for item in source_images])
        visual_prompt, visual_ids = self._visual_prompt(prompt)
        provider = self._image_provider()
        blobs = provider.edit(
            visual_prompt,
            source_images=originals,
            count=count,
            size=str(self.config.get("images.default_size", "auto")),
            quality=str(self.config.get("images.default_quality", "auto")),
        )
        return self._save(
            blobs,
            operation="edit",
            prompt=visual_prompt,
            visual_ids=visual_ids,
            sources=originals,
            turn_id=turn_id,
        )

    def edit_assets(
        self,
        prompt: str,
        image_ids: Sequence[str],
        *,
        count: int = 1,
        confirmed: bool = False,
        turn_id: str | None = None,
    ) -> ImageResult:
        if not image_ids:
            raise ValueError("image.edit requires at least one source image_id")
        paths = [self.resolve_path(image_id) for image_id in image_ids]
        return self.edit(
            prompt,
            paths,
            count=count,
            confirmed=confirmed,
            turn_id=turn_id,
        )

    def review(self, artifact_id: str, action: str, *, actor: str, reason: str = "") -> str:
        statuses = {
            "keep": "keepsake",
            "candidate": "canon_candidate",
            "accept": "accepted_canon",
            "reject": "rejected",
            "supersede": "superseded",
            "share": "shareable",
        }
        normalized = action.strip().lower()
        if normalized not in statuses:
            raise ValueError("action must be keep, candidate, accept, reject, supersede, or share")
        asset = self.get_asset(artifact_id)
        actual_artifact = asset.get("artifact_id") if asset else artifact_id
        if not asset:
            return self.db.append_artifact_event(
                artifact_id,
                event_type=normalized,
                status=statuses[normalized],
                actor=actor,
                reason=reason or f"artifact marked {statuses[normalized]}",
            )
        if actual_artifact:
            event_id = self.db.append_artifact_event(
                str(actual_artifact),
                event_type=normalized,
                status=statuses[normalized],
                actor=actor,
                reason=reason or f"artifact marked {statuses[normalized]}",
            )
        else:
            event_id = new_id("aev")
        with self.db.connect() as connection:
            if normalized == "share":
                connection.execute(
                    "UPDATE image_assets SET privacy='shareable' "
                    "WHERE id=? AND resident_id=?",
                    (str(asset["id"]), self.resident_id),
                )
            connection.execute(
                """
                INSERT INTO image_events
                (id, image_id, event_type, status, actor, reason, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, '{}', ?)
                """,
                (
                    new_id("iev"),
                    str(asset["id"]),
                    normalized,
                    statuses[normalized],
                    actor,
                    reason or f"image marked {statuses[normalized]}",
                    datetime.now(UTC).isoformat(),
                ),
            )
        return event_id

    def ingest_bytes(
        self,
        data: bytes,
        *,
        filename: str,
        source_kind: str = "received",
        source: dict[str, Any] | None = None,
        privacy: str = "private",
        artifact_id: str | None = None,
    ) -> dict[str, Any]:
        maximum = int(self.config.get("images.max_input_bytes", 20_000_000))
        if not data:
            raise ValueError("image data may not be empty")
        if len(data) > maximum:
            raise ValueError(f"image exceeds the configured {maximum}-byte input ceiling")
        clean_name = Path(filename).name or "image.png"
        suffix = Path(clean_name).suffix.lower()
        allowed = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
        if suffix not in allowed:
            raise ValueError("supported image types are PNG, JPEG, WEBP, and GIF")
        width, height, detected_format = self._validate_image_bytes(data)
        if detected_format not in {"PNG", "JPEG", "WEBP", "GIF"}:
            raise ValueError("the attachment is not a supported image")
        digest = hashlib.sha256(data).hexdigest()
        with self.db.connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM image_assets
                WHERE resident_id=? AND content_hash=?
                """,
                (self.resident_id, digest),
            ).fetchone()
        if existing:
            with self.db.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO image_events
                    (id, image_id, event_type, status, actor, reason,
                     payload_json, created_at)
                    VALUES (?, ?, 'seen_again', 'private', 'runtime',
                            'matching image bytes were shared again', ?, ?)
                    """,
                    (
                        new_id("iev"),
                        str(existing["id"]),
                        json.dumps(source or {}, ensure_ascii=False, sort_keys=True),
                        datetime.now(UTC).isoformat(),
                    ),
                )
            return self._asset_row(existing, reused=True)
        media_type = mimetypes.guess_type(clean_name)[0] or "application/octet-stream"
        shelf = self.home / "artifacts" / "images" / "shelf"
        shelf.mkdir(parents=True, exist_ok=True)
        canonical_suffix = ".jpg" if suffix == ".jpeg" else suffix
        target = shelf / f"{digest}{canonical_suffix}"
        if not target.exists():
            target.write_bytes(data)
        image_id = new_id("img")
        now = datetime.now(UTC).isoformat()
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO image_assets
                (id, resident_id, room_id, artifact_id, content_hash, path,
                 original_filename, media_type, width, height, source_kind,
                 source_json, privacy, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    image_id,
                    self.resident_id,
                    self.room_id,
                    artifact_id,
                    digest,
                    target.relative_to(self.home).as_posix(),
                    clean_name,
                    media_type,
                    width,
                    height,
                    source_kind,
                    json.dumps(source or {}, ensure_ascii=False, sort_keys=True),
                    privacy,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO image_events
                (id, image_id, event_type, status, actor, reason, payload_json, created_at)
                VALUES (?, ?, 'ingested', 'private', 'runtime',
                        'image entered the content-addressed shelf', '{}', ?)
                """,
                (new_id("iev"), image_id, now),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO image_cards
                (image_id, resident_id, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (image_id, self.resident_id, now, now),
            )
        self._refresh_card_fts(image_id)
        return self.get_asset(image_id) or {}

    def ingest_file(
        self,
        path: str | Path,
        *,
        source_kind: str = "received",
        source: dict[str, Any] | None = None,
        privacy: str = "private",
        artifact_id: str | None = None,
    ) -> dict[str, Any]:
        candidate = Path(path).resolve()
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        return self.ingest_bytes(
            candidate.read_bytes(),
            filename=candidate.name,
            source_kind=source_kind,
            source=source,
            privacy=privacy,
            artifact_id=artifact_id,
        )

    def get_asset(self, image_or_artifact_id: str) -> dict[str, Any] | None:
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM image_assets
                WHERE resident_id=? AND (id=? OR artifact_id=?)
                """,
                (self.resident_id, image_or_artifact_id, image_or_artifact_id),
            ).fetchone()
        return self._asset_row(row) if row else None

    def resolve_path(self, image_or_artifact_id: str) -> Path:
        asset = self.get_asset(image_or_artifact_id)
        if not asset:
            legacy = next(
                (
                    item
                    for item in self.db.list_artifacts(self.resident_id)
                    if str(item["id"]) == image_or_artifact_id
                ),
                None,
            )
            if not legacy:
                raise KeyError(f"unknown image: {image_or_artifact_id}")
            candidate = (self.home / str(legacy["path"])).resolve()
        else:
            candidate = (self.home / str(asset["path"])).resolve()
        try:
            candidate.relative_to(self.home.resolve())
        except ValueError as exc:
            raise PermissionError("image path leaves the resident house") from exc
        if not candidate.is_file() or candidate.is_symlink():
            raise FileNotFoundError("stored image is unavailable")
        return candidate

    def history(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                WITH latest AS (
                    SELECT image_id, status,
                           ROW_NUMBER() OVER (PARTITION BY image_id ORDER BY rowid DESC) AS rn
                    FROM image_events
                )
                SELECT a.*, COALESCE(latest.status, 'private') AS status
                FROM image_assets a
                LEFT JOIN latest ON latest.image_id=a.id AND latest.rn=1
                WHERE a.resident_id=?
                ORDER BY a.rowid DESC LIMIT ?
                """,
                (self.resident_id, min(100, max(1, int(limit)))),
            ).fetchall()
        return [self._asset_row(row) for row in rows]

    def card(self, image_id: str) -> dict[str, Any]:
        asset = self.get_asset(image_id)
        if not asset:
            raise KeyError(f"unknown image: {image_id}")
        actual_id = str(asset["id"])
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM image_cards WHERE image_id=? AND resident_id=?",
                (actual_id, self.resident_id),
            ).fetchone()
            pockets = connection.execute(
                """
                SELECT pocket FROM image_pockets
                WHERE resident_id=? AND image_id=? ORDER BY pocket
                """,
                (self.resident_id, actual_id),
            ).fetchall()
        if not row:
            now = datetime.now(UTC).isoformat()
            with self.db.connect() as connection:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO image_cards
                    (image_id, resident_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (actual_id, self.resident_id, now, now),
                )
            return self.card(actual_id)
        return self._card_row(row, asset=asset, pockets=[str(item["pocket"]) for item in pockets])

    def _bootstrap_card_index(self) -> None:
        """Promote existing cached readings without making any provider call."""

        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT image_id, summary, alt_text, visible_text
                FROM image_cards WHERE resident_id=? ORDER BY rowid
                """,
                (self.resident_id,),
            ).fetchall()
        for row in rows:
            image_id = str(row["image_id"])
            if not str(row["summary"]).strip() or not str(row["visible_text"]).strip():
                with self.db.connect() as connection:
                    interpretations = connection.execute(
                        """
                        SELECT route, result_text FROM image_interpretations
                        WHERE resident_id=? AND image_id=?
                        ORDER BY rowid DESC
                        """,
                        (self.resident_id, image_id),
                    ).fetchall()
                vision = next(
                    (
                        str(item["result_text"]).strip()
                        for item in interpretations
                        if str(item["route"]).startswith("vision")
                    ),
                    "",
                )
                ocr = next(
                    (
                        str(item["result_text"]).strip()
                        for item in interpretations
                        if str(item["route"]) == "ocr"
                    ),
                    "",
                )
                if vision or ocr:
                    with self.db.connect() as connection:
                        connection.execute(
                            """
                            UPDATE image_cards
                            SET summary=CASE WHEN summary='' THEN ? ELSE summary END,
                                alt_text=CASE WHEN alt_text='' THEN ? ELSE alt_text END,
                                visible_text=CASE WHEN visible_text='' THEN ? ELSE visible_text END,
                                summary_provenance=CASE
                                    WHEN summary_provenance='none'
                                    THEN 'cached_image_interpretation:migrated'
                                    ELSE summary_provenance END,
                                updated_at=?
                            WHERE image_id=? AND resident_id=?
                            """,
                            (
                                vision,
                                vision,
                                ocr,
                                datetime.now(UTC).isoformat(),
                                image_id,
                                self.resident_id,
                            ),
                        )
            self._refresh_card_fts(image_id)

    def update_card(
        self,
        image_id: str,
        changes: dict[str, Any],
        *,
        actor: str,
    ) -> dict[str, Any]:
        current = self.card(image_id)
        actual_id = str(current["image_id"])
        scalar_fields = {
            "alias",
            "summary",
            "alt_text",
            "visible_text",
            "resident_note",
            "inherited_framing",
            "present_resonance",
            "adoption_state",
            "summary_provenance",
        }
        list_fields = {
            "people": "people_json",
            "places": "places_json",
            "motifs": "motifs_json",
            "moods": "moods_json",
            "uses": "uses_json",
            "avoid_when": "avoid_when_json",
        }
        assignments: list[str] = []
        values: list[Any] = []
        changed: dict[str, Any] = {}
        for field in scalar_fields:
            if field not in changes:
                continue
            value = " ".join(str(changes[field] or "").split()).strip()
            if field == "adoption_state" and value not in {
                "unreviewed",
                "inherited",
                "resonant",
                "adopted",
                "negative_canon",
                "windowsill",
            }:
                raise ValueError("unsupported image-card adoption_state")
            assignments.append(f"{field}=?")
            values.append(value)
            changed[field] = value
        for public, column in list_fields.items():
            if public not in changes:
                continue
            raw = changes[public]
            if isinstance(raw, str):
                items = [item.strip() for item in raw.split(",") if item.strip()]
            elif isinstance(raw, list):
                items = [str(item).strip() for item in raw if str(item).strip()]
            else:
                raise ValueError(f"image card {public} must be a list or comma-separated text")
            items = list(dict.fromkeys(items))[:50]
            assignments.append(f"{column}=?")
            values.append(json.dumps(items, ensure_ascii=False))
            changed[public] = items
        privacy = None
        if "privacy" in changes:
            privacy = str(changes["privacy"]).strip().lower()
            if privacy not in {"private", "shareable"}:
                raise ValueError("image privacy must be private or shareable")
            changed["privacy"] = privacy
        if not assignments and privacy is None:
            return current
        now = datetime.now(UTC).isoformat()
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if assignments:
                connection.execute(
                    f"UPDATE image_cards SET {', '.join(assignments)}, updated_at=? "
                    "WHERE image_id=? AND resident_id=?",
                    (*values, now, actual_id, self.resident_id),
                )
            if privacy is not None:
                connection.execute(
                    "UPDATE image_assets SET privacy=? WHERE id=? AND resident_id=?",
                    (privacy, actual_id, self.resident_id),
                )
            connection.execute(
                """
                INSERT INTO image_events
                (id, image_id, event_type, status, actor, reason, payload_json, created_at)
                VALUES (?, ?, 'card_updated', ?, ?, 'resident updated picture-drawer card', ?, ?)
                """,
                (
                    new_id("iev"),
                    actual_id,
                    privacy or "card_updated",
                    actor,
                    json.dumps(changed, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
        self._refresh_card_fts(actual_id)
        return self.card(actual_id)

    def summarize_card(
        self,
        image_id: str,
        *,
        actor: str,
        inspect_if_missing: bool = False,
    ) -> dict[str, Any]:
        card = self.card(image_id)
        if card["summary"]:
            return {**card, "summary_cache": "card"}
        actual_id = str(card["image_id"])
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT route, result_text, created_at
                FROM image_interpretations
                WHERE resident_id=? AND image_id=?
                ORDER BY rowid DESC
                """,
                (self.resident_id, actual_id),
            ).fetchall()
        if not rows and inspect_if_missing:
            self.inspect(
                actual_id,
                question=(
                    "Describe the scene, visible people, objects, setting, mood, "
                    "composition, and legible text. Do not infer identity or relationship "
                    "unless supplied separately."
                ),
                routes=("ocr", "vision_low"),
            )
            return self.summarize_card(actual_id, actor=actor, inspect_if_missing=False)
        vision = next(
            (str(row["result_text"]).strip() for row in rows if str(row["route"]).startswith("vision")),
            "",
        )
        ocr = next(
            (str(row["result_text"]).strip() for row in rows if str(row["route"]) == "ocr"),
            "",
        )
        if not vision and not ocr:
            return {**card, "summary_cache": "missing", "next_action": "inspect_or_annotate"}
        return self.update_card(
            actual_id,
            {
                "summary": vision or "Image with cached text but no visual summary.",
                "alt_text": vision,
                "visible_text": ocr,
                "summary_provenance": "cached_image_interpretation",
            },
            actor=actor,
        )

    def search_cards(
        self,
        query: str,
        *,
        limit: int = 8,
        include_private: bool = True,
        pocket: str = "",
    ) -> list[dict[str, Any]]:
        clean = " ".join(str(query).split()).strip()
        maximum = min(30, max(1, int(limit)))
        words = list(dict.fromkeys(re.findall(r"[\w#-]{2,}", clean.casefold())))[:16]
        fts = " OR ".join(f'"{word}"' for word in words)
        parameters: list[Any] = [self.resident_id]
        privacy_sql = "" if include_private else " AND a.privacy!='private'"
        pocket_sql = ""
        if pocket:
            pocket_sql = (
                " AND EXISTS (SELECT 1 FROM image_pockets p "
                "WHERE p.resident_id=a.resident_id AND p.image_id=a.id AND p.pocket=?)"
            )
            parameters.append(self._normalize_pocket(pocket))
        parameters.append(maximum)
        with self.db.connect() as connection:
            if fts:
                rows = connection.execute(
                    f"""
                    SELECT c.image_id
                    FROM image_cards_fts f
                    JOIN image_cards c ON c.image_id=f.image_id
                    JOIN image_assets a ON a.id=c.image_id
                    WHERE c.resident_id=? AND image_cards_fts MATCH ?
                    {privacy_sql} {pocket_sql}
                    ORDER BY bm25(image_cards_fts), c.updated_at DESC LIMIT ?
                    """,
                    (self.resident_id, fts, *parameters[1:]),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"""
                    SELECT c.image_id FROM image_cards c
                    JOIN image_assets a ON a.id=c.image_id
                    WHERE c.resident_id=? {privacy_sql} {pocket_sql}
                    ORDER BY c.updated_at DESC LIMIT ?
                    """,
                    tuple(parameters),
                ).fetchall()
        return [self.card(str(row["image_id"])) for row in rows]

    def set_pocket(self, image_id: str, pocket: str, *, present: bool = True) -> dict[str, Any]:
        card = self.card(image_id)
        actual_id = str(card["image_id"])
        clean = self._normalize_pocket(pocket)
        with self.db.connect() as connection:
            if present:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO image_pockets
                    (resident_id, pocket, image_id, added_at) VALUES (?, ?, ?, ?)
                    """,
                    (self.resident_id, clean, actual_id, datetime.now(UTC).isoformat()),
                )
            else:
                connection.execute(
                    "DELETE FROM image_pockets WHERE resident_id=? AND pocket=? AND image_id=?",
                    (self.resident_id, clean, actual_id),
                )
        return self.card(actual_id)

    def pockets(self) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT pocket, COUNT(*) AS image_count, MAX(added_at) AS updated_at
                FROM image_pockets WHERE resident_id=?
                GROUP BY pocket ORDER BY pocket
                """,
                (self.resident_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def timeline(self, image_id: str, *, limit: int = 50) -> dict[str, Any]:
        card = self.card(image_id)
        actual_id = str(card["image_id"])
        with self.db.connect() as connection:
            events = connection.execute(
                """
                SELECT id, event_type, status, actor, reason, payload_json, created_at
                FROM image_events WHERE image_id=?
                ORDER BY rowid DESC LIMIT ?
                """,
                (actual_id, min(200, max(1, int(limit)))),
            ).fetchall()
        return {
            "image": card,
            "events": [
                {**dict(row), "payload": json.loads(str(row["payload_json"]) or "{}")}
                for row in events
            ],
        }

    def inspect(
        self,
        image_id: str,
        *,
        question: str = "Describe the image and anything important in it.",
        routes: Sequence[str] = ("ocr", "vision_low"),
        language: str = "eng",
    ) -> dict[str, Any]:
        asset = self.get_asset(image_id)
        if not asset:
            raise KeyError(f"unknown image: {image_id}")
        path = self.resolve_path(image_id)
        clean_question = " ".join(question.split()).strip()
        if not clean_question:
            raise ValueError("image.inspect requires a question")
        allowed = {"ocr", "vision_low", "vision_high"}
        requested = []
        for item in routes:
            normalized = str(item).strip().lower()
            if normalized not in allowed:
                raise ValueError(f"unknown image inspection route: {normalized}")
            if normalized not in requested:
                requested.append(normalized)
        if not requested:
            requested = ["ocr", "vision_low"]
        outputs: list[dict[str, Any]] = []
        for route in requested:
            if route == "ocr":
                outputs.append(
                    self._inspect_ocr(
                        str(asset["id"]),
                        path,
                        question=clean_question,
                        language=language,
                    )
                )
            else:
                outputs.append(
                    self._inspect_vision(
                        str(asset["id"]),
                        path,
                        question=clean_question,
                        detail="high" if route == "vision_high" else "low",
                    )
                )
        return {
            "image": {
                key: asset.get(key)
                for key in (
                    "id",
                    "artifact_id",
                    "content_hash",
                    "original_filename",
                    "media_type",
                    "width",
                    "height",
                    "source_kind",
                )
            },
            "question": clean_question,
            "results": outputs,
        }

    def _mark_challenge_expired(self, challenge_id: str) -> None:
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE image_share_challenges SET status='expired' WHERE id=?",
                (challenge_id,),
            )

    def authorize_share(
        self,
        payload: dict[str, Any],
        *,
        turn_id: str | None,
        interface: str | None,
        participant_id: str | None,
        delivery_target: dict[str, Any] | None,
        consume: bool = False,
    ) -> None:
        """Centralized policy engine authorizer for quick-draw image sharing."""
        schema_version = str(payload.get("schema_version") or "v2").strip().lower()
        if schema_version not in {"v1", "v2"}:
            raise ValueError("unsupported image.share schema_version; use v1 or v2")
        draft_id = str(payload.get("draft_id", "")).strip()
        legacy_decision = str(payload.get("decision", "")).strip().lower()
        mode = str(payload.get("mode") or legacy_decision or "").strip().lower()
        if not mode:
            mode = "preview" if draft_id else "prepare"
        if mode == "quick":
            mode = "send"
            
        if mode == "send":
            image_id = str(
                payload.get("image_id") or payload.get("artifact_id") or ""
            ).strip()
            asset = self.get_asset(image_id)
            if not asset:
                raise KeyError(f"unknown image: {image_id}")
            privacy = str(asset.get("privacy") or "private").strip().lower()
            if privacy == "private" and payload.get("confirm") is True:
                ch_id = str(payload.get("challenge_id") or "").strip()
                if not ch_id:
                    raise ValueError(
                        "Confirming a private image share requires a valid challenge_id. "
                        "Call image.share first without confirm:true to obtain a challenge."
                    )
                p_id = participant_id or "local-user"
                dest_kind = "unknown"
                dest_id = "unknown"
                if delivery_target:
                    dest_kind = delivery_target.get("kind") or "unknown"
                    dest_id = delivery_target.get("id") or "unknown"
                now_str = datetime.now(UTC).isoformat()
                
                with self.db.connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    row = connection.execute(
                        """
                        SELECT * FROM image_share_challenges
                        WHERE id = ? AND image_id = ? AND resident_id = ?
                        """,
                        (ch_id, str(asset["id"]), self.resident_id),
                    ).fetchone()
                    if not row:
                        raise PermissionError(
                            f"No pending confirmation challenge found matching challenge_id={ch_id} and image_id={asset['id']}"
                        )
                    if row["status"] != "pending":
                        raise PermissionError(
                            f"Confirmation challenge {ch_id} has already been {row['status']}."
                        )
                    if row["expires_at"] < now_str:
                        connection.execute(
                            "UPDATE image_share_challenges SET status='expired' WHERE id=?",
                            (ch_id,),
                        )
                        connection.commit()
                        raise PermissionError(
                            f"Confirmation challenge {ch_id} has expired."
                        )
                    if row["requested_turn_id"] == turn_id:
                        raise PermissionError(
                            "A private image cannot be confirmed within the same turn/invocation it was requested. "
                            "The confirmation must occur in a subsequent participant-originated later turn."
                        )
                    if row["participant_id"] != p_id:
                        raise PermissionError(
                            f"Confirmation challenger participant ID mismatch: expected {row['participant_id']}, got {p_id}."
                        )
                    if row["destination_id"] != dest_id or row["destination_kind"] != dest_kind:
                        raise PermissionError(
                            f"Confirmation destination mismatch: expected {row['destination_kind']}:{row['destination_id']}, "
                            f"got {dest_kind}:{dest_id}."
                        )
                    if interface != "discord":
                        raise PermissionError(
                            "A private image confirmation must be initiated by a participant-originated later turn on Discord."
                        )
                    if row["content_hash"] != str(asset.get("content_hash") or ""):
                        raise PermissionError(
                            "Image content changed after confirmation challenge creation."
                        )
                    if consume:
                        connection.execute(
                            """
                            UPDATE image_share_challenges
                            SET status='consumed', consumed_turn_id=?, consumed_at=?
                            WHERE id=?
                            """,
                            (turn_id, now_str, ch_id),
                        )

    def share(
        self,
        payload: dict[str, Any],
        *,
        turn_id: str | None,
        actor: str,
        interface: str | None = None,
        invocation: str | None = None,
        participant_id: str | None = None,
        delivery_target: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Draft or claim an outward image attachment.

        Claiming in the same resident turn as drafting is refused. The returned
        `_outbound_path` is runtime-private and is removed before a provider sees
        the receipt.
        """

        schema_version = str(payload.get("schema_version") or "v2").strip().lower()
        if schema_version not in {"v1", "v2"}:
            raise ValueError("unsupported image.share schema_version; use v1 or v2")
        draft_id = str(payload.get("draft_id", "")).strip()
        legacy_decision = str(payload.get("decision", "")).strip().lower()
        mode = str(payload.get("mode") or legacy_decision or "").strip().lower()
        if not mode:
            mode = "preview" if draft_id else "prepare"
        if mode == "quick":
            mode = "send"
        if mode not in {"send", "preview", "prepare", "claim", "reject"}:
            raise ValueError(
                "image.share mode must be send, preview, prepare, claim, or reject"
            )

        if mode == "send":
            if invocation == "private_curation" or str(turn_id or "").startswith(
                "curation_batch_"
            ):
                raise PermissionError(
                    "private curation cannot deliver an outward image attachment"
                )
            if interface not in {"discord", "bell"}:
                raise PermissionError(
                    "quick image delivery requires the authenticated Discord doorway; "
                    "No outward action occurred."
                )
            image_id = str(
                payload.get("image_id") or payload.get("artifact_id") or ""
            ).strip()
            asset = self.get_asset(image_id)
            if not asset:
                raise KeyError(f"unknown image: {image_id}")
            privacy = str(asset.get("privacy") or "private").strip().lower()
            if privacy == "private":
                if payload.get("confirm") is not True:
                    challenge_id = new_id("ch")
                    now = datetime.now(UTC)
                    expires = now + timedelta(minutes=5)
                    p_id = participant_id or "local-user"
                    dest_kind = "unknown"
                    dest_id = "unknown"
                    if delivery_target:
                        dest_kind = delivery_target.get("kind") or "unknown"
                        dest_id = delivery_target.get("id") or "unknown"
                    with self.db.connect() as connection:
                        connection.execute(
                            """
                            INSERT INTO image_share_challenges (
                                id, resident_id, image_id, content_hash, participant_id,
                                destination_kind, destination_id, requested_turn_id, status, expires_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                            """,
                            (
                                challenge_id,
                                self.resident_id,
                                str(asset["id"]),
                                asset.get("content_hash") or "",
                                p_id,
                                dest_kind,
                                dest_id,
                                turn_id or "unknown",
                                expires.isoformat(),
                            ),
                        )
                    return {
                        "schema_version": "vestigia.image-share.v2",
                        "mode": "send",
                        "image_id": str(asset["id"]),
                        "status": "resident_confirmation_required",
                        "privacy": "private",
                        "recipient": "current_authenticated_doorway",
                        "content_hash": asset.get("content_hash"),
                        "challenge_id": challenge_id,
                        "next_action": f"repeat image.share mode:send with confirm:true and challenge_id:{challenge_id}",
                        "outward_action": False,
                        "invariant": "No outward action occurred.",
                        "friendly_summary": (
                            f"This picture is private. Participant confirmation is required (challenge_id: {challenge_id}) "
                            "before a one-time handoff."
                        ),
                    }
                else:
                    self.authorize_share(
                        payload,
                        turn_id=turn_id,
                        interface=interface,
                        participant_id=participant_id,
                        delivery_target=delivery_target,
                        consume=True,
                    )
            path = self.resolve_path(str(asset["id"]))
            now = datetime.now(UTC).isoformat()
            reason = str(payload.get("reason", "")).strip() or "resident quick-draw"
            with self.db.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO image_events
                    (id, image_id, event_type, status, actor, reason, payload_json, created_at)
                    VALUES (?, ?, 'delivery_authorized', 'pending_platform_delivery',
                            ?, ?, ?, ?)
                    """,
                    (
                        new_id("iev"),
                        str(asset["id"]),
                        actor,
                        reason,
                        json.dumps(
                            {
                                "delivery": "current_authenticated_doorway",
                                "privacy": privacy,
                                "private_share_once": privacy == "private",
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        now,
                    ),
                )
            return {
                "schema_version": "vestigia.image-share.v2",
                "mode": "send",
                "image_id": str(asset["id"]),
                "status": "handoff_prepared",
                "privacy": privacy,
                "private_share_once": privacy == "private",
                "outward_action": True,
                "delivery": "current_authenticated_doorway",
                "delivery_status": "pending_platform_delivery",
                "next_action": "platform_delivery_receipt",
                "friendly_summary": (
                    "Picture attack authorized. The doorway is attempting delivery; "
                    "platform visibility is not yet confirmed."
                ),
                "_outbound_path": str(path),
            }

        if mode == "preview":
            if draft_id:
                row = self._pending_share_row(draft_id)
                expected = str(payload.get("expected_hash", "")).strip()
                if expected and expected != str(row["payload_hash"]):
                    raise PermissionError(
                        "image share draft hash mismatch; refresh pending and preview again"
                    )
                return self._share_preview(row)
            image_id = str(
                payload.get("image_id") or payload.get("artifact_id") or ""
            ).strip()
            asset = self.get_asset(image_id)
            if not asset:
                raise KeyError(f"unknown image: {image_id}")
            return {
                "schema_version": "vestigia.image-share.v1",
                "mode": "preview",
                "image_id": str(asset["id"]),
                "status": "available",
                "recipient": "current_authenticated_doorway",
                "privacy": asset.get("privacy") or "private",
                "content_hash": asset.get("content_hash"),
                "next_action": "prepare",
                "state_change": False,
                "outward_action": False,
                "invariant": "No outward action occurred.",
            }

        if mode == "prepare":
            if draft_id:
                raise ValueError("image.share prepare accepts image_id, not draft_id")
            image_id = str(
                payload.get("image_id") or payload.get("artifact_id") or ""
            ).strip()
            asset = self.get_asset(image_id)
            if not asset:
                raise KeyError(f"unknown image: {image_id}")
            reason = str(payload.get("reason", "")).strip() or "resident proposes sharing"
            canonical = {
                "image_id": str(asset["id"]),
                "reason": reason,
                "boundary": "outward_attachment",
            }
            payload_hash = sha256_text(
                json.dumps(canonical, ensure_ascii=False, sort_keys=True)
            )
            with self.db.connect() as connection:
                equivalent = connection.execute(
                    """
                    SELECT * FROM image_share_drafts
                    WHERE resident_id=? AND image_id=? AND payload_hash=?
                      AND status='pending'
                    ORDER BY rowid LIMIT 1
                    """,
                    (self.resident_id, str(asset["id"]), payload_hash),
                ).fetchone()
            if equivalent:
                result = self._share_preview(equivalent)
                result["mode"] = "prepare"
                result["idempotent_reuse"] = True
                result["friendly_summary"] = "Existing equivalent draft found."
                return result
            draft_id = new_id("image_share_draft")
            with self.db.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO image_share_drafts
                    (id, resident_id, image_id, payload_hash, reason,
                     created_turn_id, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        draft_id,
                        self.resident_id,
                        str(asset["id"]),
                        payload_hash,
                        reason,
                        turn_id,
                        datetime.now(UTC).isoformat(),
                    ),
                )
            return {
                "schema_version": "vestigia.image-share.v1",
                "mode": "prepare",
                "draft_id": draft_id,
                "continuation_id": draft_id,
                "image_id": str(asset["id"]),
                "expected_hash": payload_hash,
                "current_hash": payload_hash,
                "status": "claimable",
                "recipient": "current_authenticated_doorway",
                "purpose": reason,
                "next_action": "preview_or_claim_on_later_participant_turn",
                "required_next_action": "image.share",
                "outward_action": False,
                "invariant": "No outward action occurred.",
                "friendly_summary": "Draft found. Share not yet performed.",
                "instruction": (
                    "Review this draft, then atomically claim and prepare delivery with "
                    "its exact hash, confirm:true, and mode:claim in a later participant turn."
                ),
            }
        if not draft_id:
            raise ValueError(f"image.share {mode} requires draft_id")
        decision = mode
        if decision == "claim" and payload.get("confirm") is not True:
            raise PermissionError(
                "image.share claim requires confirm:true; No outward action occurred."
            )
        if decision == "claim" and (
            invocation == "private_curation"
            or str(turn_id or "").startswith("curation_batch_")
        ):
            raise PermissionError(
                "private curation cannot claim an outward image attachment"
            )
        if decision == "claim" and interface not in {None, "discord", "bell"}:
            raise PermissionError(
                "image sharing is unavailable through this interface"
            )
        expected = str(payload.get("expected_hash", "")).strip()
        row = self._pending_share_row(draft_id)
        if expected != str(row["payload_hash"]):
            raise PermissionError("image share draft hash mismatch")
        if turn_id and row["created_turn_id"] and turn_id == str(row["created_turn_id"]):
            raise PermissionError("image sharing requires a later resident turn")
        now = datetime.now(UTC).isoformat()
        if decision == "reject":
            with self.db.connect() as connection:
                connection.execute(
                    """
                    UPDATE image_share_drafts
                    SET status='rejected', resolved_at=?
                    WHERE id=? AND status='pending'
                    """,
                    (now, draft_id),
                )
            return {
                "schema_version": "vestigia.image-share.v1",
                "mode": "reject",
                "draft_id": draft_id,
                "status": "rejected",
                "next_action": "none",
                "outward_action": False,
                "invariant": "No outward action occurred.",
                "friendly_summary": "Draft rejected. Nothing was shared.",
            }
        image_id = str(row["image_id"])
        path = self.resolve_path(image_id)
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE image_share_drafts
                SET status='claimed', resolved_at=?
                WHERE id=? AND status='pending'
                """,
                (now, draft_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("image share draft changed before claim")
            connection.execute(
                """
                INSERT INTO image_events
                (id, image_id, event_type, status, actor, reason, payload_json, created_at)
                VALUES (?, ?, 'share_claimed', 'shareable', ?, ?,
                        '{"delivery":"current_authenticated_doorway"}', ?)
                """,
                (
                    new_id("iev"),
                    image_id,
                    actor,
                    str(row["reason"]),
                    now,
                ),
            )
        return {
            "schema_version": "vestigia.image-share.v1",
            "mode": "claim",
            "draft_id": draft_id,
            "image_id": image_id,
            "status": "claimed",
            "outward_action": True,
            "delivery": "current_authenticated_doorway",
            "delivery_status": "pending_platform_delivery",
            "next_action": "platform_delivery_receipt",
            "friendly_summary": (
                "Hash verified. Claim succeeded. Attachment prepared; platform "
                "delivery is not yet confirmed."
            ),
            "_outbound_path": str(path),
        }

    def pending_shares(self) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, image_id, payload_hash, reason, created_turn_id, created_at
                FROM image_share_drafts
                WHERE resident_id=? AND status='pending'
                ORDER BY rowid
                """,
                (self.resident_id,),
            ).fetchall()
        canonical: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            key = (str(item["image_id"]), str(item["payload_hash"]))
            if key not in canonical:
                canonical[key] = self._share_preview(item)
        return list(canonical.values())

    def _pending_share_row(self, draft_id: str) -> Any:
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM image_share_drafts
                WHERE id=? AND resident_id=?
                """,
                (draft_id, self.resident_id),
            ).fetchone()
        if not row or str(row["status"]) != "pending":
            raise KeyError(
                "unknown or resolved image share draft; No outward action occurred."
            )
        return row

    @staticmethod
    def _share_preview(row: Any) -> dict[str, Any]:
        item = dict(row)
        return {
            "schema_version": "vestigia.image-share.v1",
            "mode": "preview",
            "draft_id": str(item["id"]),
            "continuation_id": str(item["id"]),
            "image_id": str(item["image_id"]),
            "status": "claimable",
            "current_hash": str(item["payload_hash"]),
            "expected_hash": str(item["payload_hash"]),
            "recipient": "current_authenticated_doorway",
            "purpose": str(item["reason"]),
            "created_at": str(item["created_at"]),
            "updated_at": str(item.get("resolved_at") or item["created_at"]),
            "next_action": "preview_or_claim_on_later_participant_turn",
            "required_next_action": "image.share",
            "state_change": False,
            "outward_action": False,
            "invariant": "No outward action occurred.",
            "friendly_summary": "Draft found. Share not yet performed.",
        }

    def record_delivery(
        self,
        path: str | Path,
        *,
        status: str,
        actor: str,
        external_id: str | None = None,
        error_type: str | None = None,
    ) -> str:
        """Record the doorway result separately from a resident share claim."""

        candidate = Path(path).resolve()
        try:
            relative = candidate.relative_to(self.home).as_posix()
        except ValueError as exc:
            raise PermissionError("delivery path leaves the resident house") from exc
        normalized = str(status).strip().lower()
        if normalized not in {"delivered", "failed"}:
            raise ValueError("delivery status must be delivered or failed")
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT id FROM image_assets
                WHERE resident_id=? AND path=?
                """,
                (self.resident_id, relative),
            ).fetchone()
            if not row:
                raise KeyError("delivered file is not a registered image asset")
            event_id = new_id("iev")
            connection.execute(
                """
                INSERT INTO image_events
                (id, image_id, event_type, status, actor, reason, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    str(row["id"]),
                    "delivery_succeeded" if normalized == "delivered" else "delivery_failed",
                    normalized,
                    actor,
                    "Discord attachment delivery result",
                    json.dumps(
                        {
                            "external_id": external_id,
                            "error_type": error_type,
                            "doorway_status": (
                                "platform_accepted"
                                if normalized == "delivered"
                                else "platform_rejected_or_failed"
                            ),
                            "participant_visibility": "unknown",
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    datetime.now(UTC).isoformat(),
                ),
            )
        return event_id

    def queue_job(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        turn_id: str | None,
        delivery: dict[str, Any] | None,
    ) -> dict[str, Any]:
        normalized = operation.strip().lower()
        if normalized not in {"generate", "edit"}:
            raise ValueError("image job operation must be generate or edit")
        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            raise ValueError(f"image.{normalized} requires a prompt")
        count = int(payload.get("count", 1))
        maximum = int(self.config.get("images.max_per_request", 2))
        if count < 1 or count > maximum:
            raise ValueError(f"count must be between 1 and {maximum}")
        clean: dict[str, Any] = {
            "prompt": prompt,
            "count": count,
            # Resident JSON cannot impersonate an operator confirmation. Homes that
            # require confirmation must use the authenticated human command path.
            "confirmed": False,
        }
        if normalized == "edit":
            image_ids = payload.get("image_ids")
            if not isinstance(image_ids, list) or not image_ids:
                raise ValueError("image.edit requires a non-empty image_ids list")
            # Resolve now so a queued job cannot smuggle a path or another resident's ID.
            clean["image_ids"] = [str(item) for item in image_ids]
            for image_id in clean["image_ids"]:
                self.resolve_path(image_id)
        job_id = new_id("image_job")
        now = datetime.now(UTC).isoformat()
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO image_jobs
                (id, resident_id, room_id, operation, payload_json, delivery_json,
                 status, created_turn_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    job_id,
                    self.resident_id,
                    self.room_id,
                    normalized,
                    json.dumps(clean, ensure_ascii=False, sort_keys=True),
                    json.dumps(delivery or {}, ensure_ascii=False, sort_keys=True),
                    turn_id,
                    now,
                    now,
                ),
            )
        return {
            "job_id": job_id,
            "operation": normalized,
            "status": "queued",
            "privacy": "private",
            "completion": "resident_continuation",
            "delivery": delivery or {},
        }

    def claim_next_job(self) -> dict[str, Any] | None:
        now = datetime.now(UTC).isoformat()
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM image_jobs
                WHERE resident_id=? AND status='queued'
                ORDER BY rowid LIMIT 1
                """,
                (self.resident_id,),
            ).fetchone()
            if not row:
                return None
            cursor = connection.execute(
                """
                UPDATE image_jobs SET status='running', updated_at=?
                WHERE id=? AND status='queued'
                """,
                (now, str(row["id"])),
            )
            if cursor.rowcount != 1:
                return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        item["delivery"] = json.loads(item.pop("delivery_json") or "{}")
        item["status"] = "running"
        return item

    def execute_job(self, job_id: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM image_jobs
                WHERE id=? AND resident_id=?
                """,
                (job_id, self.resident_id),
            ).fetchone()
        if not row or str(row["status"]) != "running":
            raise KeyError("unknown or non-running image job")
        payload = json.loads(str(row["payload_json"]) or "{}")
        operation = str(row["operation"])
        try:
            if operation == "generate":
                result = self.generate(
                    str(payload["prompt"]),
                    count=int(payload.get("count", 1)),
                    confirmed=bool(payload.get("confirmed", False)),
                    turn_id=str(row["created_turn_id"] or "") or None,
                )
            else:
                result = self.edit_assets(
                    str(payload["prompt"]),
                    [str(item) for item in payload.get("image_ids", [])],
                    count=int(payload.get("count", 1)),
                    confirmed=bool(payload.get("confirmed", False)),
                    turn_id=str(row["created_turn_id"] or "") or None,
                )
            result_payload = {
                "operation": operation,
                "artifact_ids": list(result.artifact_ids),
                "image_ids": list(result.image_ids),
                "model": result.model,
                "privacy": "private",
                "publication": False,
            }
            with self.db.connect() as connection:
                connection.execute(
                    """
                    UPDATE image_jobs
                    SET status='completed', result_json=?, updated_at=?
                    WHERE id=? AND status='running'
                    """,
                    (
                        json.dumps(result_payload, ensure_ascii=False, sort_keys=True),
                        datetime.now(UTC).isoformat(),
                        job_id,
                    ),
                )
            return {"job_id": job_id, "status": "completed", **result_payload}
        except Exception as exc:
            with self.db.connect() as connection:
                connection.execute(
                    """
                    UPDATE image_jobs
                    SET status='failed', error_type=?, error_hash=?, updated_at=?
                    WHERE id=? AND status='running'
                    """,
                    (
                        type(exc).__name__,
                        sha256_text(str(exc)),
                        datetime.now(UTC).isoformat(),
                        job_id,
                    ),
                )
            return {
                "job_id": job_id,
                "status": "failed",
                "error_type": type(exc).__name__,
                "error_hash": sha256_text(str(exc)),
            }

    def unnotified_jobs(self, *, limit: int = 10) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM image_jobs
                WHERE resident_id=? AND status IN ('completed','failed')
                  AND notified_at IS NULL
                ORDER BY rowid LIMIT ?
                """,
                (self.resident_id, min(50, max(1, int(limit)))),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            item["delivery"] = json.loads(item.pop("delivery_json") or "{}")
            item["result"] = json.loads(item.pop("result_json") or "{}")
            result.append(item)
        return result

    def mark_job_notified(self, job_id: str) -> None:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE image_jobs SET notified_at=?, updated_at=?
                WHERE id=? AND resident_id=? AND notified_at IS NULL
                """,
                (
                    datetime.now(UTC).isoformat(),
                    datetime.now(UTC).isoformat(),
                    job_id,
                    self.resident_id,
                ),
            )
        if cursor.rowcount != 1:
            raise KeyError("unknown or already notified image job")

    def jobs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, operation, status, result_json, error_type, error_hash,
                       created_turn_id, created_at, updated_at, notified_at
                FROM image_jobs
                WHERE resident_id=?
                ORDER BY rowid DESC LIMIT ?
                """,
                (self.resident_id, min(100, max(1, int(limit)))),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["result"] = json.loads(item.pop("result_json") or "{}")
            result.append(item)
        return result

    def diagnostics(self) -> dict[str, Any]:
        binary = str(self.config.get("images.ocr_binary", "tesseract")).strip()
        with self.db.connect() as connection:
            counts = connection.execute(
                """
                SELECT COUNT(*) AS cards,
                       SUM(CASE WHEN summary!='' THEN 1 ELSE 0 END) AS summarized
                FROM image_cards WHERE resident_id=?
                """,
                (self.resident_id,),
            ).fetchone()
        return {
            "ocr_enabled": bool(self.config.get("images.ocr_enabled", True)),
            "ocr_binary": binary,
            "ocr_available": shutil.which(binary) is not None,
            "vision_enabled": bool(self.config.get("images.vision_enabled", True)),
            "vision_model": str(self.config.get("models.vision", "gpt-5-mini")),
            "vision_default_detail": str(
                self.config.get("images.vision_default_detail", "low")
            ),
            "shelf": "artifacts/images/shelf",
            "picture_drawer_cards": int(counts["cards"] or 0),
            "picture_drawer_summarized": int(counts["summarized"] or 0),
            "pockets": self.pockets(),
        }

    def _inspect_ocr(
        self,
        image_id: str,
        path: Path,
        *,
        question: str,
        language: str,
    ) -> dict[str, Any]:
        if not bool(self.config.get("images.ocr_enabled", True)):
            return {"route": "ocr", "status": "disabled", "cached": False, "text": ""}
        binary = str(self.config.get("images.ocr_binary", "tesseract")).strip()
        version = self._ocr_version(binary)
        recipe = str(self.config.get("images.ocr_recipe", "direct-psm6"))
        category = self._question_category(question)
        cache_key = sha256_text(
            "\0".join(
                [
                    image_id,
                    "ocr",
                    version,
                    language,
                    recipe,
                ]
            )
        )
        cached = self._cached_interpretation(cache_key)
        if cached:
            return {
                "route": "ocr",
                "status": "ok",
                "cached": True,
                "model": str(cached["model"]),
                "text": str(cached["result_text"]),
            }
        if shutil.which(binary) is None:
            return {
                "route": "ocr",
                "status": "unavailable",
                "cached": False,
                "model": version,
                "text": "",
                "note": (
                    "Install Tesseract or configure VESTIGIA_OCR_BINARY; "
                    "paid vision remains independently available."
                ),
            }
        psm = min(13, max(0, int(self.config.get("images.ocr_page_segmentation", 6))))
        completed = subprocess.run(
            [binary, str(path), "stdout", "-l", language, "--psm", str(psm)],
            check=False,
            capture_output=True,
            text=True,
            timeout=max(1, int(self.config.get("images.ocr_timeout_seconds", 20))),
        )
        if completed.returncode != 0:
            return {
                "route": "ocr",
                "status": "failed",
                "cached": False,
                "model": version,
                "text": "",
                "error": (completed.stderr or "OCR failed").strip()[:400],
            }
        text = (completed.stdout or "").strip()
        self._store_interpretation(
            image_id=image_id,
            route="ocr",
            model=version,
            detail=language,
            category=category,
            question=question,
            cache_key=cache_key,
            result=text,
            metadata={"recipe": recipe, "psm": psm, "paid_call": False},
        )
        return {
            "route": "ocr",
            "status": "ok",
            "cached": False,
            "model": version,
            "text": text,
        }

    def _inspect_vision(
        self,
        image_id: str,
        path: Path,
        *,
        question: str,
        detail: str,
    ) -> dict[str, Any]:
        if not bool(self.config.get("images.vision_enabled", True)):
            return {
                "route": f"vision_{detail}",
                "status": "disabled",
                "cached": False,
                "text": "",
            }
        provider = self._vision_provider()
        category = self._question_category(question)
        cache_key = sha256_text(
            "\0".join(
                [
                    image_id,
                    "vision",
                    provider.name,
                    provider.model,
                    detail,
                    sha256_text(" ".join(question.casefold().split())),
                    "inspect-v1",
                ]
            )
        )
        cached = self._cached_interpretation(cache_key)
        if cached:
            return {
                "route": f"vision_{detail}",
                "status": "ok",
                "cached": True,
                "model": str(cached["model"]),
                "text": str(cached["result_text"]),
            }
        result = (provider.inspect(path, question=question, detail=detail) or "").strip()
        self._store_interpretation(
            image_id=image_id,
            route="vision",
            model=provider.model,
            detail=detail,
            category=category,
            question=question,
            cache_key=cache_key,
            result=result,
            metadata={
                "provider": provider.name,
                "paid_call": provider.name != "fake",
                "schema": "inspect-v1",
            },
        )
        return {
            "route": f"vision_{detail}",
            "status": "ok",
            "cached": False,
            "model": provider.model,
            "text": result,
        }

    def _cached_interpretation(self, cache_key: str) -> Any | None:
        with self.db.connect() as connection:
            return connection.execute(
                "SELECT * FROM image_interpretations WHERE cache_key=?",
                (cache_key,),
            ).fetchone()

    def _store_interpretation(
        self,
        *,
        image_id: str,
        route: str,
        model: str,
        detail: str,
        category: str,
        question: str,
        cache_key: str,
        result: str,
        metadata: dict[str, Any],
    ) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO image_interpretations
                (id, image_id, resident_id, route, model, detail,
                 question_category, question_hash, cache_key, result_text,
                 result_hash, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("imginterp"),
                    image_id,
                    self.resident_id,
                    route,
                    model,
                    detail,
                    category,
                    sha256_text(question),
                    cache_key,
                    result,
                    sha256_text(result),
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    datetime.now(UTC).isoformat(),
                ),
            )

    @staticmethod
    def _question_category(question: str) -> str:
        lowered = question.casefold()
        if any(word in lowered for word in ("write", "text", "read", "say", "ocr")):
            return "text"
        if any(word in lowered for word in ("compare", "difference", "change")):
            return "comparison"
        if any(word in lowered for word in ("mood", "feel", "tone", "symbol")):
            return "interpretive"
        return "general"

    @staticmethod
    def _ocr_version(binary: str) -> str:
        if shutil.which(binary) is None:
            return "tesseract-unavailable"
        try:
            completed = subprocess.run(
                [binary, "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
            output = completed.stdout or completed.stderr or ""
            lines = output.splitlines()
            first = lines[0].strip() if lines else ""
            return first or "tesseract"
        except Exception:
            return "tesseract"

    @staticmethod
    def _validate_image_bytes(data: bytes) -> tuple[int, int, str]:
        try:
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError(
                "Image support requires Pillow in this virtual environment. "
                'Run: .\\.venv\\Scripts\\python.exe -m pip install -e ".[discord]"'
            ) from exc
        try:
            with Image.open(BytesIO(data)) as image:
                width, height = image.size
                detected = str(image.format or "").upper()
                image.verify()
            return int(width), int(height), detected
        except Exception as exc:
            raise ValueError("attachment bytes are not a valid image") from exc

    @staticmethod
    def _asset_row(row: Any, *, reused: bool = False) -> dict[str, Any]:
        item = dict(row)
        item["source"] = json.loads(item.pop("source_json") or "{}")
        item["reused"] = reused
        return item

    @staticmethod
    def _card_row(
        row: Any,
        *,
        asset: dict[str, Any],
        pockets: list[str],
    ) -> dict[str, Any]:
        item = dict(row)
        result = {
            "image_id": str(item["image_id"]),
            "alias": str(item["alias"]),
            "summary": str(item["summary"]),
            "alt_text": str(item["alt_text"]),
            "visible_text": str(item["visible_text"]),
            "resident_note": str(item["resident_note"]),
            "inherited_framing": str(item["inherited_framing"]),
            "present_resonance": str(item["present_resonance"]),
            "adoption_state": str(item["adoption_state"]),
            "summary_provenance": str(item["summary_provenance"]),
            "created_at": str(item["created_at"]),
            "updated_at": str(item["updated_at"]),
            "pockets": pockets,
            "privacy": str(asset.get("privacy") or "private"),
            "content_hash": str(asset.get("content_hash") or ""),
            "original_filename": asset.get("original_filename"),
            "source_kind": asset.get("source_kind"),
            "width": asset.get("width"),
            "height": asset.get("height"),
        }
        for public, column in (
            ("people", "people_json"),
            ("places", "places_json"),
            ("motifs", "motifs_json"),
            ("moods", "moods_json"),
            ("uses", "uses_json"),
            ("avoid_when", "avoid_when_json"),
        ):
            result[public] = json.loads(str(item[column]) or "[]")
        result["quick_draw"] = {
            "available": result["privacy"] != "private",
            "action": {
                "action": "image.share",
                "mode": "send",
                "image_id": result["image_id"],
                **({"confirm": True} if result["privacy"] == "private" else {}),
                "after": "finish",
            },
        }
        return result

    def _refresh_card_fts(self, image_id: str) -> None:
        card = self.card(image_id)
        with self.db.connect() as connection:
            connection.execute("DELETE FROM image_cards_fts WHERE image_id=?", (image_id,))
            connection.execute(
                """
                INSERT INTO image_cards_fts
                (image_id, alias, summary, visible_text, people, places, motifs,
                 moods, uses, resident_note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    image_id,
                    card["alias"],
                    card["summary"],
                    card["visible_text"],
                    " ".join(card["people"]),
                    " ".join(card["places"]),
                    " ".join(card["motifs"]),
                    " ".join(card["moods"]),
                    " ".join(card["uses"]),
                    card["resident_note"],
                ),
            )

    @staticmethod
    def _normalize_pocket(value: str) -> str:
        clean = re.sub(r"[^a-z0-9_-]+", "-", str(value).strip().casefold()).strip("-")
        if not clean or len(clean) > 80:
            raise ValueError("pocket must contain a short readable name")
        return clean

    def _authorize(self, count: int, confirmed: bool, *, editing: bool) -> None:
        if not bool(self.config.get("images.enabled", True)):
            raise PermissionError("Image generation is disabled")
        if editing and not bool(self.config.get("images.edits_enabled", True)):
            raise PermissionError("Image editing is disabled")
        maximum = int(self.config.get("images.max_per_request", 2))
        if count < 1 or count > maximum:
            raise ValueError(f"count must be between 1 and {maximum}")
        if bool(self.config.get("images.require_confirmation", False)) and not confirmed:
            raise PermissionError("This home requires explicit confirmation for image calls")
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        used = len(self.db.list_artifacts(self.resident_id, since_iso=start))
        daily = int(self.config.get("images.daily_limit", 20))
        if used + count > daily:
            raise PermissionError(f"Daily image limit would be exceeded ({used}/{daily})")

    def _visual_prompt(self, request: str) -> tuple[str, list[str]]:
        budget = int(self.config.get("context.image_context_tokens", 3000))
        visual_path = self.home / "identity" / "visual_canon.md"
        canon = visual_path.read_text(encoding="utf-8") if visual_path.is_file() else ""
        records = self.db.list_memories(
            resident_id=self.resident_id,
            room_id=self.room_id,
            statuses=["accepted"],
            memory_types=["identity", "symbol", "place", "relationship"],
            tiers=["core", "hot", "warm"],
            limit=100,
        )
        blocks = [
            "# Current visual request\n" + request.strip(),
            "# Accepted visual canon\n" + canon.strip(),
        ]
        visual_ids: list[str] = []
        for record in records:
            blocks.append(f"[accepted visual record {record.id}]\n{record.content}")
            visual_ids.append(record.id)
        combined = "\n\n".join(blocks)
        return self.counter.trim(combined, budget), visual_ids

    def _preserve_sources(self, sources: list[Path]) -> list[Path]:
        preserved: list[Path] = []
        destination_dir = self.home / "artifacts" / "images" / "originals"
        destination_dir.mkdir(parents=True, exist_ok=True)
        for source in sources:
            if not source.is_file():
                raise FileNotFoundError(source)
            digest = sha256_file(source)
            target = destination_dir / f"{digest[:16]}-{source.name}"
            if not target.exists():
                shutil.copy2(source, target)
            preserved.append(target)
        return preserved

    def _save(
        self,
        blobs: list[bytes],
        *,
        operation: str,
        prompt: str,
        visual_ids: list[str],
        sources: list[Path],
        turn_id: str | None,
    ) -> ImageResult:
        provider = self._image_provider()
        directory = self.home / "artifacts" / "images" / (
            "generated" if operation == "generate" else "edits"
        )
        directory.mkdir(parents=True, exist_ok=True)
        artifact_ids: list[str] = []
        image_ids: list[str] = []
        paths: list[Path] = []
        for blob in blobs:
            path = directory / f"{new_id(operation)}.png"
            path.write_bytes(blob)
            artifact_id = self.db.add_artifact(
                resident_id=self.resident_id,
                room_id=self.room_id,
                turn_id=turn_id,
                operation=operation,
                provider=provider.name,
                model=provider.model,
                path=str(path.relative_to(self.home)),
                content_hash=sha256_file(path),
                prompt_hash=sha256_text(prompt),
                prompt_text=prompt if bool(self.config.get("images.save_prompts", True)) else None,
                source_images=[str(item.relative_to(self.home)) for item in sources],
                visual_records=visual_ids,
                privacy="private",
            )
            asset = self.ingest_file(
                path,
                source_kind=operation,
                source={
                    "turn_id": turn_id,
                    "source_images": [
                        str(item.relative_to(self.home)) for item in sources
                    ],
                    "visual_records": visual_ids,
                },
                privacy="private",
                artifact_id=artifact_id,
            )
            # The content-addressed shelf is canonical for resident tools. Keep the
            # legacy generated/edit path for upgrade compatibility and CLI output.
            if asset:
                image_ids.append(str(asset["id"]))
                with self.db.connect() as connection:
                    connection.execute(
                        """
                        INSERT INTO image_events
                        (id, image_id, event_type, status, actor, reason,
                         payload_json, created_at)
                        VALUES (?, ?, 'generated', 'private', 'runtime',
                                'resident image operation completed', ?, ?)
                        """,
                        (
                            new_id("iev"),
                            str(asset["id"]),
                            json.dumps(
                                {"operation": operation, "artifact_id": artifact_id},
                                sort_keys=True,
                            ),
                            datetime.now(UTC).isoformat(),
                        ),
                    )
            artifact_ids.append(artifact_id)
            paths.append(path)
        return ImageResult(
            artifact_ids=tuple(artifact_ids),
            paths=tuple(paths),
            model=provider.model,
            operation=operation,
            image_ids=tuple(image_ids),
        )
