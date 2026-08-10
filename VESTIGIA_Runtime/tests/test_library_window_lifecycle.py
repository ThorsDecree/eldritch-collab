from __future__ import annotations

from pathlib import Path

import pytest

from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.home import initialize_home
from vestigia.house_tools import HousePort
from vestigia.library_window_lifecycle import (
    authorize_notebook_lifecycle,
    authorize_source_management,
)
from vestigia.library_window_store import create_notebook


def _house(tmp_path: Path) -> HousePort:
    home = initialize_home(tmp_path / "home", name="Lifecycle Test", glyph="🔐")
    config = load_config(home)
    db = ContinuityDB(home / "memory" / "continuity.db")
    db.initialize()
    return HousePort(config, db)


def _turn(house: HousePort, text: str, *, interface: str = "cli") -> str:
    return house.db.add_turn(
        resident_id=house.resident_id,
        room_id=house.room_id,
        speaker_role="user",
        speaker_id="local-user",
        content=text,
        interface=interface,
    )


def test_negated_delete_language_requests_confirmation_instead_of_authorizing(tmp_path: Path) -> None:
    house = _house(tmp_path)
    notebook_id = create_notebook(house, title="Keep me")["notebook_id"]
    turn_id = _turn(house, "Do not delete this notebook.")
    payload = {
        "action": "research.notebook",
        "mode": "discard",
        "notebook_id": notebook_id,
    }

    with pytest.raises(
        PermissionError, match=r"Delete notebook sha256:[0-9a-f]{12}\? Y/N"
    ) as exc:
        authorize_notebook_lifecycle(
            house,
            payload,
            {"turn_id": turn_id, "interface": "cli", "invocation": "conversation"},
        )

    assert getattr(exc.value, "house_error_code", None) == "confirmation_required"
    with house.db.connect() as connection:
        assert connection.execute(
            "SELECT id FROM research_notebooks WHERE id=?", (notebook_id,)
        ).fetchone() is not None


def test_yes_consumes_only_matching_pending_confirmation(tmp_path: Path) -> None:
    house = _house(tmp_path)
    notebook_id = create_notebook(house, title="Delete me")["notebook_id"]
    other_id = create_notebook(house, title="Not me")["notebook_id"]
    payload = {
        "action": "research.notebook",
        "mode": "discard",
        "notebook_id": notebook_id,
    }
    first_turn = _turn(house, "Please remove that temporary notebook.")

    with pytest.raises(PermissionError, match="Delete notebook"):
        authorize_notebook_lifecycle(
            house, payload, {"turn_id": first_turn, "interface": "cli"}
        )

    yes_turn = _turn(house, "Y")
    authorize_notebook_lifecycle(
        house, payload, {"turn_id": yes_turn, "interface": "cli"}
    )

    with pytest.raises(PermissionError, match="No pending confirmation"):
        authorize_notebook_lifecycle(
            house,
            {
                "action": "research.notebook",
                "mode": "discard",
                "notebook_id": other_id,
            },
            {"turn_id": yes_turn, "interface": "cli"},
        )


def test_no_cancels_pending_confirmation(tmp_path: Path) -> None:
    house = _house(tmp_path)
    notebook_id = create_notebook(house, title="Maybe")["notebook_id"]
    payload = {
        "action": "research.notebook",
        "mode": "discard",
        "notebook_id": notebook_id,
    }
    first_turn = _turn(house, "Delete the temporary notebook.", interface="discord")
    with pytest.raises(PermissionError, match="Delete notebook"):
        authorize_notebook_lifecycle(
            house, payload, {"turn_id": first_turn, "interface": "discord"}
        )

    no_turn = _turn(house, "N", interface="discord")
    with pytest.raises(PermissionError, match="cancelled"):
        authorize_notebook_lifecycle(
            house, payload, {"turn_id": no_turn, "interface": "discord"}
        )


def test_autonomous_noninteractive_resident_discard_needs_no_human_round_trip(tmp_path: Path) -> None:
    house = _house(tmp_path)
    notebook_id = create_notebook(house, title="Resident housekeeping")["notebook_id"]
    authorize_notebook_lifecycle(
        house,
        {
            "action": "research.notebook",
            "mode": "discard",
            "notebook_id": notebook_id,
        },
        {"turn_id": "bell-turn", "interface": "bell", "invocation": "conversation"},
    )


def test_retain_and_detach_do_not_use_keyword_authorization(tmp_path: Path) -> None:
    house = _house(tmp_path)
    turn_id = _turn(
        house, "Do not retain or remove anything based on these words alone."
    )
    authorize_notebook_lifecycle(
        house,
        {"action": "research.notebook", "mode": "retain", "notebook_id": "notebook_x"},
        {"turn_id": turn_id, "interface": "cli"},
    )
    authorize_notebook_lifecycle(
        house,
        {
            "action": "research.notebook",
            "mode": "remove_source",
            "notebook_id": "notebook_x",
        },
        {"turn_id": turn_id, "interface": "cli"},
    )


def test_source_revocation_uses_same_yes_no_boundary(tmp_path: Path) -> None:
    house = _house(tmp_path)
    turn_id = _turn(house, "Please revoke that source.", interface="discord")
    payload = {
        "action": "source.manage",
        "mode": "revoke",
        "source_id": "source_123",
    }
    with pytest.raises(PermissionError, match="Revoke source retrieval"):
        authorize_source_management(
            house, payload, {"turn_id": turn_id, "interface": "discord"}
        )
