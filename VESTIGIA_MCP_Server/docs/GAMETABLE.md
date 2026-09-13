# GameTable module

GameTable is VESTIGIA MCP's first reference module for shared, hidden-information tabletop
state. It proves a narrow but important shape: the broker can host a bounded local application
without treating Archive files, Runtime continuity, or the MCP host as the game's source of truth.

## Boundary

```text
seat / MCP host -> VESTIGIA MCP policy -> GameTable store -> filtered table view
                                                |
                                                +-> event chain + local SQLite state
```

The GameTable store owns game sessions. MCP owns tool registration, policy, and coarse audit
receipts. Neither a tool description nor a GameTable profile grants filesystem, Runtime, Archive,
or external-platform authority.

The default database lives under `VESTIGIA_MCP_STATE_DIR/gametable/`, outside Archive roots.
GameTable produces no canonical continuity material automatically. A later export adapter may
create a deliberately selected replay or recap proposal through the ordinary Archive staging lane.

## Enablement

The module is deliberately off by default:

```text
VESTIGIA_MCP_GAMETABLE_ENABLED=1
```

`VESTIGIA_MCP_GAMETABLE_STATE_DIR` can override its SQLite location. This feature flag is a small
reference contract for future installable modules: disabled modules register no tools and do not
appear in executable policy.

## Profiles and current scope

`magic.commander.v0.1` supplies:

- 2–4 seats;
- 40 starting life;
- a seven-card opening hand;
- library, hand, battlefield, graveyard, exile, and command zones;
- Magic-shaped priority steps from untap through cleanup.

`generic.card-table.v0.1` supplies a neutral 2–8 seat table with a smaller turn flow.

Profiles define state shape and table flow. They do not bundle copyrighted card data or implement
deck legality, card text, replacement effects, targeting, triggers, combat legality, stack rules,
or the Magic comprehensive rules. A card is an instance with a caller-supplied `definition_ref`,
owner/controller, zone, tapped state, counters, marked damage, and status tags. Players retain
rules adjudication.

## Privacy model

Creating a game returns one opaque development seat token per seat. The caller must deliver each
one privately; each recipient loads only their own deck with `game.load_deck` before the game can
start. The server stores only SHA-256 token verifiers; ordinary MCP audit events retain only an
arguments digest.

- No token: public state, public events, hand/library counts, public zones.
- Seat token: public state plus *that seat's* hand and private event details.
- No ordinary view exposes an opponent's hand or any library order.
- Server-side shuffles use OS-backed randomness and publish a per-seat commitment, not deck order;
  the store retains the corresponding nonce for a future explicit replay/reveal feature.

This is an application-level view boundary, not remote multi-principal authentication. A person or
process with direct access to the local GameTable database is within the operator trust boundary.
The eventual Keyring/principal resolver should replace bearer-token validation with a caller-to-seat
binding; the event reducer and filtered projection do not need to change.

## State and action rules

Every mutation requires the exact `expected_revision`. A stale caller is rejected rather than
silently overwriting a newer table state. Events are append-only-ish records with a public hash
chain; private event payloads are stored separately and filtered at read time.

The first `game.act` supports bounded table bookkeeping only:

- `draw`
- `play`
- `move` to a public profile zone
- `tap` / `untap`
- `counter`
- `damage`
- `life` (the acting seat's total)

Only the current priority seat may act or pass. After an action, that seat retains priority;
when every non-conceded seat passes, GameTable advances the configured step and resets priority to
the active seat. `game.concede` preserves the event record rather than deleting state.

## Next increments

1. Replace development tokens with Keyring-backed caller/seat principals.
2. Add consensual undo proposals and votes as compensating events.
3. Add deck commitment/import adapters and a separately licensed card-data integration.
4. Add game-specific profile packages (including Pokémon) without changing the generic reducer.
5. Add an explicit replay export/stage path and optional visual tabletop client.
