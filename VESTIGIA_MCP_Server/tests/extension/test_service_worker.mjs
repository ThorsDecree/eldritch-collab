import assert from "node:assert/strict";
import { cp, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";

const extensionDirectory = new URL("../../porchlight_extension/", import.meta.url);

async function sharePageWithPairedToken(token) {
  const workerDirectory = await mkdtemp(join(tmpdir(), "porchlight-worker-"));
  const workerPath = join(workerDirectory, "service_worker.mjs");
  await cp(new URL("service_worker.js", extensionDirectory), workerPath);
  await cp(new URL("capture_contract.mjs", extensionDirectory), join(workerDirectory, "capture_contract.mjs"));

  const previousChrome = globalThis.chrome;
  const previousFetch = globalThis.fetch;
  const requests = [];
  const stored = { token };
  let messageHandler;
  globalThis.chrome = {
    contextMenus: {
      create() {},
      onClicked: { addListener() {} },
    },
    runtime: {
      onInstalled: { addListener() {} },
      onMessage: { addListener(handler) { messageHandler = handler; } },
    },
    scripting: {
      async executeScript() {
        return [{ result: { url: "https://example.test/page", title: "Example", text: "Readable page" } }];
      },
    },
    storage: {
      local: {
        async get(keys) {
          if (typeof keys === "string") return { [keys]: stored[keys] };
          return Object.fromEntries(
            Object.entries(keys).map(([key, fallback]) => [key, stored[key] ?? fallback])
          );
        },
        async set(values) { Object.assign(stored, values); },
      },
    },
    tabs: {
      async query() { return [{ id: 1, windowId: 1 }]; },
    },
  };
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    return { ok: true, json: async () => ({ shared_directly: true }) };
  };

  try {
    await import(`${pathToFileURL(workerPath).href}?token=${encodeURIComponent(token)}`);
    const result = await new Promise((resolve) => {
      messageHandler({ type: "porchlight-capture", mode: "page", screenshot: false }, {}, resolve);
    });
    return { requests, result };
  } finally {
    globalThis.chrome = previousChrome;
    globalThis.fetch = previousFetch;
    await rm(workerDirectory, { force: true, recursive: true });
  }
}

test("page sharing forwards the token saved during pairing", async () => {
  const { requests, result } = await sharePageWithPairedToken("paired-token");

  assert.deepEqual(result, { shared_directly: true });
  assert.equal(requests.length, 1);
  assert.equal(requests[0].options.headers.Authorization, "Bearer paired-token");
});
