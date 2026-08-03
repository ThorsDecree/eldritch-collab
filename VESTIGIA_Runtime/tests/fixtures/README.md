# Historical home fixture matrix

`historical_homes.json` describes small synthetic schema-era homes used by the resilience
suite. The tests create these homes at runtime, seed fixed memory/turn/state evidence, remove
only the tables that had not yet existed in the named era, and then start the current Runtime.

These are intentionally not copies of real resident homes. They contain no personal archive,
identity prose, private conversation, credentials, or image bytes.

The matrix verifies that additive initialization:

- recreates later tables;
- preserves seeded record IDs, content hashes, authority/status, turns, and runtime state;
- restores the current contract plaques;
- can be run repeatedly without duplicating evidence.
