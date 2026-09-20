const bridgeUrl = document.querySelector("#bridgeUrl");
const extensionOrigin = document.querySelector("#extensionOrigin");
const token = document.querySelector("#token");
const status = document.querySelector("#status");

const saved = await chrome.storage.local.get({
  bridgeUrl: bridgeUrl.value,
  extensionOrigin: extensionOrigin.value,
  token: "",
});
bridgeUrl.value = saved.bridgeUrl;
extensionOrigin.value = saved.extensionOrigin;
token.value = saved.token;

document.querySelector("#save").addEventListener("click", async () => {
  status.textContent = "Verifying…";
  await chrome.storage.local.set({
    bridgeUrl: bridgeUrl.value,
    extensionOrigin: extensionOrigin.value,
    token: token.value,
  });
  try {
    const response = await fetch(`${bridgeUrl.value.replace(/\/$/, "")}/v1/pair/verify`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Origin: extensionOrigin.value,
        Authorization: `Bearer ${token.value}`,
      },
      body: "{}",
    });
    status.textContent = response.ok ? "Paired." : "Pairing failed.";
  } catch (error) {
    status.textContent = `Pairing error: ${error.message || error}`;
  }
});
