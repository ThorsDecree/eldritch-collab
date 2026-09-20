# Porchlight Loopback Bridge MVP

## Status

Approved design for the first locally testable Chrome integration.

## Intent

Give a resident an explicit, readable-only way to send the current browser selection, page text, or an update of a previously captured page into the local VESTIGIA Archive. The Chrome surface talks to an authenticated loopback HTTP bridge; it does not speak MCP stdio directly.

## Goals

- MV3 Chrome extension with explicit selection, page, and update actions.
- Python bridge bound to loopback only, paired with a bearer token and an allowlisted extension origin.
- UTF-8 readable text plus URL/title/capture metadata; no raw HTML, scripts, styles, cookies, headers, or credentials.
- Optional visible-viewport PNG screenshot, disabled by default.
- New captures under `Modules/Porchlight/{latest,history,receipts,images}`.
- Latest is searchable; immutable history is addressable; unchanged updates are first-class; stale update bases conflict rather than overwrite newer content.
- Direct resident action is the consent gate. No second promotion click, no automatic memory promotion, and no background monitoring.
- Atomic text/receipt/optional-image bundles with hashes, write-prefix enforcement, byte limits, audit metadata, and rollback.

## Compatibility and migration

Existing staged `archive.stage_porchlight` behavior remains available for scripted/import workflows. Legacy root-level `Porchlight/` artifacts remain readable/addressable; new direct captures use `Modules/Porchlight`.

## Capture actions

- `selection`: non-empty selected text and page metadata.
- `page`: `document.body.innerText` and page metadata.
- `update`: same readable content model, compared against the current latest body using an expected-base hash.

The extension rejects empty or over-limit content before sending. Screenshot bytes are stored separately and excluded from semantic text/receipt content except for path/hash/ dimensions metadata.

## HTTP API

- `GET /health`: local liveness and protocol version.
- `POST /v1/pair/verify`: verifies the supplied pairing token and returns bridge configuration metadata.
- `POST /v1/shares`: authenticated direct share request.

The bridge requires the configured extension `Origin`, a bearer token, bounded JSON body size, exact JSON parsing, correlation IDs, and structured 400/401/403/409/503 responses. It never logs or echoes raw capture content.

## Security and policy

The server rejects configured hosts other than `127.0.0.1` or normalized localhost. Tokens are generated from at least 32 random bytes, persisted with restrictive permissions where supported, and rotatable. The direct capability is separately named `archive.share_porchlight`; policy still enforces allowed write prefixes and all archive mutation invariants.

## Testing

Cover the bundle writer, policy/catalog metadata, service validation and conflict behavior, bridge auth/origin/size/loopback behavior, extension payload helpers and screenshot default, and an end-to-end temporary Archive text-plus-image share with rollback and unchanged update checks.
