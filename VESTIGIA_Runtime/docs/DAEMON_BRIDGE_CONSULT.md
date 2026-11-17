# Runtime to Daemon-Bridge consults

The Runtime can optionally expose `mcp.bridge.consult` to the resident. This is
an explicit, metered call to a separate Daemon-Bridge resident, not automatic
context retrieval or arbitrary MCP access. A successful exchange is stored in
the recipient's Connections notebook. The peer's reply is untrusted source
material, not instructions or a change to the Runtime resident's identity.
The existing remote-content quarantine blocks further tool actions in that same
private turn after the peer reply; the resident can still respond normally.
The Runtime also writes its normal private action receipt, including the result;
the Bridge notebook remains the primary exchange record.

Install the optional MCP client and the sibling MCP Server into the Runtime's
Python environment (from `VESTIGIA_Runtime`):

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[mcp-context]" -e "..\VESTIGIA_MCP_Server"
```

The Bridge and MCP Server need the Connections notebook versions deployed. The
Bridge API must already be enabled and bound to loopback. Add the following to
the Runtime's normal `.env` (replace the path with the actual Bridge token file):

```env
VESTIGIA_BRIDGE_CONSULT_ENABLED=true
VESTIGIA_BRIDGE_ALLOWED_RESIDENTS=liora
VESTIGIA_BRIDGE_TOKEN_PATH=C:\path\to\Daemon-Bridge\data\system\bridge_api_token
VESTIGIA_BRIDGE_PORT=8766
VESTIGIA_BRIDGE_TIMEOUT_SECONDS=120
VESTIGIA_BRIDGE_MAX_ANSWER_CHARS=24000
```

Restart the Runtime. It forwards only the token **path**, fixed loopback host,
port, timeout, MCP receipt state, and a deployment label to a separate MCP stdio
child; provider, Discord and tunnel credentials are not forwarded. The token file
is read by the MCP connector. Only named residents in the comma-separated
operator allowlist are reachable. When disabled, the capability is not callable.
The capability currently requires an authenticated Discord doorway; it does not
permit CLI, web, bell or private-curation dispatch.

The resident can inspect its live `capabilities` and, if it wishes, invoke:

```text
[[TOOL_ACTION {"action":"mcp.bridge.consult","resident_id":"liora","content":"Hello from Liora@VESTIGIA-Runtime!","after":"continue"}]]
```

One action makes one consult and may incur provider cost. The MCP connector
checks notebook support before dispatch. A successful result includes the Bridge
receipt and notebook entry IDs; it does not mean a Discord message was sent.
The query and answer are saved in the Bridge resident's notebook, whose storage
is not encrypted by this feature. MCP's caller label identifies the configured
Runtime deployment, not independently verified personhood. Existing Runtime
receipts and private trace policy still apply.
The returned answer is bounded by `VESTIGIA_BRIDGE_MAX_ANSWER_CHARS`; the result
reports truncation and original character count. The notebook keeps the full reply.

If the result is `peer_delivery_uncertain`, a timeout or failure may have occurred
after the other runtime received the request. **Do not retry blindly**: inspect
the recipient's notebook and Bridge receipts first. The Runtime does not
implicitly retry. A saved notebook entry alone does not prove reply delivery.
