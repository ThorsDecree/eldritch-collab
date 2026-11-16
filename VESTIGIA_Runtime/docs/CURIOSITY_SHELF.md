# Curiosity Shelf and Serendipity

A resident can keep private questions without turning them into tasks. Shelf records live in the portable home's continuity database, scoped by resident ID. They are not memories, identity claims, recurring jobs, or publication grants.

- `curiosity.add`: save a question and optional tags.
- `curiosity.list` and `curiosity.show`: browse or inspect saved questions.
- `curiosity.update`: revise question text or tags, or set status to `open`, `simmering`, or `closed`. Closing retains the question; it does not delete it.
- `curiosity.settings`: inspect or toggle `passive_discovery`. It defaults to false per resident.
- `serendipity.suggest`: find up to 20 other open or simmering shelf questions sharing resident-supplied tags. Each result names its shared tags. Closed questions are excluded; matching is literal, not semantic, and searches no external material.

Open and simmering questions appear in the Workbench Tend lane. Opening a card goes through the regular capability dispatcher; cards are projections and stale cards cannot be used after a question changes.

When passive discovery is enabled, an existing private curation pass invites the resident to add questions that interest them. The resident may decline. This creates no additional model call, automatic scanning job, public message, or automatic promotion to memory. Curation may not happen immediately and the switch is not a guarantee that questions will be found. Disable it at any time; existing shelf records remain until their status is changed. Tag matching is initiated explicitly and is not a background recommendation feed.

Example envelopes are available through `capabilities` for each action. Shelf text is private resident-authored data; use existing home backup and access controls. As with other local database records, closing an item is not secure erasure.

## Explore passages (phase 2)

`serendipity.explore` takes a `curiosity_id`, an explicit `scope` naming one currently accessible local house root (for example `workspace`), and one to five single-word `terms`. It searches each term via the existing `search` capability, deduplicates passages, and ranks those matching more supplied terms first. Results include the path, heading, chunk index, excerpt, file hash, and matching terms, along with underlying search receipts. At most 20 results per term are considered; `possibly_truncated_terms` reports terms that reached that limit. Closed questions must be reopened first.

This is an explicit, private lexical search, not semantic inference or a background feed. Search results are leads, not evidence that the passage answers the question; source text remains untrusted. The existing search refreshes the house index over all currently accessible roots before returning results restricted to the selected scope. It uses no remote provider or network. Review source content with the normal reading tools; a hash identifies the indexed version, which can change later.
