from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


TRACKED_TABLES = (
    "schema_meta",
    "state_events",
    "memory_records",
    "memory_events",
    "turns",
    "artifacts",
    "artifact_events",
    "image_assets",
    "image_events",
    "image_interpretations",
    "image_share_drafts",
    "image_jobs",
    "image_cards",
    "image_pockets",
    "bells",
    "bell_events",
    "bell_drafts",
    "house_receipts",
    "house_documents",
    "house_bookmarks",
    "identity_revisions",
    "identity_drafts",
    "curation_batches",
    "curation_drafts",
)

PRESERVED_TABLE_KEYS: dict[str, tuple[str, ...]] = {
    "memory_records": ("id", "content_hash", "authority_state"),
    "turns": ("id", "content_hash", "speaker_role", "interface"),
    "artifacts": ("id", "content_hash", "operation"),
    "image_assets": ("id", "content_hash", "source_kind", "privacy"),
    "image_jobs": ("id", "operation", "status"),
    "bells": ("id", "schedule_kind", "status"),
    "house_receipts": ("id", "action", "status"),
}

IDENTITY_FILES = (
    "identity/identity_context.md",
    "identity/breathprint.md",
    "identity/current_self.md",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def safe_rows(
    connection: sqlite3.Connection,
    table: str,
    keys: Iterable[str],
) -> list[dict[str, Any]]:
    if not table_exists(connection, table):
        return []
    columns = {
        str(row["name"])
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    }
    selected = [key for key in keys if key in columns]
    if not selected:
        return []
    projection = ", ".join(f'"{item}"' for item in selected)
    rows = connection.execute(
        f'SELECT {projection} FROM "{table}" ORDER BY rowid'
    ).fetchall()
    return [{key: row[key] for key in selected} for row in rows]


def snapshot(home: Path) -> dict[str, Any]:
    from vestigia import __version__
    from vestigia.config import load_config
    from vestigia.db import ContinuityDB

    config = load_config(home)
    resident_id = str(config.get("resident.id"))
    room_id = str(config.get("room.id"))
    db = ContinuityDB(home / "memory" / "continuity.db")

    with db.connect() as connection:
        schema_row = (
            connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()
            if table_exists(connection, "schema_meta")
            else None
        )
        counts: dict[str, int | None] = {}
        for table in TRACKED_TABLES:
            if table_exists(connection, table):
                count = connection.execute(
                    f'SELECT COUNT(*) AS n FROM "{table}"'
                ).fetchone()
                counts[table] = int(count["n"] if count else 0)
            else:
                counts[table] = None
        records = {
            table: safe_rows(connection, table, keys)
            for table, keys in PRESERVED_TABLE_KEYS.items()
        }

    identity_hashes = {
        relative: sha256_file(home / relative)
        for relative in IDENTITY_FILES
        if (home / relative).is_file()
    }
    state = db.current_state(resident_id)
    return {
        "runtime_version": __version__,
        "schema_version": str(schema_row["value"]) if schema_row else None,
        "resident_id_hash": sha256_bytes(resident_id.encode("utf-8"))[:16],
        "room_id_hash": sha256_bytes(room_id.encode("utf-8"))[:16],
        "state": state,
        "counts": counts,
        "records": records,
        "identity_file_hashes": identity_hashes,
    }


def assert_preserved(
    before: dict[str, Any], after: dict[str, Any], *, label: str
) -> list[str]:
    checks: list[str] = []
    if before["resident_id_hash"] != after["resident_id_hash"]:
        raise AssertionError(f"{label}: resident identity hash changed")
    if before["room_id_hash"] != after["room_id_hash"]:
        raise AssertionError(f"{label}: room identity hash changed")
    if before["state"] != after["state"]:
        raise AssertionError(
            f"{label}: runtime state changed from {before['state']} to {after['state']}"
        )
    checks.extend(["resident_id", "room_id", "runtime_state"])

    for relative, digest in before.get("identity_file_hashes", {}).items():
        if after.get("identity_file_hashes", {}).get(relative) != digest:
            raise AssertionError(f"{label}: identity file hash changed: {relative}")
        checks.append(f"identity:{relative}")

    for table, keys in PRESERVED_TABLE_KEYS.items():
        before_rows = before.get("records", {}).get(table, [])
        if not before_rows:
            continue
        after_rows = {
            str(item.get("id")): item
            for item in after.get("records", {}).get(table, [])
        }
        for item in before_rows:
            item_id = str(item.get("id"))
            if item_id not in after_rows:
                raise AssertionError(f"{label}: missing {table} record {item_id}")
            actual = after_rows[item_id]
            for key in keys:
                if key in item and actual.get(key) != item.get(key):
                    raise AssertionError(
                        f"{label}: {table}.{key} changed for {item_id}: "
                        f"{item.get(key)!r} -> {actual.get(key)!r}"
                    )
        checks.append(f"records:{table}")
    return checks


def seed(home: Path, evidence: Path) -> int:
    from vestigia import __version__
    from vestigia.bells import BellService
    from vestigia.config import load_config
    from vestigia.db import ContinuityDB
    from vestigia.home import initialize_home
    from vestigia.images import ImageService
    from vestigia.models import NormalizedMessage
    from vestigia.runtime import CoreRuntime

    if home.exists():
        shutil.rmtree(home)
    initialize_home(
        home,
        name="Synthetic V061 Resident",
        glyph="🏮",
        resident_id="synthetic-v061-resident",
        room_id="hearth",
    )
    config = load_config(home)
    db = ContinuityDB(home / "memory" / "continuity.db")
    db.initialize()
    resident_id = str(config.get("resident.id"))
    room_id = str(config.get("room.id"))

    memory_id = db.add_memory(
        resident_id=resident_id,
        room_id=room_id,
        content="Synthetic v0.6.1 continuity witness.",
        memory_type="identity",
        tier="core",
        authorship="resident",
        authority_state="resident_stated",
        status="accepted",
        actor="resident:synthetic-v061-resident",
        reason="release upgrade canary",
        tags=["release-canary", "synthetic"],
        provenance={"source": "synthetic-v0.6.1-upgrade-canary"},
    )

    runtime = CoreRuntime.from_home(home, fake=True)
    runtime.transition_state(
        "ACTIVE",
        actor="release-canary",
        reason="synthetic v0.6.1 release specimen",
    )
    turn_result = runtime.chat(
        NormalizedMessage(
            content="Please produce one deterministic synthetic canary response.",
            speaker_id="synthetic-human",
            interface="release-canary",
            room_id=room_id,
            external_id="synthetic-v061:seed-turn",
        ),
        model_route="default",
    )

    bell = BellService(db, resident_id, room_id).create(
        title="Synthetic upgrade bell",
        purpose="maintenance",
        prompt="Remain a synthetic release witness; no outward action is requested.",
        schedule_kind="once",
        schedule={
            "at": (datetime.now(UTC) + timedelta(days=30)).isoformat()
        },
        timezone="UTC",
        created_by="release-canary",
        delivery_interface="discord",
        delivery_target={"kind": "channel", "id": "synthetic-channel"},
    )

    images = ImageService(config, db, fake=True)
    image_result = images.generate(
        "A one-pixel synthetic release canary lantern.",
        count=1,
        confirmed=True,
        turn_id=getattr(turn_result, "turn_id", None),
    )
    image_id = str(image_result.image_ids[0])
    job = images.queue_job(
        "generate",
        {"prompt": "A queued synthetic upgrade witness.", "count": 1},
        turn_id=getattr(turn_result, "turn_id", None),
        delivery={"kind": "none", "id": "synthetic"},
    )

    receipt_id = "receipt_synthetic_v061"
    with db.connect() as connection:
        if table_exists(connection, "house_receipts"):
            now = datetime.now(UTC).isoformat()
            result_json = json.dumps(
                {"kind": "synthetic", "outcome": "preserve-me"},
                sort_keys=True,
            )
            connection.execute(
                """
                INSERT INTO house_receipts
                (id, resident_id, room_id, turn_id, parent_receipt_id, action, status,
                 source_envelope, normalized_envelope, adapter_version, target_json,
                 result_json, result_hash, outward_effect, pinned, started_at, completed_at)
                VALUES (?, ?, ?, NULL, NULL, 'release.canary', 'succeeded',
                        'synthetic-v0.6.1', 'synthetic-v0.6.1', 'release-canary-v0.1',
                        '{}', ?, ?, 'none', 1, ?, ?)
                """,
                (
                    receipt_id,
                    resident_id,
                    room_id,
                    result_json,
                    sha256_bytes(result_json.encode("utf-8")),
                    now,
                    now,
                ),
            )

    workspace = home / "workspace" / "release-canary.txt"
    workspace.parent.mkdir(parents=True, exist_ok=True)
    workspace.write_text(
        "Synthetic v0.6.1 upgrade canary. No resident continuity is present.\n",
        encoding="utf-8",
    )

    baseline = snapshot(home)
    baseline.update(
        {
            "phase": "v0.6.1-seed",
            "source_commit": "b12f8f778c390f4ac8fc3abe1af1a44a73799c26",
            "seeded_ids": {
                "memory_id": memory_id,
                "bell_id": bell.id,
                "image_id": image_id,
                "image_job_id": str(job["job_id"]),
                "receipt_id": receipt_id,
            },
            "workspace_sha256": sha256_file(workspace),
        }
    )
    json_dump(evidence, baseline)
    print(
        json.dumps(
            {
                "phase": "seed",
                "runtime_version": __version__,
                "schema_version": baseline["schema_version"],
                "state": baseline["state"],
                "counts": baseline["counts"],
            },
            sort_keys=True,
        )
    )
    return 0


def pack_manifest(archive: Path) -> dict[str, Any]:
    with zipfile.ZipFile(archive, "r") as handle:
        bad = handle.testzip()
        if bad is not None:
            raise AssertionError(f"ZIP CRC verification failed for {bad}")
        manifest = json.loads(handle.read("PACK_MANIFEST.json"))
    return {
        "schema_version": manifest.get("schema_version"),
        "file_count": len(manifest.get("files", [])),
        "manifest_sha256": sha256_bytes(
            json.dumps(manifest, sort_keys=True).encode("utf-8")
        ),
    }


def upgrade(
    home: Path,
    baseline_path: Path,
    archive: Path,
    restored: Path,
    evidence: Path,
    expected_version: str,
) -> int:
    from vestigia import __version__
    from vestigia.models import NormalizedMessage
    from vestigia.packing import pack_home, restore_home
    from vestigia.runtime import CoreRuntime

    if __version__ != expected_version:
        raise AssertionError(
            f"current Runtime version {__version__!r} does not match {expected_version!r}"
        )
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    runtime = CoreRuntime.from_home(home, fake=True)
    migrated = snapshot(home)
    migration_checks = assert_preserved(baseline, migrated, label="post-migration")

    before_turns = int(migrated["counts"].get("turns") or 0)
    before_receipts = int(migrated["counts"].get("house_receipts") or 0)
    runtime.chat(
        NormalizedMessage(
            content="Confirm the upgraded synthetic home with one deterministic response.",
            speaker_id="synthetic-human",
            interface="release-canary",
            room_id=runtime.room_id,
            external_id="synthetic-v070:upgrade-turn",
        ),
        model_route="default",
    )
    after_turn = snapshot(home)
    turn_checks = assert_preserved(baseline, after_turn, label="post-turn")
    if int(after_turn["counts"].get("turns") or 0) <= before_turns:
        raise AssertionError("current fake-provider turn did not add transcript records")
    if after_turn["counts"].get("house_receipts") is not None:
        if int(after_turn["counts"].get("house_receipts") or 0) < before_receipts:
            raise AssertionError("current fake-provider turn removed an existing receipt")

    if archive.exists():
        archive.unlink()
    pack_home(home, archive)
    archive_digest = sha256_file(archive)
    pack_info = pack_manifest(archive)

    if restored.exists():
        shutil.rmtree(restored)
    restore_home(archive, restored)
    CoreRuntime.from_home(restored, fake=True)
    restored_snapshot = snapshot(restored)
    restore_checks = assert_preserved(after_turn, restored_snapshot, label="restored-home")

    exact_count_tables = (
        "state_events",
        "memory_records",
        "memory_events",
        "turns",
        "artifacts",
        "artifact_events",
        "image_assets",
        "image_events",
        "image_jobs",
        "bells",
        "bell_events",
        "house_receipts",
    )
    for table in exact_count_tables:
        before = after_turn["counts"].get(table)
        after = restored_snapshot["counts"].get(table)
        if before != after:
            raise AssertionError(
                f"restored-home: count changed for {table}: {before!r} -> {after!r}"
            )
        restore_checks.append(f"count:{table}")

    workspace = restored / "workspace" / "release-canary.txt"
    if not workspace.is_file():
        raise AssertionError("restored-home: synthetic workspace witness is missing")
    if sha256_file(workspace) != baseline["workspace_sha256"]:
        raise AssertionError("restored-home: synthetic workspace witness hash changed")

    result = {
        "schema_version": "vestigia.release-upgrade-canary.v0.1",
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "runtime_version": baseline["runtime_version"],
            "commit": baseline["source_commit"],
            "schema_version": baseline["schema_version"],
        },
        "target": {
            "runtime_version": __version__,
            "schema_version": migrated["schema_version"],
        },
        "baseline": baseline,
        "post_migration": migrated,
        "post_fake_turn": after_turn,
        "restored": restored_snapshot,
        "pack": {
            "sha256": archive_digest,
            "size_bytes": archive.stat().st_size,
            **pack_info,
            "restore_hash_verification": "passed",
        },
        "checks": {
            "migration": sorted(set(migration_checks)),
            "fake_provider_turn": sorted(
                set(turn_checks + ["turn_count_increased", "receipts_non_decreasing"])
            ),
            "restore": sorted(
                set(restore_checks + ["workspace_hash", "pack_manifest_hashes"])
            ),
        },
        "privacy": {
            "specimen": "synthetic",
            "resident_content": False,
            "secret_values": False,
            "message_content_in_evidence": False,
        },
        "result": "passed",
    }
    json_dump(evidence, result)
    print(
        json.dumps(
            {
                "result": "passed",
                "source_version": baseline["runtime_version"],
                "target_version": __version__,
                "source_schema": baseline["schema_version"],
                "target_schema": migrated["schema_version"],
                "pack_sha256": archive_digest,
                "turns_before": before_turns,
                "turns_after": after_turn["counts"].get("turns"),
            },
            sort_keys=True,
        )
    )
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)

    seed_parser = sub.add_parser("seed")
    seed_parser.add_argument("--home", type=Path, required=True)
    seed_parser.add_argument("--evidence", type=Path, required=True)

    upgrade_parser = sub.add_parser("upgrade")
    upgrade_parser.add_argument("--home", type=Path, required=True)
    upgrade_parser.add_argument("--baseline", type=Path, required=True)
    upgrade_parser.add_argument("--archive", type=Path, required=True)
    upgrade_parser.add_argument("--restored", type=Path, required=True)
    upgrade_parser.add_argument("--evidence", type=Path, required=True)
    upgrade_parser.add_argument("--expected-version", required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "seed":
        return seed(args.home.resolve(), args.evidence.resolve())
    return upgrade(
        args.home.resolve(),
        args.baseline.resolve(),
        args.archive.resolve(),
        args.restored.resolve(),
        args.evidence.resolve(),
        str(args.expected_version),
    )


if __name__ == "__main__":
    raise SystemExit(main())
