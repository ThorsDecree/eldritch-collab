import {
  buildSharePayload,
  captureReadableText,
} from "./capture_contract.mjs";

const DEFAULTS = {
  bridgeUrl: "http://127.0.0.1:8765",
  extensionOrigin: "chrome-extension://porchlight",
};

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "porchlight-selection",
    title: "Share selection with Porchlight",
    contexts: ["selection"],
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== "porchlight-selection" || !tab?.id) return;
  try {
    const result = await captureAndShare(tab, "selection", info.selectionText || "", false);
    await chrome.storage.local.set({ porchlightLastResult: result });
  } catch (error) {
    await chrome.storage.local.set({ porchlightLastError: String(error.message || error) });
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type !== "porchlight-capture") return undefined;
  captureAndShareFromActiveTab(message.mode, Boolean(message.screenshot))
    .then(sendResponse)
    .catch((error) => sendResponse({ error: String(error.message || error) }));
  return true;
});

async function settings() {
  return { ...DEFAULTS, ...(await chrome.storage.local.get(DEFAULTS)) };
}

async function captureAndShareFromActiveTab(mode, screenshot) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) throw new Error("No active tab is available");
  return captureAndShare(tab, mode, null, screenshot);
}

async function captureAndShare(tab, mode, selectedText, screenshotEnabled) {
  const extracted = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: (requestedMode, selected) => ({
      text: requestedMode === "selection" ? selected || window.getSelection()?.toString() || "" : document.body?.innerText || "",
      url: location.href,
      title: document.title,
    }),
    args: [mode, selectedText],
  });
  const page = extracted?.[0]?.result;
  if (!page) throw new Error("The active page did not return readable content");
  const config = await settings();
  const previous = mode === "update" ? (await chrome.storage.local.get("porchlightLastResult")).porchlightLastResult?.latest_sha256 : undefined;
  let screenshotBase64;
  if (screenshotEnabled) {
    const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
    screenshotBase64 = dataUrl.split(",", 2)[1];
  }
  const payload = buildSharePayload({
    url: page.url,
    title: page.title,
    text: page.text,
    mode,
    previousSnapshotSha256: previous,
    screenshotBase64,
  });
  const response = await fetch(`${config.bridgeUrl.replace(/\/$/, "")}/v1/shares`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Origin: config.extensionOrigin,
      Authorization: `Bearer ${config.token || ""}`,
    },
    body: JSON.stringify(payload),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body?.error?.message || "Porchlight bridge request failed");
  await chrome.storage.local.set({ porchlightLastResult: body, porchlightLastError: "" });
  return body;
}
