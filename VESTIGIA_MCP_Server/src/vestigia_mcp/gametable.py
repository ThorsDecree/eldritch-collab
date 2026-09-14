"""A small, event-sourced hidden-information tabletop game engine.

GameTable deliberately tracks a table; it does not pretend to be a full rules
engine for Magic or any other game.  The MCP server transports scoped requests
to this store, while this module owns its separate mutable game state.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


GAME_SCHEMA_VERSION = "vestigia.gametable.v0.1"
_SEAT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_CARD_REF_MAX_LENGTH = 512
_TITLE_MAX_LENGTH = 160


class GameTableError(RuntimeError):
    """A safe, player-facing GameTable error."""


class GameConflictError(GameTableError):
    """A state change was offered against an obsolete revision."""


@dataclass(frozen=True)
class GameProfile:
    profile_id: str
    title: str
    player_min: int
    player_max: int
    starting_life: int
    opening_hand_size: int
    turn_steps: tuple[str, ...]
    zones: tuple[str, ...]
    rules_enforcement: str

    def summary(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "title": self.title,
            "player_count": {"min": self.player_min, "max": self.player_max},
            "starting_life": self.starting_life,
            "opening_hand_size": self.opening_hand_size,
            "turn_steps": list(self.turn_steps),
            "zones": list(self.zones),
            "rules_enforcement": self.rules_enforcement,
            "card_database": "not bundled; card definition_ref values are caller supplied",
        }


PROFILES: dict[str, GameProfile] = {
    "generic.card-table.v0.1": GameProfile(
        profile_id="generic.card-table.v0.1",
        title="Generic Card Table",
        player_min=2,
        player_max=8,
        starting_life=20,
        opening_hand_size=0,
        turn_steps=("start", "main", "end"),
        zones=("library", "hand", "battlefield", "graveyard", "exile"),
        rules_enforcement="table-state tracking only; players resolve game rules",
    ),
    "magic.commander.v0.1": GameProfile(
        profile_id="magic.commander.v0.1",
        title="Magic: The Gathering — Commander tabletop profile",
        player_min=2,
        player_max=4,
        starting_life=40,
        opening_hand_size=7,
        turn_steps=(
            "untap",
            "upkeep",
            "draw",
            "precombat_main",
            "begin_combat",
            "declare_attackers",
            "declare_blockers",
            "combat_damage",
            "end_combat",
            "postcombat_main",
            "end_step",
            "cleanup",
        ),
        zones=(
            "library",
            "hand",
            "battlefield",
            "graveyard",
            "exile",
            "command",
        ),
        rules_enforcement=(
            "turn/priority and table-state tracking only; no card oracle, deck legality, "
            "trigger, target, or comprehensive-rules adjudication"
        ),
    ),
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def _require_nonblank(value: object, label: str, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise GameTableError(f"{label} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise GameTableError(f"{label} must not be blank")
    if len(cleaned) > max_length:
        raise GameTableError(f"{label} must be at most {max_length} characters")
    return cleaned


class GameTableStore:
    """SQLite-backed materialized state plus an append-only-ish event record.

    The visibility methods are the security boundary for ordinary MCP callers.
    A local operator who can directly read this SQLite database is necessarily
    inside the deployment trust boundary and can inspect its state.
    """

    def __init__(self, state_dir: Path, deployment_id: str):
        self._state_dir = state_dir.expanduser()
        self._path = self._state_dir / "gametable.sqlite3"
        self._deployment_id = deployment_id
        self._lock = threading.RLock()
        self._initialize()

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._lock:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS games (
                        game_id TEXT PRIMARY KEY,
                        state_json TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS seat_tokens (
                        game_id TEXT NOT NULL,
                        seat_id TEXT NOT NULL,
                        token_sha256 TEXT NOT NULL,
                        PRIMARY KEY (game_id, seat_id),
                        FOREIGN KEY (game_id) REFERENCES games(game_id)
                    );
                    CREATE TABLE IF NOT EXISTS game_events (
                        game_id TEXT NOT NULL,
                        sequence INTEGER NOT NULL,
                        event_id TEXT NOT NULL,
                        event_sha256 TEXT NOT NULL,
                        previous_event_sha256 TEXT,
                        event_json TEXT NOT NULL,
                        private_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (game_id, sequence),
                        UNIQUE (event_id),
                        FOREIGN KEY (game_id) REFERENCES games(game_id)
                    );
                    CREATE INDEX IF NOT EXISTS game_events_game_sequence
                    ON game_events (game_id, sequence);
                    """
                )

    def profiles(self) -> dict[str, object]:
        return {
            "schema_version": GAME_SCHEMA_VERSION,
            "profiles": [PROFILES[key].summary() for key in sorted(PROFILES)],
            "authority": "gametable_profile_registry",
            "note": (
                "Profiles define table flow and visible state; they do not install a full "
                "card rules engine."
            ),
        }

    def status(self) -> dict[str, object]:
        with self._lock, self._connect() as connection:
            counts: dict[str, int] = {}
            for row in connection.execute("SELECT state_json FROM games"):
                state = json.loads(str(row["state_json"]))
                status = str(state.get("status", "unknown"))
                counts[status] = counts.get(status, 0) + 1
        return {
            "schema_version": GAME_SCHEMA_VERSION,
            "enabled": True,
            "state_backend": "mcp_owned_sqlite_event_store",
            "game_count": sum(counts.values()),
            "games_by_status": counts,
            "profiles": sorted(PROFILES),
            "privacy_model": (
                "public plus seat-filtered views; bearer development seat tokens are a "
                "temporary identity binding, not multi-principal authentication"
            ),
        }

    def create_game(
        self,
        *,
        title: str,
        profile_id: str,
        seats: object,
    ) -> dict[str, object]:
        title = _require_nonblank(title, "title", max_length=_TITLE_MAX_LENGTH)
        profile = PROFILES.get(profile_id)
        if profile is None:
            raise GameTableError(f"Unknown GameTable profile: {profile_id}")
        normalized_seats = self._normalize_seats(seats, profile)
        game_id = f"game_{uuid.uuid4().hex}"
        created_at = _now()
        seat_order = [seat["seat_id"] for seat in normalized_seats]
        state: dict[str, Any] = {
            "schema_version": GAME_SCHEMA_VERSION,
            "game_id": game_id,
            "title": title,
            "profile_id": profile.profile_id,
            "deployment_id": self._deployment_id,
            "status": "lobby",
            "revision": 0,
            "created_at": created_at,
            "seat_order": seat_order,
            "seats": {},
            "cards": {},
            "turn": None,
            "shuffle_commitments": {},
            "shuffle_nonces": {},
        }
        raw_tokens: dict[str, str] = {}
        for seat in normalized_seats:
            seat_id = seat["seat_id"]
            raw_tokens[seat_id] = f"gtseat_{seat_id}_{secrets.token_urlsafe(32)}"
            state["seats"][seat_id] = {
                "seat_id": seat_id,
                "display_name": seat["display_name"],
                "life": profile.starting_life,
                "conceded": False,
                "mulligan_count": 0,
                "opening_hand_kept": False,
                "deck_loaded": False,
                "zones": {zone: [] for zone in profile.zones},
            }

        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO games (game_id, state_json, revision, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (game_id, _json(state), 0, created_at, created_at),
            )
            connection.executemany(
                "INSERT INTO seat_tokens (game_id, seat_id, token_sha256) VALUES (?, ?, ?)",
                [
                    (game_id, seat_id, _sha256(token))
                    for seat_id, token in raw_tokens.items()
                ],
            )
            event = self._append_event(
                connection,
                game_id=game_id,
                revision=0,
                kind="game_created",
                actor_seat=None,
                public={
                    "message": "A GameTable lobby was created.",
                    "seat_count": len(normalized_seats),
                    "profile_id": profile.profile_id,
                },
                private={},
            )

        return {
            "schema_version": GAME_SCHEMA_VERSION,
            "game_id": game_id,
            "title": title,
            "profile": profile.summary(),
            "revision": 0,
            "status": "lobby",
            "seat_tokens": raw_tokens,
            "token_delivery": (
                "Development bearer tokens are returned only by this creation call. Deliver each "
                "token privately to its intended seat; they are never included in public views "
                "or readable MCP audit receipts."
            ),
            "next_step": "Each seat must load a deck, then game.start deals opening hands for private evaluation.",
            "event": self._filtered_event(event, None),
        }

    def load_deck(
        self,
        *,
        game_id: str,
        seat_token: str,
        expected_revision: int,
        deck: object,
        command: object = None,
    ) -> dict[str, object]:
        deck_refs = self._card_refs(deck, "deck")
        command_refs = self._card_refs([] if command is None else command, "command")
        if not deck_refs:
            raise GameTableError("deck must contain at least one card definition reference")

        def mutate(
            state: dict[str, Any], actor_seat: str
        ) -> tuple[dict[str, object], dict[str, object]]:
            if state["status"] != "lobby":
                raise GameTableError("Decks can only be loaded while the game is in the lobby")
            seat = state["seats"][actor_seat]
            if seat["deck_loaded"]:
                raise GameTableError(
                    "This seat already loaded a deck; create a new lobby to replace it"
                )
            if command_refs and "command" not in self._profile_for_state(state).zones:
                raise GameTableError("This profile does not define a command zone")
            for index, definition_ref in enumerate(deck_refs, start=1):
                self._add_card(state, actor_seat, definition_ref, "library", index)
            for index, definition_ref in enumerate(command_refs, start=1):
                self._add_card(state, actor_seat, definition_ref, "command", index)
            seat["deck_loaded"] = True
            return (
                {
                    "message": f"{seat['display_name']} loaded a private deck.",
                    "seat_id": actor_seat,
                },
                {
                    actor_seat: {
                        "deck_card_count": len(deck_refs),
                        "command_card_count": len(command_refs),
                    }
                },
            )

        return self._mutate(
            game_id=game_id,
            seat_token=seat_token,
            expected_revision=expected_revision,
            kind="deck_loaded",
            mutation=mutate,
        )

    def start_game(
        self, *, game_id: str, seat_token: str, expected_revision: int
    ) -> dict[str, object]:
        def mutate(state: dict[str, Any], actor_seat: str) -> tuple[dict[str, object], dict[str, object]]:
            if state["status"] != "lobby":
                raise GameTableError("Game can only start from the lobby")
            if any(
                not state["seats"][seat_id]["deck_loaded"]
                for seat_id in state["seat_order"]
            ):
                raise GameTableError("Every seat must load a deck before the game starts")
            active_seats = [
                seat_id
                for seat_id in state["seat_order"]
                if not state["seats"][seat_id]["conceded"]
            ]
            if len(active_seats) < 2:
                raise GameTableError("At least two active seats are required to start a game")
            profile = self._profile_for_state(state)
            private: dict[str, object] = {}
            for seat_id in state["seat_order"]:
                library = state["seats"][seat_id]["zones"]["library"]
                secrets.SystemRandom().shuffle(library)
                nonce = secrets.token_bytes(32)
                state["shuffle_nonces"][seat_id] = nonce.hex()
                state["shuffle_commitments"][seat_id] = _sha256(
                    nonce + _json({"game_id": state["game_id"], "seat_id": seat_id, "order": library}).encode("utf-8")
                )
                hand_size = min(profile.opening_hand_size, len(library))
                drawn = self._draw_cards(state, seat_id, hand_size)
                private[seat_id] = {
                    "opening_hand": [self._private_card(state, card_id) for card_id in drawn]
                }
            active = active_seats[0]
            state["status"] = "opening_hands"
            state["turn"] = None
            return (
                {
                    "message": "Opening hands were dealt; each seat must keep or mulligan before play begins.",
                    "shuffle_commitments": dict(state["shuffle_commitments"]),
                },
                private,
            )

        return self._mutate(
            game_id=game_id,
            seat_token=seat_token,
            expected_revision=expected_revision,
            kind="game_started",
            mutation=mutate,
        )

    def mulligan(self, *, game_id: str, seat_token: str, expected_revision: int, reason: str | None = None) -> dict[str, object]:
        if reason is not None:
            _require_nonblank(reason, "reason", max_length=160)
        def mutate(state: dict[str, Any], actor: str) -> tuple[dict[str, object], dict[str, object]]:
            if state["status"] != "opening_hands": raise GameTableError("Mulligans are only permitted while opening hands are being resolved")
            seat = state["seats"][actor]; hand = list(seat["zones"]["hand"]); library = seat["zones"]["library"]
            for cid in hand: state["cards"][cid]["zone"] = "library"
            library.extend(hand); seat["zones"]["hand"] = []
            secrets.SystemRandom().shuffle(library); nonce = secrets.token_bytes(32)
            state["shuffle_nonces"][actor] = nonce.hex(); state["shuffle_commitments"][actor] = _sha256(nonce + _json({"game_id": state["game_id"], "seat_id": actor, "order": library}).encode("utf-8"))
            seat["mulligan_count"] += 1; seat["opening_hand_kept"] = False
            drawn = self._draw_cards(state, actor, min(self._profile_for_state(state).opening_hand_size, len(library)))
            return ({"message": f"{seat['display_name']} took a mulligan.", "seat_id": actor, "shuffle_commitment": state["shuffle_commitments"][actor]}, {actor: {"opening_hand": [self._private_card(state, c) for c in drawn], "mulligan_count": seat["mulligan_count"], "reason": reason}})
        return self._mutate(game_id=game_id, seat_token=seat_token, expected_revision=expected_revision, kind="opening_hand_mulliganed", mutation=mutate)

    def keep_opening_hand(self, *, game_id: str, seat_token: str, expected_revision: int) -> dict[str, object]:
        def mutate(state: dict[str, Any], actor: str) -> tuple[dict[str, object], dict[str, object]]:
            if state["status"] != "opening_hands": raise GameTableError("Opening hands are not being resolved")
            state["seats"][actor]["opening_hand_kept"] = True
            if all(state["seats"][s]["opening_hand_kept"] or state["seats"][s]["conceded"] for s in state["seat_order"]):
                active = next(s for s in state["seat_order"] if not state["seats"][s]["conceded"]); p = self._profile_for_state(state)
                state["status"] = "active"; state["turn"] = {"number": 1, "active_seat": active, "step": p.turn_steps[0], "priority_seat": active, "consecutive_passes": 0}
                automatic_public, automatic_private = self._resolve_turn_based_step(state)
                return ({"message": "All seats kept opening hands; the game is active.", "active_seat": active, "step": state["turn"]["step"], "automatic": automatic_public}, automatic_private)
            return ({"message": f"{state['seats'][actor]['display_name']} kept an opening hand.", "seat_id": actor}, {})
        return self._mutate(game_id=game_id, seat_token=seat_token, expected_revision=expected_revision, kind="opening_hand_kept", mutation=mutate)

    def view(self, *, game_id: str, seat_token: str | None = None) -> dict[str, object]:
        with self._lock, self._connect() as connection:
            state = self._load_state(connection, game_id)
            viewer_seat = self._seat_for_token(connection, game_id, seat_token)
            return self._view_for(state, viewer_seat)

    def events(
        self,
        *,
        game_id: str,
        seat_token: str | None = None,
        after_sequence: int = 0,
        limit: int = 50,
    ) -> dict[str, object]:
        if after_sequence < 0:
            raise GameTableError("after_sequence must be zero or positive")
        if limit <= 0 or limit > 200:
            raise GameTableError("limit must be between 1 and 200")
        with self._lock, self._connect() as connection:
            state = self._load_state(connection, game_id)
            viewer_seat = self._seat_for_token(connection, game_id, seat_token)
            rows = connection.execute(
                """
                SELECT sequence, event_id, event_sha256, previous_event_sha256,
                       event_json, private_json, created_at
                FROM game_events
                WHERE game_id = ? AND sequence > ?
                ORDER BY sequence ASC LIMIT ?
                """,
                (game_id, after_sequence, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        visible_rows = rows[:limit]
        events = [self._filtered_event(self._row_event(row), viewer_seat) for row in visible_rows]
        return {
            "schema_version": GAME_SCHEMA_VERSION,
            "game_id": game_id,
            "viewer": self._viewer(viewer_seat),
            "revision": state["revision"],
            "events": events,
            "page": {
                "after_sequence": after_sequence,
                "returned": len(events),
                "has_more": has_more,
                "next_after_sequence": visible_rows[-1]["sequence"] if has_more and visible_rows else None,
            },
            "privacy_note": "Only public events and private details addressed to this seat are returned.",
        }

    def act(
        self,
        *,
        game_id: str,
        seat_token: str,
        expected_revision: int,
        action: object,
        response_view: str = "public",
    ) -> dict[str, object]:
        if not isinstance(action, dict):
            raise GameTableError("action must be an object")
        action_type = _require_nonblank(action.get("type"), "action.type", max_length=64)

        def mutate(state: dict[str, Any], actor_seat: str) -> tuple[dict[str, object], dict[str, object]]:
            self._require_active_priority(state, actor_seat)
            if action_type == "draw":
                count = action.get("count", 1)
                if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 20:
                    raise GameTableError("draw count must be an integer from 1 to 20")
                drawn = self._draw_cards(state, actor_seat, count)
                self._reset_priority(state, actor_seat)
                return (
                    {"message": f"{state['seats'][actor_seat]['display_name']} drew {len(drawn)} card(s).", "count": len(drawn)},
                    {actor_seat: {"drawn_cards": [self._private_card(state, card_id) for card_id in drawn]}},
                )
            if action_type == "play":
                card_id = self._action_card_id(action)
                card = self._controlled_card(state, actor_seat, card_id, allowed_zones={"hand"})
                self._move_card(state, card, "battlefield")
                self._apply_initial_state(card, action.get("initial_state"))
                self._reset_priority(state, actor_seat)
                return (
                    {
                        "message": f"{state['seats'][actor_seat]['display_name']} played a card.",
                        "card": self._public_card(state, card_id),
                    },
                    {},
                )
            if action_type == "move":
                destination = _require_nonblank(action.get("destination"), "action.destination", max_length=64)
                profile = self._profile_for_state(state)
                if destination not in profile.zones or destination in {"hand", "library"}:
                    raise GameTableError("move destination must be a public profile zone")
                card_id = self._action_card_id(action)
                card = self._controlled_card(state, actor_seat, card_id, allowed_zones=set(profile.zones) - {"library"})
                self._move_card(state, card, destination)
                self._reset_priority(state, actor_seat)
                return (
                    {
                        "message": f"{state['seats'][actor_seat]['display_name']} moved a card to {destination}.",
                        "card": self._public_card(state, card_id),
                    },
                    {},
                )
            if action_type in {"tap", "untap"}:
                card_id = self._action_card_id(action)
                card = self._controlled_card(state, actor_seat, card_id, allowed_zones={"battlefield"})
                card["tapped"] = action_type == "tap"
                self._reset_priority(state, actor_seat)
                return (
                    {
                        "message": f"{state['seats'][actor_seat]['display_name']} {action_type}ped a card.",
                        "card": self._public_card(state, card_id),
                    },
                    {},
                )
            if action_type == "counter":
                card_id = self._action_card_id(action)
                card = self._controlled_card(state, actor_seat, card_id, allowed_zones={"battlefield"})
                counter = _require_nonblank(action.get("counter"), "action.counter", max_length=64)
                delta = action.get("delta")
                if not isinstance(delta, int) or isinstance(delta, bool) or not -1000 <= delta <= 1000 or delta == 0:
                    raise GameTableError("counter delta must be a nonzero integer from -1000 to 1000")
                next_value = int(card["counters"].get(counter, 0)) + delta
                if next_value < 0:
                    raise GameTableError("counter total cannot become negative")
                if next_value:
                    card["counters"][counter] = next_value
                else:
                    card["counters"].pop(counter, None)
                self._reset_priority(state, actor_seat)
                return (
                    {
                        "message": f"{state['seats'][actor_seat]['display_name']} adjusted a counter.",
                        "card": self._public_card(state, card_id),
                    },
                    {},
                )
            if action_type == "damage":
                card_id = self._action_card_id(action)
                card = self._controlled_card(state, actor_seat, card_id, allowed_zones={"battlefield"})
                amount = action.get("amount")
                if not isinstance(amount, int) or isinstance(amount, bool) or not 0 <= amount <= 10000:
                    raise GameTableError("damage amount must be an integer from 0 to 10000")
                card["damage"] = amount
                self._reset_priority(state, actor_seat)
                return (
                    {
                        "message": f"{state['seats'][actor_seat]['display_name']} set marked damage.",
                        "card": self._public_card(state, card_id),
                    },
                    {},
                )
            if action_type == "life":
                delta = action.get("delta")
                if not isinstance(delta, int) or isinstance(delta, bool) or not -10000 <= delta <= 10000 or delta == 0:
                    raise GameTableError("life delta must be a nonzero integer from -10000 to 10000")
                state["seats"][actor_seat]["life"] += delta
                self._reset_priority(state, actor_seat)
                return (
                    {
                        "message": f"{state['seats'][actor_seat]['display_name']} adjusted their life total.",
                        "seat_id": actor_seat,
                        "life": state["seats"][actor_seat]["life"],
                    },
                    {},
                )
            raise GameTableError(
                "Unsupported action.type. Supported actions are draw, play, move, tap, untap, counter, damage, and life."
            )

        return self._mutate(
            game_id=game_id,
            seat_token=seat_token,
            expected_revision=expected_revision,
            kind=f"action.{action_type}",
            mutation=mutate,
            response_view=response_view,
        )

    def pass_priority(
        self, *, game_id: str, seat_token: str, expected_revision: int, response_view: str = "public"
    ) -> dict[str, object]:
        def mutate(state: dict[str, Any], actor_seat: str) -> tuple[dict[str, object], dict[str, object]]:
            self._require_active_priority(state, actor_seat)
            turn = state["turn"]
            turn["consecutive_passes"] += 1
            active_seat_count = sum(
                1 for seat_id in state["seat_order"] if not state["seats"][seat_id]["conceded"]
            )
            if turn["consecutive_passes"] >= active_seat_count:
                automatic_public, automatic_private = self._advance_step(state)
                return (
                    {
                        "message": "All remaining seats passed priority; GameTable advanced the turn step.",
                        "turn": dict(state["turn"]),
                        "automatic": automatic_public,
                    },
                    automatic_private,
                )
            turn["priority_seat"] = self._next_active_seat(state, actor_seat)
            return (
                {
                    "message": "Priority passed to the next active seat.",
                    "priority_seat": turn["priority_seat"],
                    "consecutive_passes": turn["consecutive_passes"],
                },
                {},
            )

        return self._mutate(
            game_id=game_id,
            seat_token=seat_token,
            expected_revision=expected_revision,
            kind="priority_passed",
            mutation=mutate,
            response_view=response_view,
        )

    def propose_shortcut(
        self,
        *,
        game_id: str,
        seat_token: str,
        expected_revision: int,
        target: object,
        response_view: str = "public",
    ) -> dict[str, object]:
        def mutate(state: dict[str, Any], actor_seat: str) -> tuple[dict[str, object], dict[str, object]]:
            self._require_active_priority(state, actor_seat)
            if state.get("shortcut") is not None:
                raise GameTableError("A shortcut proposal is already awaiting responses")
            normalized_target = self._normalize_shortcut_target(state, target)
            state["shortcut"] = {
                "proposal_id": f"shortcut_{uuid.uuid4().hex}",
                "proposed_by": actor_seat,
                "target": normalized_target,
                "accepted_by": [actor_seat],
            }
            return (
                {
                    "message": f"{state['seats'][actor_seat]['display_name']} proposed a shortcut.",
                    "shortcut": self._public_shortcut(state["shortcut"]),
                },
                {},
            )

        return self._mutate(
            game_id=game_id,
            seat_token=seat_token,
            expected_revision=expected_revision,
            kind="shortcut_proposed",
            mutation=mutate,
            response_view=response_view,
        )

    def respond_shortcut(
        self,
        *,
        game_id: str,
        seat_token: str,
        expected_revision: int,
        proposal_id: str,
        accept: bool,
        response_view: str = "public",
    ) -> dict[str, object]:
        proposal_id = _require_nonblank(proposal_id, "proposal_id", max_length=128)
        if not isinstance(accept, bool):
            raise GameTableError("accept must be a boolean")

        def mutate(state: dict[str, Any], actor_seat: str) -> tuple[dict[str, object], dict[str, object]]:
            self._require_active_seat(state, actor_seat)
            shortcut = state.get("shortcut")
            if not isinstance(shortcut, dict) or shortcut.get("proposal_id") != proposal_id:
                raise GameTableError("Shortcut proposal is unavailable")
            accepted_by = shortcut.get("accepted_by")
            if not isinstance(accepted_by, list):
                raise GameTableError("Shortcut proposal is inconsistent")
            if actor_seat in accepted_by:
                raise GameTableError("This seat has already responded to the shortcut")
            if not accept:
                del state["shortcut"]
                return (
                    {
                        "message": f"{state['seats'][actor_seat]['display_name']} declined a shortcut.",
                        "proposal_id": proposal_id,
                        "status": "declined",
                    },
                    {},
                )
            accepted_by.append(actor_seat)
            remaining = [
                seat_id for seat_id in state["seat_order"]
                if not state["seats"][seat_id]["conceded"] and seat_id not in accepted_by
            ]
            if remaining:
                return (
                    {
                        "message": f"{state['seats'][actor_seat]['display_name']} accepted a shortcut.",
                        "shortcut": self._public_shortcut(shortcut),
                        "awaiting_seats": remaining,
                    },
                    {},
                )
            target_state = dict(shortcut["target"])
            del state["shortcut"]
            automatic_public, automatic_private, skipped = self._advance_to_shortcut_target(state, target_state)
            return (
                {
                    "message": "Every active seat accepted the shortcut; GameTable advanced the agreed turn flow.",
                    "proposal_id": proposal_id,
                    "status": "completed",
                    "target": target_state,
                    "skipped": skipped,
                    "turn": dict(state["turn"]),
                    "automatic": automatic_public,
                },
                automatic_private,
            )

        return self._mutate(
            game_id=game_id,
            seat_token=seat_token,
            expected_revision=expected_revision,
            kind="shortcut_responded",
            mutation=mutate,
            response_view=response_view,
        )

    def concede(
        self, *, game_id: str, seat_token: str, expected_revision: int
    ) -> dict[str, object]:
        def mutate(state: dict[str, Any], actor_seat: str) -> tuple[dict[str, object], dict[str, object]]:
            if state["status"] not in {"lobby", "active"}:
                raise GameTableError("Only an open game can be conceded")
            state["seats"][actor_seat]["conceded"] = True
            remaining = [
                seat_id for seat_id in state["seat_order"] if not state["seats"][seat_id]["conceded"]
            ]
            if len(remaining) <= 1:
                state["status"] = "finished"
                state["turn"] = None
            elif state["turn"] and state["turn"]["active_seat"] == actor_seat:
                state["turn"]["active_seat"] = self._next_active_seat(state, actor_seat)
                state["turn"]["priority_seat"] = state["turn"]["active_seat"]
                state["turn"]["consecutive_passes"] = 0
            return (
                {
                    "message": f"{state['seats'][actor_seat]['display_name']} conceded.",
                    "remaining_seats": remaining,
                    "status": state["status"],
                    "winner_seat": remaining[0] if state["status"] == "finished" and remaining else None,
                },
                {},
            )

        return self._mutate(
            game_id=game_id,
            seat_token=seat_token,
            expected_revision=expected_revision,
            kind="seat_conceded",
            mutation=mutate,
        )

    def _normalize_seats(self, seats: object, profile: GameProfile) -> list[dict[str, Any]]:
        if not isinstance(seats, list):
            raise GameTableError("seats must be a list of seat objects")
        if not profile.player_min <= len(seats) <= profile.player_max:
            raise GameTableError(
                f"{profile.profile_id} requires between {profile.player_min} and {profile.player_max} seats"
            )
        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        for raw in seats:
            if not isinstance(raw, dict):
                raise GameTableError("Each seat must be an object")
            seat_id = _require_nonblank(raw.get("seat_id"), "seat_id", max_length=32).lower()
            if not _SEAT_ID_RE.fullmatch(seat_id):
                raise GameTableError("seat_id must match [a-z][a-z0-9_-]{0,31}")
            if seat_id in seen:
                raise GameTableError("seat_id values must be unique")
            seen.add(seat_id)
            display_name = _require_nonblank(
                raw.get("display_name", seat_id), "display_name", max_length=80
            )
            result.append(
                {
                    "seat_id": seat_id,
                    "display_name": display_name,
                }
            )
        return result

    @staticmethod
    def _card_refs(value: object, label: str) -> list[str]:
        if not isinstance(value, list):
            raise GameTableError(f"{label} must be a list of card definition references")
        if len(value) > 500:
            raise GameTableError(f"{label} may contain at most 500 cards")
        return [_require_nonblank(item, f"{label} entry", max_length=_CARD_REF_MAX_LENGTH) for item in value]

    @staticmethod
    def _add_card(
        state: dict[str, Any],
        seat_id: str,
        definition_ref: str,
        zone: str,
        sequence: int,
    ) -> None:
        card_id = f"card_{seat_id}_{zone}_{sequence}_{uuid.uuid4().hex[:12]}"
        state["cards"][card_id] = {
            "instance_id": card_id,
            "definition_ref": definition_ref,
            "owner_seat": seat_id,
            "controller_seat": seat_id,
            "zone": zone,
            "tapped": False,
            "counters": {},
            "damage": 0,
            "status_tags": [],
        }
        state["seats"][seat_id]["zones"][zone].append(card_id)

    def _mutate(
        self,
        *,
        game_id: str,
        seat_token: str,
        expected_revision: int,
        kind: str,
        mutation: Any,
        response_view: str = "public",
    ) -> dict[str, object]:
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 0:
            raise GameTableError("expected_revision must be a zero-or-positive integer")
        if response_view not in {"public", "seat"}:
            raise GameTableError("response_view must be 'public' or 'seat'")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._load_state(connection, game_id)
            actor_seat = self._seat_for_token(connection, game_id, seat_token)
            assert actor_seat is not None
            if state["revision"] != expected_revision:
                raise GameConflictError(
                    f"Game revision changed: expected {expected_revision}, current {state['revision']}"
                )
            public, private = mutation(state, actor_seat)
            state["revision"] += 1
            updated_at = _now()
            event = self._append_event(
                connection,
                game_id=game_id,
                revision=state["revision"],
                kind=kind,
                actor_seat=actor_seat,
                public=public,
                private=private,
            )
            connection.execute(
                "UPDATE games SET state_json = ?, revision = ?, updated_at = ? WHERE game_id = ?",
                (_json(state), state["revision"], updated_at, game_id),
            )
        return {
            "schema_version": GAME_SCHEMA_VERSION,
            "game_id": game_id,
            "revision": state["revision"],
            "event": self._filtered_event(event, actor_seat),
            "view": self._view_for(state, actor_seat if response_view == "seat" else None),
        }

    @staticmethod
    def _load_state(connection: sqlite3.Connection, game_id: str) -> dict[str, Any]:
        row = connection.execute("SELECT state_json FROM games WHERE game_id = ?", (game_id,)).fetchone()
        if row is None:
            raise GameTableError("GameTable game not found")
        try:
            state = json.loads(str(row["state_json"]))
        except json.JSONDecodeError as exc:
            raise GameTableError("GameTable state is unreadable") from exc
        if not isinstance(state, dict) or state.get("schema_version") != GAME_SCHEMA_VERSION:
            raise GameTableError("GameTable state schema is unsupported")
        return state

    @staticmethod
    def _seat_for_token(
        connection: sqlite3.Connection, game_id: str, seat_token: str | None
    ) -> str | None:
        if seat_token is None:
            return None
        if not isinstance(seat_token, str) or not seat_token.strip():
            raise GameTableError("Game seat token is invalid")
        token_hash = _sha256(seat_token)
        row = connection.execute(
            "SELECT seat_id FROM seat_tokens WHERE game_id = ? AND token_sha256 = ?",
            (game_id, token_hash),
        ).fetchone()
        if row is None:
            raise GameTableError("Game seat token is invalid")
        return str(row["seat_id"])

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        game_id: str,
        revision: int,
        kind: str,
        actor_seat: str | None,
        public: dict[str, object],
        private: dict[str, object],
    ) -> dict[str, object]:
        previous = connection.execute(
            "SELECT sequence, event_sha256 FROM game_events WHERE game_id = ? ORDER BY sequence DESC LIMIT 1",
            (game_id,),
        ).fetchone()
        sequence = int(previous["sequence"]) + 1 if previous is not None else 1
        previous_hash = str(previous["event_sha256"]) if previous is not None else None
        event = {
            "schema_version": GAME_SCHEMA_VERSION,
            "event_id": f"gameevt_{uuid.uuid4().hex}",
            "sequence": sequence,
            "revision": revision,
            "kind": kind,
            "actor_seat": actor_seat,
            "public": public,
            "created_at": _now(),
        }
        event_hash = _sha256(_json({"previous": previous_hash, "event": event}))
        connection.execute(
            """
            INSERT INTO game_events (
                game_id, sequence, event_id, event_sha256, previous_event_sha256,
                event_json, private_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game_id,
                sequence,
                event["event_id"],
                event_hash,
                previous_hash,
                _json(event),
                _json(private),
                event["created_at"],
            ),
        )
        return {**event, "event_sha256": event_hash, "previous_event_sha256": previous_hash, "private": private}

    @staticmethod
    def _row_event(row: sqlite3.Row) -> dict[str, object]:
        event = json.loads(str(row["event_json"]))
        private = json.loads(str(row["private_json"]))
        return {
            **event,
            "event_sha256": str(row["event_sha256"]),
            "previous_event_sha256": row["previous_event_sha256"],
            "private": private,
        }

    @staticmethod
    def _filtered_event(event: dict[str, object], viewer_seat: str | None) -> dict[str, object]:
        visible = {
            key: value
            for key, value in event.items()
            if key not in {"private"}
        }
        private = event.get("private")
        if viewer_seat and isinstance(private, dict) and viewer_seat in private:
            visible["private"] = private[viewer_seat]
        return visible

    @staticmethod
    def _profile_for_state(state: dict[str, Any]) -> GameProfile:
        profile = PROFILES.get(state.get("profile_id"))
        if profile is None:
            raise GameTableError("Game profile is unavailable")
        return profile

    @staticmethod
    def _viewer(viewer_seat: str | None) -> dict[str, object]:
        return {"kind": "seat", "seat_id": viewer_seat} if viewer_seat else {"kind": "public"}

    def _view_for(self, state: dict[str, Any], viewer_seat: str | None) -> dict[str, object]:
        profile = self._profile_for_state(state)
        public_seats: list[dict[str, object]] = []
        for seat_id in state["seat_order"]:
            seat = state["seats"][seat_id]
            zones = seat["zones"]
            public_seats.append(
                {
                    "seat_id": seat_id,
                    "display_name": seat["display_name"],
                    "life": seat["life"],
                    "conceded": seat["conceded"],
                    "mulligan_count": seat.get("mulligan_count", 0),
                    "zone_counts": {zone: len(cards) for zone, cards in zones.items()},
                    "battlefield": [self._public_card(state, card_id) for card_id in zones.get("battlefield", [])],
                    "graveyard": [self._public_card(state, card_id) for card_id in zones.get("graveyard", [])],
                    "exile": [self._public_card(state, card_id) for card_id in zones.get("exile", [])],
                    "command": [self._public_card(state, card_id) for card_id in zones.get("command", [])],
                }
            )
        result: dict[str, object] = {
            "schema_version": GAME_SCHEMA_VERSION,
            "game": {
                "game_id": state["game_id"],
                "title": state["title"],
                "profile_id": profile.profile_id,
                "status": state["status"],
                "revision": state["revision"],
            },
            "viewer": self._viewer(viewer_seat),
            "turn": state["turn"],
            "seats": public_seats,
            "rules_enforcement": profile.rules_enforcement,
            "privacy": {
                "library_order": "never returned through player or public views",
                "opponent_hand": "count only",
                "server_operator": "outside this view boundary; can inspect local game state",
            },
        }
        shortcut = state.get("shortcut")
        if isinstance(shortcut, dict):
            result["shortcut"] = self._public_shortcut(shortcut)
        if viewer_seat:
            own = state["seats"][viewer_seat]
            result["private"] = {
                "seat_id": viewer_seat,
                "hand": [self._private_card(state, card_id) for card_id in own["zones"]["hand"]],
                "library": {"count": len(own["zones"]["library"])},
            }
        return result

    @staticmethod
    def _private_card(state: dict[str, Any], card_id: str) -> dict[str, object]:
        card = state["cards"][card_id]
        return {
            "instance_id": card["instance_id"],
            "definition_ref": card["definition_ref"],
            "owner_seat": card["owner_seat"],
            "controller_seat": card["controller_seat"],
            "zone": card["zone"],
            "tapped": card["tapped"],
            "counters": dict(card["counters"]),
            "damage": card["damage"],
            "status_tags": list(card["status_tags"]),
        }

    @staticmethod
    def _public_card(state: dict[str, Any], card_id: str) -> dict[str, object]:
        card = state["cards"][card_id]
        if card["zone"] in {"hand", "library"}:
            raise GameTableError("Private card cannot be projected publicly")
        return {
            "instance_id": card["instance_id"],
            "definition_ref": card["definition_ref"],
            "owner_seat": card["owner_seat"],
            "controller_seat": card["controller_seat"],
            "zone": card["zone"],
            "tapped": card["tapped"],
            "counters": dict(card["counters"]),
            "damage": card["damage"],
            "status_tags": list(card["status_tags"]),
        }

    @staticmethod
    def _draw_cards(state: dict[str, Any], seat_id: str, count: int) -> list[str]:
        library = state["seats"][seat_id]["zones"]["library"]
        hand = state["seats"][seat_id]["zones"]["hand"]
        drawn: list[str] = []
        for _ in range(min(count, len(library))):
            card_id = library.pop(0)
            hand.append(card_id)
            state["cards"][card_id]["zone"] = "hand"
            drawn.append(card_id)
        return drawn

    @staticmethod
    def _action_card_id(action: dict[str, Any]) -> str:
        return _require_nonblank(action.get("card_id"), "action.card_id", max_length=128)

    @staticmethod
    def _controlled_card(
        state: dict[str, Any], actor_seat: str, card_id: str, *, allowed_zones: set[str]
    ) -> dict[str, Any]:
        card = state["cards"].get(card_id)
        if not isinstance(card, dict) or card.get("controller_seat") != actor_seat or card.get("zone") not in allowed_zones:
            raise GameTableError("Card action is not permitted")
        return card

    @staticmethod
    def _move_card(state: dict[str, Any], card: dict[str, Any], destination: str) -> None:
        previous_zone = card["zone"]
        owner = card["owner_seat"]
        previous = state["seats"][owner]["zones"].get(previous_zone)
        if not isinstance(previous, list) or card["instance_id"] not in previous:
            raise GameTableError("Game card zone state is inconsistent")
        previous.remove(card["instance_id"])
        state["seats"][owner]["zones"][destination].append(card["instance_id"])
        card["zone"] = destination
        if destination != "battlefield":
            card["tapped"] = False
            card["damage"] = 0

    @staticmethod
    def _apply_initial_state(card: dict[str, Any], value: object) -> None:
        if value is None:
            return
        if not isinstance(value, dict):
            raise GameTableError("action.initial_state must be an object")
        unknown = set(value) - {"tapped", "counters", "damage", "status_tags"}
        if unknown:
            raise GameTableError("action.initial_state contains unsupported fields")
        if "tapped" in value:
            if not isinstance(value["tapped"], bool):
                raise GameTableError("action.initial_state.tapped must be a boolean")
            card["tapped"] = value["tapped"]
        if "damage" in value:
            damage = value["damage"]
            if not isinstance(damage, int) or isinstance(damage, bool) or not 0 <= damage <= 10000:
                raise GameTableError("action.initial_state.damage must be an integer from 0 to 10000")
            card["damage"] = damage
        if "counters" in value:
            counters = value["counters"]
            if not isinstance(counters, dict) or any(
                not isinstance(name, str) or not name.strip() or len(name.strip()) > 64
                or not isinstance(count, int) or isinstance(count, bool) or count < 0 or count > 1000
                for name, count in counters.items()
            ):
                raise GameTableError("action.initial_state.counters must map nonblank names to integers from 0 to 1000")
            card["counters"] = {name.strip(): count for name, count in counters.items() if count}
        if "status_tags" in value:
            tags = value["status_tags"]
            if not isinstance(tags, list) or len(tags) > 32 or any(
                not isinstance(tag, str) or not tag.strip() or len(tag.strip()) > 64 for tag in tags
            ):
                raise GameTableError("action.initial_state.status_tags must be a list of up to 32 nonblank strings")
            card["status_tags"] = list(dict.fromkeys(tag.strip() for tag in tags))

    @staticmethod
    def _require_active_priority(state: dict[str, Any], actor_seat: str) -> None:
        if state.get("status") != "active" or not isinstance(state.get("turn"), dict):
            raise GameTableError("Game is not active")
        if state["seats"][actor_seat]["conceded"]:
            raise GameTableError("Conceded seats cannot act")
        if state.get("shortcut") is not None:
            raise GameTableError("A shortcut proposal is awaiting responses")
        if state["turn"]["priority_seat"] != actor_seat:
            raise GameTableError("It is not this seat's priority")

    @staticmethod
    def _require_active_seat(state: dict[str, Any], actor_seat: str) -> None:
        if state.get("status") != "active" or not isinstance(state.get("turn"), dict):
            raise GameTableError("Game is not active")
        if state["seats"][actor_seat]["conceded"]:
            raise GameTableError("Conceded seats cannot act")

    @staticmethod
    def _reset_priority(state: dict[str, Any], actor_seat: str) -> None:
        state["turn"]["priority_seat"] = actor_seat
        state["turn"]["consecutive_passes"] = 0

    @staticmethod
    def _next_active_seat(state: dict[str, Any], current: str) -> str:
        seats = state["seat_order"]
        try:
            start = seats.index(current)
        except ValueError as exc:
            raise GameTableError("Game turn state is inconsistent") from exc
        for offset in range(1, len(seats) + 1):
            candidate = seats[(start + offset) % len(seats)]
            if not state["seats"][candidate]["conceded"]:
                return candidate
        raise GameTableError("No active seats remain")

    def _advance_step(self, state: dict[str, Any]) -> tuple[dict[str, object], dict[str, object]]:
        profile = self._profile_for_state(state)
        turn = state["turn"]
        step_index = profile.turn_steps.index(turn["step"])
        if step_index == len(profile.turn_steps) - 1:
            turn["number"] += 1
            turn["active_seat"] = self._next_active_seat(state, turn["active_seat"])
            turn["step"] = profile.turn_steps[0]
        else:
            turn["step"] = profile.turn_steps[step_index + 1]
        return self._resolve_turn_based_step(state)

    def _resolve_turn_based_step(self, state: dict[str, Any]) -> tuple[dict[str, object], dict[str, object]]:
        """Run profile-owned turn actions before creating the next priority window."""
        turn = state["turn"]
        profile = self._profile_for_state(state)
        automatic: list[dict[str, object]] = []
        private: dict[str, object] = {}
        if profile.profile_id == "magic.commander.v0.1" and turn["step"] == "untap":
            active = turn["active_seat"]
            untapped = 0
            for card_id in state["seats"][active]["zones"].get("battlefield", []):
                card = state["cards"][card_id]
                if card["tapped"] and "skip_untap" not in card.get("status_tags", []):
                    card["tapped"] = False
                    untapped += 1
            automatic.append({"type": "untap", "seat_id": active, "untapped_count": untapped})
            turn["step"] = profile.turn_steps[profile.turn_steps.index("untap") + 1]
        if profile.profile_id == "magic.commander.v0.1" and turn["step"] == "draw":
            active = turn["active_seat"]
            drawn = self._draw_cards(state, active, 1)
            automatic.append({"type": "draw", "seat_id": active, "count": len(drawn)})
            private[active] = {"drawn_cards": [self._private_card(state, card_id) for card_id in drawn]}
        turn["priority_seat"] = turn["active_seat"]
        turn["consecutive_passes"] = 0
        return {"actions": automatic}, private

    def _normalize_shortcut_target(self, state: dict[str, Any], value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            raise GameTableError("shortcut target must be an object")
        if set(value) != {"turn_number", "step"}:
            raise GameTableError("shortcut target must contain exactly turn_number and step")
        turn_number = value.get("turn_number")
        if not isinstance(turn_number, int) or isinstance(turn_number, bool):
            raise GameTableError("shortcut target.turn_number must be an integer")
        step = _require_nonblank(value.get("step"), "shortcut target.step", max_length=64)
        turn = state["turn"]
        profile = self._profile_for_state(state)
        if step not in profile.turn_steps:
            raise GameTableError("shortcut target.step is not part of this game profile")
        if profile.profile_id == "magic.commander.v0.1" and step == "untap":
            raise GameTableError("Magic shortcut targets cannot be untap because no priority window exists there")
        current_number = turn["number"]
        current_index = profile.turn_steps.index(turn["step"])
        target_index = profile.turn_steps.index(step)
        if turn_number == current_number and target_index <= current_index:
            raise GameTableError("shortcut target must be later than the current turn step")
        if turn_number not in {current_number, current_number + 1}:
            raise GameTableError("shortcut targets may be in the current or next turn only")
        return {"turn_number": turn_number, "step": step}

    @staticmethod
    def _public_shortcut(shortcut: dict[str, Any]) -> dict[str, object]:
        return {
            "proposal_id": shortcut["proposal_id"],
            "proposed_by": shortcut["proposed_by"],
            "target": dict(shortcut["target"]),
            "accepted_by": list(shortcut["accepted_by"]),
        }

    def _advance_to_shortcut_target(
        self, state: dict[str, Any], target: dict[str, object]
    ) -> tuple[dict[str, object], dict[str, object], list[dict[str, object]]]:
        automatic: list[dict[str, object]] = []
        private: dict[str, object] = {}
        skipped: list[dict[str, object]] = []
        while True:
            public, private_delta = self._advance_step(state)
            actions = public.get("actions")
            if isinstance(actions, list):
                automatic.extend(actions)
            private.update(private_delta)
            skipped.append({"turn_number": state["turn"]["number"], "step": state["turn"]["step"]})
            if state["turn"]["number"] == target["turn_number"] and state["turn"]["step"] == target["step"]:
                return {"actions": automatic}, private, skipped
