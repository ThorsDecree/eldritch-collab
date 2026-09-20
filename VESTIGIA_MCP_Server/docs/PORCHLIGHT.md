# Porchlight browser sharing

Porchlight turns an explicit browser action into a bounded, searchable warm Archive receipt.
The v1 path is a local MV3 Chrome extension talking to an authenticated Python bridge on
`127.0.0.1`; Chrome does not speak MCP stdio directly.

## Start and pair

From the MCP server environment:

```text
pair-porchlight
run-porchlight-bridge
```

Configure `VESTIGIA_MCP_PORCHLIGHT_BRIDGE_EXTENSION_ORIGIN` to the actual unpacked extension
origin (`chrome-extension://<id>`), then load `porchlight_extension/` from
`chrome://extensions` with Developer mode enabled. Open the extension’s pairing settings,
enter the bridge URL, origin, and token, and choose **Save and verify**.

The bridge is loopback-only, uses a bearer token stored under the configured state directory,
requires the configured extension origin, bounds JSON bodies, and does not log capture content.

## Capture contract

The popup offers explicit **Share selection**, **Share readable page**, and **Share page update**
actions. A context-menu action shares selected text. The page action sends `document.body.innerText`
plus URL/title metadata; it never sends raw HTML, cookies, headers, scripts, or styles.

The screenshot checkbox is unchecked by default. When enabled, it sends only the visible viewport
PNG returned by `chrome.tabs.captureVisibleTab`; screenshot bytes are stored separately and are
not semantic receipt text. Empty or over-limit captures are rejected in the extension before HTTP.

## Direct layout

Direct resident shares use this namespace:

```text
Modules/Porchlight/latest/<source-key>.md
Modules/Porchlight/history/<source-key>_<capture-id>.md
Modules/Porchlight/receipts/<source-key>_<capture-id>.json
Modules/Porchlight/images/<source-key>/<capture-id>.png
```

Latest bodies are searchable like other warm transcript sources. History and receipts are
addressable provenance shelves. The receipt records canonical URL, title, mode, timestamp,
content hash/size, optional screenshot path/hash/size, and consent basis; it contains no HTML or
image bytes. Repeating the same latest content returns `unchanged` without another history copy.
Update requests bind to the latest body hash and return a conflict rather than overwriting a
newer capture.

Clicking Porchlight is the consent gate for this direct path. There is no second promotion click,
but write prefixes, path normalization, byte ceilings, hashes, conflict checks, audit records,
and atomic text/receipt/image bundle behavior still apply. A failed screenshot or receipt write
leaves the prior latest untouched.

## Staged compatibility path

`archive.stage_porchlight` remains available for scripted/import workflows. It accepts URL,
title, readable content, `selection`/`page`/`update`, timestamp, and an optional previous hash,
and stages the legacy root-level layout:

```text
Porchlight/latest/<source-key>.md
Porchlight/history/<source-key>_<capture-id>.md
Porchlight/receipts/<source-key>_<capture-id>.json
```

Legacy root-level artifacts remain readable/addressable during migration. The staged tool still
requires inspection and explicit `archive.promote`; the bridge’s explicit resident action uses
the separate `archive.share_porchlight` direct capability.
