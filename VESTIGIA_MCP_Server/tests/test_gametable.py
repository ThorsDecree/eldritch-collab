import json

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
    assert "Liora private card" not in json.dumps(completed)
