import assert from "node:assert/strict";
import test from "node:test";

import {
  buildSharePayload,
  captureReadableText,
  mapBridgeResponse,
} from "../../porchlight_extension/capture_contract.mjs";

test("selection capture requires non-empty readable text", () => {
  assert.throws(
    () => buildSharePayload({ url: "https://example.test", title: "x", text: "  ", mode: "selection" }),
    /empty/
  );
});

test("page capture payload contains readable text and metadata only", () => {
  const payload = buildSharePayload({
    url: "https://example.test/page",
    title: "Example",
    text: "body text",
    mode: "page",
  });
  assert.deepEqual(payload, {
    url: "https://example.test/page",
    title: "Example",
    content: "body text",
    mode: "page",
  });
  assert.equal("html" in payload, false);
  assert.equal("cookies" in payload, false);
});

test("update payload carries the expected latest hash", () => {
  const payload = buildSharePayload({
    url: "https://example.test/page",
    title: "Example",
    text: "new text",
    mode: "update",
    previousSnapshotSha256: "a".repeat(64),
  });
  assert.equal(payload.mode, "update");
  assert.equal(payload.previous_snapshot_sha256, "a".repeat(64));
});

test("screenshot is omitted unless explicitly enabled", () => {
  const without = buildSharePayload({
    url: "https://example.test",
    title: "x",
    text: "body",
    mode: "page",
    screenshotBase64: null,
  });
  const withShot = buildSharePayload({
    url: "https://example.test",
    title: "x",
    text: "body",
    mode: "page",
    screenshotBase64: "iVBORw0KGgo=",
  });
  assert.equal("screenshot_base64" in without, false);
  assert.equal(withShot.screenshot_base64, "iVBORw0KGgo=");
});

test("page helper uses readable body text and rejects empty pages", () => {
  assert.equal(captureReadableText({ body: { innerText: "readable" } }), "readable");
  assert.throws(() => captureReadableText({ body: { innerText: "\n" } }), /empty/);
});

test("capture helper rejects text over the one-megabyte ceiling", () => {
  assert.throws(
    () => buildSharePayload({ url: "https://example.test", text: "x".repeat(1_000_001), mode: "page" }),
    /byte ceiling/
  );
});

test("bridge responses expose stable UI states", () => {
  assert.equal(mapBridgeResponse(200, { shared_directly: true }).state, "shared");
  assert.equal(mapBridgeResponse(200, { unchanged: true }).state, "unchanged");
  assert.equal(mapBridgeResponse(409, {}).state, "conflict");
  assert.equal(mapBridgeResponse(400, {}).state, "error");
});
