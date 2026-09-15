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

Mutation authorization and response projection are deliberately separate. A successful mutation
returns a **public** table view by default, plus only the actor's explicitly addressed private
event delta (for example, the cards that actor just drew). Callers can request
`response_view="seat"` when they actually need their own full hand projection. Possessing a seat
token therefore never makes it appropriate for a public/referee thread to receive that seat's
whole hand as an incidental mutation result.

The first `game.act` supports bounded table bookkeeping only:

- `draw`
- `play`
- `move` to a public profile zone
- `tap` / `untap`
- `counter`
- `damage`
- `life` (the acting seat's total)
- `tap_bundle` (one atomic, caller-adjudicated payment/tap record)

Only the current priority seat may act or pass. After an action, that seat retains priority;
when every non-conceded seat passes, GameTable advances the configured step and resets priority to
the active seat. The Commander profile has no priority window during untap: it performs its
generic tagged untap, enters upkeep, and later draws one private card automatically on entering
the draw step. `skip_untap` is an explicit table-state tag; card-specific exceptions remain
player adjudication.

`game.shortcut_propose` and `game.shortcut_respond` make routine speed explicit rather than
heuristic. The priority holder proposes one exact target step in the current or next turn. Every
non-conceded seat accepts or any seat declines; only unanimous acceptance advances there in a
single compact event, including any generic automatic untap/draw actions. While a proposal is
awaiting responses, ordinary priority actions pause. This records consent without claiming that an
empty board proves nobody has a response.

`play` accepts an optional `initial_state` with `tapped`, `counters`, `damage`, and `status_tags`.
It records the state the table agrees a permanent entered with, without pretending to evaluate
Oracle text or replacement effects.

### Standing yields

`game.yield` is the explicit fast path for routine empty windows. A seat records one bounded
scope: `{"kind":"step"}` yields to the next priority-bearing step, `{"kind":"turn"}` yields
to the next turn's first priority-bearing step, and `{"kind":"target","turn_number":N,"step":"..."}`
chooses an exact current/next-turn endpoint. Each active seat must record a yield. The reducer
advances once, in one compact event, to the earliest endpoint requested by any seat; a longer
yield can never carry a more cautious seat past its consent. Automatic profile work (including a
turn draw) is included in the event while addressed private cards remain private. Any normal
action, state repair/effect operation, shortcut, concession, or state-changing pass clears pending
yields so they cannot become stale consent. Pending effects remain a hard blocker, and no heuristic
absence-of-response inference is performed.

## Pending effects, hidden zones, and repair

`game.effect_declare` gives a spell, activated ability, triggered ability, or manual effect a
first-class pending record: opaque label/targets, controller, optional source, originating
revision, and lifecycle state. It does not parse or validate card text. Once every active seat
passes, GameTable **does not advance the turn**: the top pending effect becomes `resolving` and
its controller receives priority to record its adjudicated outcome.

`game.effect_resolve` accepts an atomic bounded list of state operations. It can be used with
`complete=false` for an intermediate batch—such as shuffle then reveal—while the effect remains
resolving. A later completing call closes it as `resolved`, `countered`, or `fizzled` and returns
priority to the active seat. Supported operations are:

- `move`, including effect-driven movement into an owner’s private hand or library;
- `shuffle`, using server RNG and publishing a fresh commitment rather than library order;
- `reveal_top`, publicly or only to the owning seat, without moving the cards;
- `random_int`, publicly or privately, with a bounded server-generated receipt.

Private-zone moves deliberately return only a minimal public statement; the destination card is
delivered only in the receiving seat’s private event details. A public reveal makes exactly the
revealed card(s) public, never neighbouring library order. These are table-state primitives, not
an assertion that GameTable knows why an effect made them legal.

`game.repair_state` is the separate, visibly labeled recovery lane for an already human-adjudicated
state mismatch. It is revision-bound, requires a reason, cannot run while an effect is pending,
and can only repair cards/libraries owned or controlled by the token holder. It supports the same
safe moves/shuffle plus `set_state` for a controlled battlefield card. This is an explicit local
referee trust boundary, not a hidden direct-write bypass. Any zone change clears generic transient
state (tapped, counters, damage, and status tags), so counters cannot become ghost state after a
card changes zones.

## Next increments

1. Add narrowly scoped shortcut presets and a seat-local “decision needed?” routing predicate so a
   shared table never needs to receive a private hand merely to decide whether to route a player to
   a private chair.
2. Add London bottom-card selection and a profile-declared mulligan-cost policy.
3. Add consensual undo proposals and votes as compensating events.
4. Replace development tokens with Keyring-backed caller/seat principals.
5. Add deck commitment/import adapters and a separately licensed card-data integration.
6. Add game-specific profile packages (including Pokémon) without changing the generic reducer.
7. Add an explicit replay/export checkpoint path and optional visual tabletop client.
8. Add table telemetry receipts for latency/consent debugging without exposing hidden zones.
