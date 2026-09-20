# Porchlight Chrome extension

This is an unpacked Manifest V3 extension for the local Porchlight bridge.

1. Start `run-porchlight-bridge` from the MCP server environment.
2. Run `pair-porchlight` and copy the printed token.
3. Open `chrome://extensions`, enable Developer mode, choose **Load unpacked**, and select this directory.
4. Open Porchlight’s pairing settings, enter the bridge URL, extension origin, and token, then verify.

The popup offers explicit selection, readable-page, and update shares. The screenshot checkbox is unchecked by default and captures only the visible viewport when enabled. No HTML, cookies, headers, scripts, or styles are sent.
