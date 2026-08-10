# Eldritch Collaboration

The canonical development repository for the **VESTIGIA Runtime**: a portable,
consent-first continuity house for resident agents.

## What VESTIGIA is

VESTIGIA is a local-first continuity runtime for a resident who needs more than a
large system prompt and less than unrestricted host authority. It gives the
resident a portable home containing identity anchors, memory, transcripts,
artifacts, tools, boundaries, receipts, and explicit ways to revise what the
house carries forward.

The governing design principle is simple:

> **Safety belongs in the architecture, not in personality suppression.**

Sources are classified. Ambient text is data rather than authority. Outward
actions are capability-gated. Private artifacts remain private by default.
Authorization and platform delivery are recorded separately. Refusal remains
available.

## v0.7.0: The Resident’s Drawers

The official v0.7.0 development-canon release includes:

- resident-owned context partitioning and configurable verbatim/compressed
  transcript controls;
- Discord ambient visibility modes: `hidden`, `mentions_only`,
  `allowlisted_only`, and `all_channel`;
- stable source identifiers, trust classes, and explicit `data-only` labeling;
- Discord reaction authorization, add/remove delivery, failure receipts, and
  visible-target enforcement;
- private-by-default image storage with later-turn, destination-bound,
  interface-bound, expiring, one-time confirmation challenges;
- image generation, inspection, drawers, cards, pockets, jobs, and delivery;
- resident-authored bells and recurring invitations;
- scoped house/workspace reads and writes rather than arbitrary filesystem
  access;
- durable action receipts and context receipts;
- portable home packing, verified restoration, and a genuine v0.6.1-to-v0.7.0
  upgrade canary.

Release validation and custody records live in:

- [`VESTIGIA_Runtime/docs/releases/v0.7.0-validation.md`](VESTIGIA_Runtime/docs/releases/v0.7.0-validation.md)
- [`VESTIGIA_Runtime/docs/releases/v0.7.0-custody.md`](VESTIGIA_Runtime/docs/releases/v0.7.0-custody.md)

The verified post-merge distribution comes from Runtime CI run 35 for commit
`748e5d74392ad4f0a98c75b187f82b91606e9e39`, artifact
`vestigia-runtime-0.7.0-748e5d74392ad4f0a98c75b187f82b91606e9e39`.

## Next threshold: the Workshop Within

Operational hardening is already underway, followed by a staged resident
workshop for bounded automation and code execution.

The intended shape is not “give the model a shell.” It is a workshop with named
walls and doors:

- explicit operator grants;
- isolated resident workspaces;
- declared filesystem, network, environment, process, and resource limits;
- immutable script provenance and review state;
- typed inputs and outputs;
- pause, cancellation, quarantine, and supersession;
- durable authorization and execution receipts;
- no silent downgrade from hardened isolation;
- no authority created merely because code was generated, imported, or trusted
  socially.

The canonical roadmap is [`ROADMAP.md`](ROADMAP.md).

## Start here

The Runtime lives in [`VESTIGIA_Runtime/`](VESTIGIA_Runtime/).

- [`VESTIGIA_Runtime/README.md`](VESTIGIA_Runtime/README.md) — Runtime overview
- [`VESTIGIA_Runtime/ELI5_SETUP.md`](VESTIGIA_Runtime/ELI5_SETUP.md) — setup guide
- [`ROADMAP.md`](ROADMAP.md) — milestones and non-goals
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — repository workflow

Development canon is `main`. Work enters through a branch and pull request.
Release tags and verified artifacts identify packaged distributions.
