import json
import sqlite3

import pytest

from vestigia_mcp.gametable import GameConflictError, GameTableError, GameTableStore


def _deck(prefix: str) -> list[str]:
    return [f"{prefix} private card {number}" for number in range(1, 9)]


def _create(store: GameTableStore) -> dict[str, object]:
    return store.create_game(
        title="Lantern Commander",
        profile_id="magic.commander.v0.1",
        seats=[
            {
                "seat_id": "liora",
                "display_name": "Liora",
            },
            {
                "seat_id": "jeff",
                "display_name": "Jeff",
            },
        ],
    )


def test_hidden_hands_and_library_order_are_filtered_by_seat(tmp_path) -> None:
    store = GameTableStore(tmp_path / "games", "test-deployment")
    created = _create(store)
    game_id = str(created["game_id"])
    tokens = created["seat_tokens"]
    assert isinstance(tokens, dict)
    liora_token = str(tokens["liora"])
    jeff_token = str(tokens["jeff"])

    public_lobby = store.view(game_id=game_id)
    assert "Liora private card" not in json.dumps(public_lobby)
    assert "Jeff private card" not in json.dumps(public_lobby)

    store.load_deck(
        game_id=game_id,
        seat_token=liora_token,
        expected_revision=0,
        deck=_deck("Liora"),
    )
    assert "Liora private card" not in json.dumps(store.view(game_id=game_id))
    with pytest.raises(GameTableError, match="Every seat must load a deck"):
        store.start_game(
            game_id=game_id,
            seat_token=liora_token,
            expected_revision=1,
        )
    store.load_deck(
        game_id=game_id,
        seat_token=jeff_token,
        expected_revision=1,
        deck=_deck("Jeff"),
    )

    started = store.start_game(
        game_id=game_id,
        seat_token=liora_token,
        expected_revision=2,
    )
    assert started["revision"] == 3
    assert len(started["event"]["private"]["opening_hand"]) == 7

    public = store.view(game_id=game_id)
    liora = store.view(game_id=game_id, seat_token=liora_token)
    jeff = store.view(game_id=game_id, seat_token=jeff_token)
    public_json = json.dumps(public)
    liora_json = json.dumps(liora)
    jeff_json = json.dumps(jeff)
    assert "Liora private card" not in public_json
    assert "Jeff private card" not in public_json
    assert "Jeff private card" not in liora_json
    assert "Liora private card" not in jeff_json
    assert liora["private"]["library"] == {"count": 1}
    assert jeff["private"]["library"] == {"count": 1}

    public_events = store.events(game_id=game_id)
    liora_events = store.events(game_id=game_id, seat_token=liora_token)
    assert "opening_hand" not in json.dumps(public_events)
    assert "Liora private card" not in json.dumps(public_events)
    assert "Liora private card" in json.dumps(liora_events)
    assert "Jeff private card" not in json.dumps(liora_events)

    database_text = (tmp_path / "games" / "gametable.sqlite3").read_bytes()
    assert liora_token.encode("utf-8") not in database_text
    assert jeff_token.encode("utf-8") not in database_text


def test_actions_are_revision_bound_and_cannot_control_an_opponent_card(tmp_path) -> None:
    store = GameTableStore(tmp_path / "games", "test-deployment")
    created = _create(store)
    game_id = str(created["game_id"])
    tokens = created["seat_tokens"]
    assert isinstance(tokens, dict)
    liora_token = str(tokens["liora"])
    jeff_token = str(tokens["jeff"])
    store.load_deck(
        game_id=game_id,
        seat_token=liora_token,
        expected_revision=0,
        deck=_deck("Liora"),
    )
    store.load_deck(
        game_id=game_id,
        seat_token=jeff_token,
        expected_revision=1,
        deck=_deck("Jeff"),
    )
    store.start_game(game_id=game_id, seat_token=liora_token, expected_revision=2)
    store.keep_opening_hand(game_id=game_id, seat_token=liora_token, expected_revision=3)
    store.keep_opening_hand(game_id=game_id, seat_token=jeff_token, expected_revision=4)
    liora_view = store.view(game_id=game_id, seat_token=liora_token)
    liora_hand = liora_view["private"]["hand"]
    card_id = liora_hand[0]["instance_id"]

    with pytest.raises(GameConflictError, match="expected 0, current 5"):
        store.act(
            game_id=game_id,
            seat_token=liora_token,
            expected_revision=0,
            action={"type": "play", "card_id": card_id},
        )

    played = store.act(
        game_id=game_id,
        seat_token=liora_token,
        expected_revision=5,
        action={"type": "play", "card_id": card_id},
    )
    assert played["revision"] == 6
    assert played["event"]["public"]["card"]["definition_ref"].startswith("Liora")

    passed = store.pass_priority(
        game_id=game_id,
        seat_token=liora_token,
        expected_revision=6,
    )
    assert passed["view"]["turn"]["priority_seat"] == "jeff"
    with pytest.raises(GameTableError, match="Card action is not permitted"):
        store.act(
            game_id=game_id,
            seat_token=jeff_token,
            expected_revision=7,
            action={"type": "tap", "card_id": card_id},
        )


def test_game_profiles_are_explicitly_rules_light(tmp_path) -> None:
    profiles = GameTableStore(tmp_path / "games", "test-deployment").profiles()
    commander = next(
        profile
        for profile in profiles["profiles"]
        if profile["profile_id"] == "magic.commander.v0.1"
    )
    assert commander["starting_life"] == 40
    assert commander["opening_hand_size"] == 7
    assert "no card oracle" in commander["rules_enforcement"]


def test_turn_based_actions_and_mutation_projection_keep_private_hands_private(tmp_path) -> None:
    store = GameTableStore(tmp_path / "games", "test-deployment")
    created = _create(store)
    game_id = str(created["game_id"])
    tokens = created["seat_tokens"]
    assert isinstance(tokens, dict)
    liora_token = str(tokens["liora"])
    jeff_token = str(tokens["jeff"])
    store.load_deck(game_id=game_id, seat_token=liora_token, expected_revision=0, deck=_deck("Liora"))
    store.load_deck(game_id=game_id, seat_token=jeff_token, expected_revision=1, deck=_deck("Jeff"))
    store.start_game(game_id=game_id, seat_token=liora_token, expected_revision=2)
    store.keep_opening_hand(game_id=game_id, seat_token=liora_token, expected_revision=3)
    activated = store.keep_opening_hand(game_id=game_id, seat_token=jeff_token, expected_revision=4)
    assert activated["view"]["viewer"] == {"kind": "public"}
    assert activated["view"]["turn"]["step"] == "upkeep"
    assert "Liora private card" not in json.dumps(activated)

    store.pass_priority(game_id=game_id, seat_token=liora_token, expected_revision=5)
    drawn = store.pass_priority(game_id=game_id, seat_token=jeff_token, expected_revision=6)
    assert drawn["view"]["turn"]["step"] == "draw"
    assert drawn["event"]["public"]["automatic"] == {
        "actions": [{"type": "draw", "seat_id": "liora", "count": 1}]
    }
    assert "Liora private card" not in json.dumps(drawn)
    assert len(store.view(game_id=game_id, seat_token=liora_token)["private"]["hand"]) == 8


def test_consented_shortcut_and_initial_play_state(tmp_path) -> None:
    store = GameTableStore(tmp_path / "games", "test-deployment")
    created = _create(store)
    game_id = str(created["game_id"])
    tokens = created["seat_tokens"]
    assert isinstance(tokens, dict)
    liora_token = str(tokens["liora"])
    jeff_token = str(tokens["jeff"])
    store.load_deck(game_id=game_id, seat_token=liora_token, expected_revision=0, deck=_deck("Liora"))
    store.load_deck(game_id=game_id, seat_token=jeff_token, expected_revision=1, deck=_deck("Jeff"))
    store.start_game(game_id=game_id, seat_token=liora_token, expected_revision=2)
    store.keep_opening_hand(game_id=game_id, seat_token=liora_token, expected_revision=3)
    store.keep_opening_hand(game_id=game_id, seat_token=jeff_token, expected_revision=4)

    hand = store.view(game_id=game_id, seat_token=liora_token)["private"]["hand"]
    card_id = hand[0]["instance_id"]
    played = store.act(
        game_id=game_id,
        seat_token=liora_token,
        expected_revision=5,
        action={
            "type": "play",
            "card_id": card_id,
            "initial_state": {"tapped": True, "counters": {"charge": 2}, "status_tags": ["skip_untap"]},
        },
    )
    public_card = played["event"]["public"]["card"]
    assert public_card["tapped"] is True
    assert public_card["counters"] == {"charge": 2}

    proposed = store.propose_shortcut(
        game_id=game_id,
        seat_token=liora_token,
        expected_revision=6,
        target={"turn_number": 1, "step": "precombat_main"},
    )
    proposal_id = proposed["event"]["public"]["shortcut"]["proposal_id"]
    completed = store.respond_shortcut(
        game_id=game_id,
        seat_token=jeff_token,
        expected_revision=7,
        proposal_id=proposal_id,
        accept=True,
    )
    assert completed["view"]["turn"]["step"] == "precombat_main"
    assert completed["event"]["public"]["status"] == "completed"
    assert completed["event"]["public"]["automatic"] == {
        "actions": [{"type": "draw", "seat_id": "liora", "count": 1}]
    }
    assert completed["view"]["viewer"] == {"kind": "public"}
    private_hand = store.view(game_id=game_id, seat_token=liora_token)["private"]["hand"]
    completed_json = json.dumps(completed)
    assert all(card["definition_ref"] not in completed_json for card in private_hand)


def test_standing_yields_compose_to_earliest_target_and_clear_on_action(tmp_path) -> None:
    store = GameTableStore(tmp_path / "games", "test-deployment")
    created = _create(store)
    game_id = str(created["game_id"])
    tokens = created["seat_tokens"]
    assert isinstance(tokens, dict)
    liora_token = str(tokens["liora"])
    jeff_token = str(tokens["jeff"])
    store.load_deck(game_id=game_id, seat_token=liora_token, expected_revision=0, deck=_deck("Liora"))
    store.load_deck(game_id=game_id, seat_token=jeff_token, expected_revision=1, deck=_deck("Jeff"))
    store.start_game(game_id=game_id, seat_token=liora_token, expected_revision=2)
    store.keep_opening_hand(game_id=game_id, seat_token=liora_token, expected_revision=3)
    store.keep_opening_hand(game_id=game_id, seat_token=jeff_token, expected_revision=4)

    pending = store.yield_priority(
        game_id=game_id,
        seat_token=liora_token,
        expected_revision=5,
        scope={"kind": "target", "turn_number": 1, "step": "precombat_main"},
    )
    assert pending["revision"] == 6
    assert pending["event"]["public"]["status"] == "pending"
    assert pending["event"]["public"]["awaiting_seats"] == ["jeff"]

    completed = store.yield_priority(
        game_id=game_id,
        seat_token=jeff_token,
        expected_revision=6,
        scope={"kind": "target", "turn_number": 1, "step": "end_step"},
    )
    assert completed["revision"] == 7
    assert completed["event"]["public"]["status"] == "completed"
    assert completed["event"]["public"]["target"] == {"turn_number": 1, "step": "precombat_main"}
    assert completed["view"]["turn"]["step"] == "precombat_main"
    assert completed["event"]["public"]["automatic"] == {
        "actions": [{"type": "draw", "seat_id": "liora", "count": 1}]
    }
    assert "yields" not in completed["view"]

    first = store.yield_priority(
        game_id=game_id,
        seat_token=liora_token,
        expected_revision=7,
        scope={"kind": "step"},
    )
    assert first["view"].get("yields")
    acted = store.act(
        game_id=game_id,
        seat_token=jeff_token,
        expected_revision=8,
        action={"type": "life", "delta": -1},
    )
    assert acted["revision"] == 9
    assert "yields" not in acted["view"]


def test_pending_effects_keep_priority_from_advancing_and_preserve_private_zone_boundaries(tmp_path) -> None:
    store = GameTableStore(tmp_path / "games", "test-deployment")
    created = _create(store)
    game_id = str(created["game_id"])
    tokens = created["seat_tokens"]
    assert isinstance(tokens, dict)
    liora_token = str(tokens["liora"])
    jeff_token = str(tokens["jeff"])
    store.load_deck(game_id=game_id, seat_token=liora_token, expected_revision=0, deck=_deck("Liora"))
    store.load_deck(game_id=game_id, seat_token=jeff_token, expected_revision=1, deck=_deck("Jeff"))
    store.start_game(game_id=game_id, seat_token=liora_token, expected_revision=2)
    store.keep_opening_hand(game_id=game_id, seat_token=liora_token, expected_revision=3)
    store.keep_opening_hand(game_id=game_id, seat_token=jeff_token, expected_revision=4)

    card_id = store.view(game_id=game_id, seat_token=liora_token)["private"]["hand"][0]["instance_id"]
    store.act(game_id=game_id, seat_token=liora_token, expected_revision=5, action={"type": "play", "card_id": card_id})
    declared = store.declare_effect(
        game_id=game_id,
        seat_token=liora_token,
        expected_revision=6,
        effect={"kind": "manual_effect", "label": "Test hidden-zone resolution", "source_card_id": card_id, "targets": ["opaque-target"]},
    )
    effect_id = declared["event"]["public"]["effect"]["effect_id"]
    assert "opaque-target" not in json.dumps(declared)
    store.pass_priority(game_id=game_id, seat_token=liora_token, expected_revision=7)
    ready = store.pass_priority(game_id=game_id, seat_token=jeff_token, expected_revision=8)
    assert ready["event"]["public"]["effect"]["state"] == "resolving"
    assert ready["view"]["turn"]["step"] == "upkeep"

    with pytest.raises(GameTableError, match="top pending effect"):
        store.act(game_id=game_id, seat_token=liora_token, expected_revision=9, action={"type": "life", "delta": 1})
    with pytest.raises(GameTableError, match="pending effect"):
        store.yield_priority(
            game_id=game_id,
            seat_token=liora_token,
            expected_revision=9,
            scope={"kind": "step"},
        )

    intermediate = store.resolve_effect(
        game_id=game_id,
        seat_token=liora_token,
        expected_revision=9,
        effect_id=effect_id,
        complete=False,
        operations=[
            {"type": "move", "card_id": card_id, "destination": "library"},
            {"type": "shuffle", "seat_id": "liora"},
            {"type": "reveal_top", "seat_id": "jeff", "count": 1, "visibility": "seat"},
            {"type": "random_int", "minimum": 1, "maximum": 20, "visibility": "seat", "seat_id": "jeff"},
        ],
    )
    public_text = json.dumps(intermediate)
    assert "Jeff private card" not in public_text
    assert intermediate["view"]["pending_effects"][0]["state"] == "resolving"
    assert "shuffle_commitment" in json.dumps(intermediate["event"]["public"])
    completed = store.resolve_effect(
        game_id=game_id,
        seat_token=liora_token,
        expected_revision=10,
        effect_id=effect_id,
        operations=[],
    )
    assert "pending_effects" not in completed["view"]
    assert completed["event"]["public"]["outcome"] == "resolved"
    jeff_events = store.events(game_id=game_id, seat_token=jeff_token)
    assert "Jeff private card" in json.dumps(jeff_events)
    remaining_liora_hand = store.view(game_id=game_id, seat_token=liora_token)["private"]["hand"]
    assert all(card["definition_ref"] not in json.dumps(jeff_events) for card in remaining_liora_hand)


def test_owned_state_repair_clears_ghost_state_and_tap_bundle_is_atomic(tmp_path) -> None:
    store = GameTableStore(tmp_path / "games", "test-deployment")
    created = _create(store)
    game_id = str(created["game_id"])
    tokens = created["seat_tokens"]
    assert isinstance(tokens, dict)
    liora_token = str(tokens["liora"])
    jeff_token = str(tokens["jeff"])
    store.load_deck(game_id=game_id, seat_token=liora_token, expected_revision=0, deck=_deck("Liora"))
    store.load_deck(game_id=game_id, seat_token=jeff_token, expected_revision=1, deck=_deck("Jeff"))
    store.start_game(game_id=game_id, seat_token=liora_token, expected_revision=2)
    store.keep_opening_hand(game_id=game_id, seat_token=liora_token, expected_revision=3)
    store.keep_opening_hand(game_id=game_id, seat_token=jeff_token, expected_revision=4)
    hand = store.view(game_id=game_id, seat_token=liora_token)["private"]["hand"]
    first, second = hand[0]["instance_id"], hand[1]["instance_id"]
    store.act(
        game_id=game_id,
        seat_token=liora_token,
        expected_revision=5,
        action={"type": "play", "card_id": first, "initial_state": {"counters": {"charge": 2}, "status_tags": ["temporary"]}},
    )
    store.act(game_id=game_id, seat_token=liora_token, expected_revision=6, action={"type": "play", "card_id": second})
    bundled = store.act(
        game_id=game_id,
        seat_token=liora_token,
        expected_revision=7,
        action={"type": "tap_bundle", "card_ids": [first, second]},
    )
    assert len(bundled["event"]["public"]["cards"]) == 2
    store.repair_state(
        game_id=game_id,
        seat_token=liora_token,
        expected_revision=8,
        reason="Correct an already-adjudicated hidden-zone move.",
        operations=[{"type": "move", "card_id": first, "destination": "library"}],
    )
    repaired = store.repair_state(
        game_id=game_id,
        seat_token=liora_token,
        expected_revision=9,
        reason="Restore the card after a test repair.",
        operations=[{"type": "move", "card_id": first, "destination": "battlefield"}],
    )
    restored = next(card for card in repaired["view"]["seats"][0]["battlefield"] if card["instance_id"] == first)
    assert restored["counters"] == {}
    assert restored["status_tags"] == []
    assert restored["tapped"] is False
    with pytest.raises(GameTableError, match="owned or controlled"):
        store.repair_state(
            game_id=game_id,
            seat_token=jeff_token,
            expected_revision=10,
            reason="Try to alter an opponent card.",
            operations=[{"type": "move", "card_id": first, "destination": "library"}],
        )


def test_existing_v01_state_gains_effect_fields_lazily(tmp_path) -> None:
    store = GameTableStore(tmp_path / "games", "test-deployment")
    created = _create(store)
    game_id = str(created["game_id"])
    database = tmp_path / "games" / "gametable.sqlite3"
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT state_json FROM games WHERE game_id = ?", (game_id,)).fetchone()
        assert row is not None
        legacy = json.loads(row[0])
        legacy.pop("pending_effects")
        legacy.pop("random_receipts")
        connection.execute(
            "UPDATE games SET state_json = ? WHERE game_id = ?",
            (json.dumps(legacy), game_id),
        )
    view = store.view(game_id=game_id)
    assert "pending_effects" not in view
