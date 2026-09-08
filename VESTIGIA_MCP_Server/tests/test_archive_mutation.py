import json
from pathlib import Path

import pytest

from vestigia_mcp.adapters.archive import ArchiveError
from vestigia_mcp.archive_mutation import ArchiveMutationStore


def make_store(
    tmp_path: Path,
    *,
    prefixes: tuple[str, ...] = ("02_Journal",),
    max_bytes: int = 1000,
) -> tuple[ArchiveMutationStore, Path, Path]:
    live = tmp_path / "live"
    (live / "02_Journal").mkdir(parents=True)
    (live / "00_Bootloader").mkdir()
    state = tmp_path / "state"
    store = ArchiveMutationStore(
        live,
        state,
        "test-deployment",
        write_prefixes=prefixes,
        max_bytes=max_bytes,
    )
    return store, live, state


def test_stage_then_promote_create_is_digest_bound_and_atomic(tmp_path: Path) -> None:
    store, live, _ = make_store(tmp_path)

    staged = store.stage_text(
        "02_Journal/lantern.md",
        "The lantern remains lit.\n",
        expected_base_sha256="absent",
        reason="bounded canary",
    )

    assert staged["status"] == "staged"
    assert staged["operation"] == "create"
    assert staged["canonical_changed"] is False
    assert "content" not in staged
    assert not (live / "02_Journal" / "lantern.md").exists()

    promoted = store.promote(staged["stage_id"], staged["proposal_sha256"])

    assert promoted["status"] == "promoted"
    assert promoted["canonical_changed"] is True
    assert promoted["atomic_replace"] is True
    assert promoted["result_sha256"] == staged["content_sha256"]
    assert (live / "02_Journal" / "lantern.md").read_text(
        encoding="utf-8"
    ) == "The lantern remains lit.\n"

    repeated = store.promote(staged["stage_id"], staged["proposal_sha256"])
    assert repeated["already_promoted"] is True
    assert repeated["canonical_changed"] is False


def test_replace_revalidates_base_and_refuses_conflict(tmp_path: Path) -> None:
    store, live, _ = make_store(tmp_path)
    target = live / "02_Journal" / "continuity.md"
    target.write_text("first\n", encoding="utf-8")

    staged = store.stage_text("02_Journal/continuity.md", "second\n")
    target.write_text("someone else arrived\n", encoding="utf-8")

    inspected = store.inspect_stage(staged["stage_id"])
    assert inspected["validation"]["ready"] is False
    assert "changed after staging" in inspected["validation"]["detail"]

    with pytest.raises(ArchiveError, match="changed after staging"):
        store.promote(staged["stage_id"], staged["proposal_sha256"])
    assert target.read_text(encoding="utf-8") == "someone else arrived\n"
    assert store.inspect_stage(staged["stage_id"])["status"] == "staged"


def test_promotion_retry_reconciles_content_written_before_stage_status(
    tmp_path: Path,
) -> None:
    store, live, _ = make_store(tmp_path)
    staged = store.stage_text(
        "02_Journal/recover.md",
        "already landed\n",
        expected_base_sha256="absent",
    )
    (live / "02_Journal" / "recover.md").write_text(
        "already landed\n",
        encoding="utf-8",
    )

    recovered = store.promote(staged["stage_id"], staged["proposal_sha256"])

    assert recovered["already_promoted"] is True
    assert recovered["promotion_reconciled"] is True
    assert recovered["canonical_changed"] is False
    assert store.inspect_stage(staged["stage_id"])["status"] == "promoted"


def test_stage_honors_expected_hash_prefix_suffix_and_size_bounds(
    tmp_path: Path,
) -> None:
    store, live, _ = make_store(tmp_path, max_bytes=8)
    target = live / "02_Journal" / "note.md"
    target.write_text("old", encoding="utf-8")

    with pytest.raises(ArchiveError, match="does not match"):
        store.stage_text(
            "02_Journal/note.md",
            "new",
            expected_base_sha256="0" * 64,
        )
    with pytest.raises(ArchiveError, match="outside configured"):
        store.stage_text("00_Bootloader/house_index.json", "{}")
    with pytest.raises(ArchiveError, match="text-like"):
        store.stage_text("02_Journal/picture.png", "x")
    with pytest.raises(ArchiveError, match="byte ceiling"):
        store.stage_text("02_Journal/large.md", "too large")
    with pytest.raises(ArchiveError, match="Parent traversal"):
        store.stage_text("../escape.md", "no")
    with pytest.raises(ArchiveError, match="Windows-unsafe"):
        store.stage_text("02_Journal/CON.md", "no")
    with pytest.raises(ArchiveError, match="NUL"):
        store.stage_text("02_Journal/nul.md", "x\x00y")
    with pytest.raises(ArchiveError, match="parent directory"):
        store.stage_text("02_Journal/missing/note.md", "no")


def test_empty_prefix_grant_denies_stage_and_promotion_surface(tmp_path: Path) -> None:
    store, _, _ = make_store(tmp_path, prefixes=())
    capabilities = store.capabilities()

    assert capabilities["promotion_configured"] is False
    assert capabilities["write_prefixes"] == []
    assert capabilities["direct_write_available"] is False
    with pytest.raises(ArchiveError, match="no configured write prefixes"):
        store.stage_text("02_Journal/nope.md", "nope")


def test_stage_integrity_discard_and_content_visibility(tmp_path: Path) -> None:
    store, _, state = make_store(tmp_path)
    staged = store.stage_text("02_Journal/draft.md", "draft\n")

    metadata = store.list_stages()
    assert metadata["total"] == 1
    assert "content" not in metadata["stages"][0]
    inspected = store.inspect_stage(staged["stage_id"], include_content=True)
    assert inspected["content"] == "draft\n"
    assert inspected["validation"]["ready"] is True

    discarded = store.discard_stage(staged["stage_id"], reason="not this turn")
    assert discarded["status"] == "discarded"
    assert discarded["canonical_changed"] is False
    assert store.list_stages()["total"] == 0
    assert store.list_stages(status="discarded")["total"] == 1

    stage_path = state / "archive-stages" / f"{staged['stage_id']}.json"
    record = json.loads(stage_path.read_text(encoding="utf-8"))
    record["content"] = "tampered"
    stage_path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ArchiveError, match="integrity verification"):
        store.inspect_stage(staged["stage_id"])


def test_state_directory_inside_archive_is_refused(tmp_path: Path) -> None:
    live = tmp_path / "live"
    (live / "02_Journal").mkdir(parents=True)
    store = ArchiveMutationStore(
        live,
        live / ".mcp-state",
        "bad-deployment",
        write_prefixes=("02_Journal",),
    )

    assert store.capabilities()["write_boundary_available"] is False
    with pytest.raises(ArchiveError, match="must not be inside"):
        store.stage_text("02_Journal/nope.md", "nope")


def test_stage_cannot_cross_deployment_identity(tmp_path: Path) -> None:
    store, live, state = make_store(tmp_path)
    staged = store.stage_text("02_Journal/private.md", "held\n")
    other = ArchiveMutationStore(
        live,
        state,
        "other-deployment",
        write_prefixes=("02_Journal",),
    )

    with pytest.raises(ArchiveError, match="different MCP deployment"):
        other.inspect_stage(staged["stage_id"])
