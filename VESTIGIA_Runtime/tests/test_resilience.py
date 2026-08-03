from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from vestigia.bells import BellService
from vestigia.config import load_config
from vestigia.curation import Curator
from vestigia.db import ContinuityDB
from vestigia.diagnostics import build_doctor_report, write_support_bundle
from vestigia.home import (
    RUNTIME_CONTRACT,
    V03_CONTRACT_MARKER,
    V04_CONTRACT_MARKER,
    V05_CONTRACT_MARKER,
    V06_CONTRACT_MARKER,
    V061_CONTRACT_MARKER,
    initialize_home,
)
from vestigia.house_tools import HousePort
from vestigia.images import ImageService
from vestigia.packing import pack_home, restore_home
from vestigia.providers.fake import FakeProvider
from vestigia.runtime import CoreRuntime


FIXTURE_MATRIX = (
    Path(__file__).parent / "fixtures" / "historical_homes.json"
)
CONTRACT_MARKERS = [
    V03_CONTRACT_MARKER,
    V04_CONTRACT_MARKER,
    V05_CONTRACT_MARKER,
    V06_CONTRACT_MARKER,
    V061_CONTRACT_MARKER,
]


def _services(home: Path):
    config = load_config(home)
    db = ContinuityDB(home / "memory" / "continuity.db")
    db.initialize()
    bells = BellService(db, str(config.get("resident.id")), str(config.get("room.id")))
    curator = Curator(config, db)
    images = ImageService(config, db, fake=True)
    house = HousePort(
        config,
        db,
        queue_for_review=curator.queue,
        open_curation=curator.create_batch,
        image_service=images,
    )
    return config, db, bells, images, house


def _truncate_contract(home: Path, marker: str | None) -> None:
    text = RUNTIME_CONTRACT
    if marker is None:
        text = text.split(V03_CONTRACT_MARKER, 1)[0].rstrip() + "\n"
    else:
        index = CONTRACT_MARKERS.index(marker)
        if index + 1 < len(CONTRACT_MARKERS):
            text = text.split(CONTRACT_MARKERS[index + 1], 1)[0].rstrip() + "\n"
    (home / "runtime_contract.md").write_text(text, encoding="utf-8")


def _downgrade_home(home: Path, fixture: dict[str, object]) -> None:
    db_path = home / "memory" / "continuity.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        for table in fixture["drop_tables"]:
            safe = str(table).replace('"', '""')
            connection.execute(f'DROP TABLE IF EXISTS "{safe}"')
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
            (str(fixture["schema_meta"]),),
        )
        connection.commit()
    finally:
        connection.close()
    _truncate_contract(home, fixture.get("contract_marker"))


def _seed_invariants(home: Path) -> dict[str, str]:
    config = load_config(home)
    db = ContinuityDB(home / "memory" / "continuity.db")
    db.initialize()
    memory_id = db.add_memory(
        resident_id=str(config.get("resident.id")),
        room_id=str(config.get("room.id")),
        content="Synthetic migration witness.",
        memory_type="boundary",
        tier="warm",
        authorship="resident",
        authority_state="resident_stated",
        status="accepted",
        actor="resident:fixture",
        reason="migration fixture",
        tags=["fixture"],
    )
    turn_id = db.add_turn(
        resident_id=str(config.get("resident.id")),
        room_id=str(config.get("room.id")),
        speaker_role="user",
        speaker_id="fixture-human",
        content="Synthetic historical turn.",
        interface="fixture",
        external_id=f"fixture:{memory_id}",
    )
    with db.connect() as connection:
        row = connection.execute(
            "SELECT content_hash FROM memory_records WHERE id=?", (memory_id,)
        ).fetchone()
    return {
        "memory_id": memory_id,
        "turn_id": turn_id,
        "content_hash": str(row["content_hash"]),
    }


def test_historical_home_fixture_matrix_migrates_idempotently(tmp_path: Path) -> None:
    matrix = json.loads(FIXTURE_MATRIX.read_text(encoding="utf-8"))
    assert matrix["schema_version"] == "vestigia.test-home-matrix.v0.1"
    for fixture in matrix["fixtures"]:
        home = initialize_home(
            tmp_path / str(fixture["release"]).replace(".", "-"),
            name="Fixture Resident",
            resident_id="fixture-resident",
        )
        invariants = _seed_invariants(home)
        _downgrade_home(home, fixture)

        CoreRuntime.from_home(home, provider=FakeProvider(), fake=True)
        BellService(
            ContinuityDB(home / "memory" / "continuity.db"),
            "fixture-resident",
            "hearth",
        )
        first_contract = (home / "runtime_contract.md").read_text(encoding="utf-8")
        first_db = ContinuityDB(home / "memory" / "continuity.db")
        with first_db.connect() as connection:
            memory = connection.execute(
                """
                SELECT r.id, r.content_hash, r.authority_state,
                       (SELECT status FROM memory_events e
                        WHERE e.record_id=r.id ORDER BY rowid DESC LIMIT 1) AS status
                FROM memory_records r WHERE r.id=?
                """,
                (invariants["memory_id"],),
            ).fetchone()
            turn = connection.execute(
                "SELECT id FROM turns WHERE id=?", (invariants["turn_id"],)
            ).fetchone()
            schema = connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()
            table_count = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
            memory_count = connection.execute(
                "SELECT COUNT(*) FROM memory_records"
            ).fetchone()[0]
            turn_count = connection.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        assert memory["id"] == invariants["memory_id"]
        assert memory["content_hash"] == invariants["content_hash"]
        assert memory["authority_state"] == "resident_stated"
        assert memory["status"] == "accepted"
        assert turn["id"] == invariants["turn_id"]
        assert schema["value"] == "4"
        for marker in CONTRACT_MARKERS:
            assert marker in first_contract

        # A second startup is a no-op for durable evidence and current plaques.
        CoreRuntime.from_home(home, provider=FakeProvider(), fake=True)
        with first_db.connect() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM memory_records"
            ).fetchone()[0] == memory_count
            assert connection.execute(
                "SELECT COUNT(*) FROM turns"
            ).fetchone()[0] == turn_count
            assert connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0] == table_count
        assert (home / "runtime_contract.md").read_text(encoding="utf-8") == first_contract


def test_interrupted_database_transaction_rolls_back(tmp_path: Path) -> None:
    home = initialize_home(tmp_path / "rollback", name="Rollback")
    db = ContinuityDB(home / "memory" / "continuity.db")
    with pytest.raises(RuntimeError, match="simulated interruption"):
        with db.connect() as connection:
            connection.execute(
                """
                INSERT INTO state_events
                (id, resident_id, from_state, to_state, actor, reason, created_at)
                VALUES ('state_interrupted', 'rollback', 'ORIENTATION', 'ACTIVE',
                        'test', 'should roll back', ?)
                """,
                (datetime.now(UTC).isoformat(),),
            )
            raise RuntimeError("simulated interruption")
    with db.connect() as connection:
        row = connection.execute(
            "SELECT id FROM state_events WHERE id='state_interrupted'"
        ).fetchone()
    assert row is None


def test_stale_running_image_job_is_requeued_on_restart(tmp_path: Path) -> None:
    home = initialize_home(tmp_path / "image-recovery", name="Image Recovery")
    config, db, _, images, _ = _services(home)
    queued = images.queue_job(
        "generate",
        {"prompt": "A recovery lantern", "count": 1},
        turn_id="turn_fixture",
        delivery={"kind": "discord_channel", "id": "123"},
    )
    job_id = str(queued["job_id"])
    stale = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    with db.connect() as connection:
        connection.execute(
            "UPDATE image_jobs SET status='running', updated_at=? WHERE id=?",
            (stale, job_id),
        )
    restarted = ImageService(config, db, fake=True)
    jobs = {item["id"]: item for item in restarted.jobs(limit=20)}
    assert jobs[job_id]["status"] == "queued"
    claimed = restarted.claim_next_job()
    assert claimed and claimed["id"] == job_id


def test_once_bell_advances_before_delivery_and_does_not_auto_retry(tmp_path: Path) -> None:
    home = initialize_home(tmp_path / "bell-recovery", name="Bell Recovery")
    config = load_config(home)
    db = ContinuityDB(home / "memory" / "continuity.db")
    service = BellService(db, "bell-recovery", "hearth")
    scheduled = datetime.now(UTC) + timedelta(milliseconds=20)
    bell = service.create(
        title="One breath",
        purpose="look_around",
        prompt="Notice, or choose nothing.",
        schedule_kind="once",
        schedule={"at": scheduled.isoformat()},
        timezone="UTC",
        created_by="resident:bell-recovery",
        delivery_interface="discord",
        delivery_target={"kind": "channel", "id": "123"},
    )
    fired = service.mark_fired(bell.id, fired_at=scheduled)
    service.event(
        bell.id,
        "delivery_failed",
        "failed",
        payload={"error_type": "SimulatedDisconnect"},
    )
    restarted = BellService(db, "bell-recovery", "hearth")
    assert fired.status == "completed"
    assert restarted.due(scheduled + timedelta(minutes=1)) == []
    assert any(
        item["event_type"] == "delivery_failed"
        for item in restarted.events(bell.id, limit=20)
    )


def test_doctor_observes_without_advancing_bells_or_interface_events(tmp_path: Path) -> None:
    home = initialize_home(tmp_path / "nonmutating-doctor", name="Nonmutating Doctor")
    config, db, bells, images, house = _services(home)
    bell = bells.create(
        title="Pending invitation",
        purpose="look_around",
        prompt="Notice, or choose nothing.",
        schedule_kind="once",
        schedule={"at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat()},
        timezone="UTC",
        created_by="resident:nonmutating-doctor",
        delivery_interface="discord",
        delivery_target={"kind": "channel", "id": "123"},
    )
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    with db.connect() as connection:
        connection.execute(
            "UPDATE bells SET next_fire_at=? WHERE id=?", (past, bell.id)
        )
    event_id, created = db.record_discord_reaction_event(
        resident_id="nonmutating-doctor",
        room_id="hearth",
        action="added",
        actor_id="human",
        actor_label="Human",
        channel_id="123",
        guild_id="456",
        target_message_id="789",
        target_excerpt="hello",
        emoji="💋",
        emoji_id=None,
        trust_class="allowlisted",
    )
    assert created
    report = build_doctor_report(
        config, db, bells=bells, house=house, images=images
    )
    assert report["operations"]["bells"]["due_now"] == 1
    current = bells.get(bell.id)
    assert current.status == "active"
    assert current.last_fired_at is None
    pending = db.pending_interface_events("nonmutating-doctor", "hearth")
    assert [item["id"] for item in pending] == [event_id]


def test_atomic_pack_failure_preserves_existing_target(tmp_path: Path) -> None:
    home = initialize_home(tmp_path / "pack-home", name="Pack Home")
    target = tmp_path / "pack-home.vestigia.zip"
    target.write_bytes(b"known-good-existing-pack")
    original_write = zipfile.ZipFile.write
    calls = 0

    def fail_first_file(self, filename, arcname=None, compress_type=None, compresslevel=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated pack interruption")
        return original_write(self, filename, arcname, compress_type, compresslevel)

    with patch.object(zipfile.ZipFile, "write", fail_first_file):
        with pytest.raises(OSError, match="simulated pack interruption"):
            pack_home(home, target)
    assert target.read_bytes() == b"known-good-existing-pack"
    assert not list(tmp_path.glob(f".{target.name}.tmp-*"))


def test_pack_restore_round_trip_is_hash_verified(tmp_path: Path) -> None:
    home = initialize_home(tmp_path / "roundtrip", name="Roundtrip")
    db = ContinuityDB(home / "memory" / "continuity.db")
    db.add_turn(
        resident_id="roundtrip",
        room_id="hearth",
        speaker_role="user",
        speaker_id="human",
        content="Round-trip witness.",
        interface="test",
    )
    archive = pack_home(home, tmp_path / "roundtrip.zip")
    restored = restore_home(archive, tmp_path / "roundtrip-restored")
    restored_db = ContinuityDB(restored / "memory" / "continuity.db")
    assert restored_db.recent_turn_count("roundtrip", "hearth") == 1


def test_doctor_and_support_bundle_are_private_by_default(tmp_path: Path) -> None:
    home = initialize_home(tmp_path / "private-doctor", name="Private Resident")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=sk-proj-super-secret-value\n"
        "DISCORD_BOT_TOKEN=discord-super-secret-value\n",
        encoding="utf-8",
    )
    config = load_config(home, env_file=env_file)
    db = ContinuityDB(home / "memory" / "continuity.db")
    db.initialize()
    db.add_memory(
        resident_id="private-resident",
        room_id="hearth",
        content="VERY_PRIVATE_MEMORY_SENTENCE",
        memory_type="identity",
        tier="core",
        authorship="resident",
        authority_state="resident_stated",
        status="accepted",
        actor="resident",
        reason="private test",
    )
    bells = BellService(db, "private-resident", "hearth")
    curator = Curator(config, db)
    images = ImageService(config, db, fake=True)
    house = HousePort(
        config,
        db,
        queue_for_review=curator.queue,
        open_curation=curator.create_batch,
        image_service=images,
    )
    report = build_doctor_report(
        config, db, bells=bells, house=house, images=images
    )
    assert report["database"]["integrity"] == "ok"
    assert report["database"]["schema_version"] == "4"
    assert report["credentials"] == {
        "openai_key": "present",
        "discord_token": "present",
        "values_exported": False,
    }
    target = write_support_bundle(config, db, report, tmp_path / "support.zip")
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        names = set(archive.namelist())
        assert names == {
            "MANIFEST.json",
            "doctor-report.json",
            "effective-config.redacted.yaml",
            "database-inventory.json",
            "recent-failed-receipts.json",
        }
        combined = "\n".join(
            archive.read(name).decode("utf-8") for name in sorted(names)
        )
    assert "sk-proj-super-secret-value" not in combined
    assert "discord-super-secret-value" not in combined
    assert "VERY_PRIVATE_MEMORY_SENTENCE" not in combined
    assert str(home) not in combined
    assert "raw SQLite database" in combined
