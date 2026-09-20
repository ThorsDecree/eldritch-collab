export const MAX_CAPTURE_BYTES = 1_000_000;

function requireText(value, label) {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`Porchlight ${label} must not be empty`);
  }
  return value;
}

export function captureReadableText(documentLike) {
  const text = documentLike?.body?.innerText ?? "";
  requireText(text, "page text");
  if (new TextEncoder().encode(text).byteLength > MAX_CAPTURE_BYTES) {
    throw new Error("Porchlight page text exceeds the byte ceiling");
  }
  return text;
}

export function buildSharePayload({
  url,
  title = "",
  text,
  mode,
  previousSnapshotSha256,
  screenshotBase64,
}) {
  requireText(url, "URL");
  requireText(text, "capture text");
  if (!["selection", "page", "update"].includes(mode)) {
    throw new Error("Porchlight capture mode is invalid");
  }
  if (new TextEncoder().encode(text).byteLength > MAX_CAPTURE_BYTES) {
    throw new Error("Porchlight capture text exceeds the byte ceiling");
  }
  const payload = {
    url,
    title: String(title),
    content: text,
    mode,
  };
  if (previousSnapshotSha256) {
    payload.previous_snapshot_sha256 = previousSnapshotSha256;
  }
  if (screenshotBase64) {
    payload.screenshot_base64 = screenshotBase64;
  }
  return payload;
}

export function mapBridgeResponse(status, body) {
  if (status === 409) return { state: "conflict", body };
  if (status >= 400) return { state: "error", body };
  if (body?.unchanged) return { state: "unchanged", body };
  return { state: "shared", body };
}
