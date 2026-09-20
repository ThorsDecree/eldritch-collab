for (const button of document.querySelectorAll("button[data-mode]")) {
  button.addEventListener("click", async () => {
    const status = document.querySelector("#status");
    status.textContent = "Capturing…";
    try {
      const result = await chrome.runtime.sendMessage({
        type: "porchlight-capture",
        mode: button.dataset.mode,
        screenshot: document.querySelector("#screenshot").checked,
      });
      status.textContent = result?.error ? `Error: ${result.error}` : `Shared ${result.capture_id || "capture"}.`;
    } catch (error) {
      status.textContent = `Error: ${error.message || error}`;
    }
  });
}
