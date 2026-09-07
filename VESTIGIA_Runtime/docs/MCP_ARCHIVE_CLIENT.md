# MCP Archive client

VESTIGIA Runtime can use the sibling VESTIGIA MCP Server as a local, read-only Archive
window. The same credential-minimizing stdio transport serves two separate Runtime needs:

- an optional context source that contributes bounded Archive evidence to relevant turns;
- explicit resident capabilities for intentional Archive browsing.

The client launches `python -m vestigia_mcp.cli` with only Archive paths, an MCP-owned
receipt directory, byte ceilings, and a deployment ID. Runtime/provider/Discord/tunnel
credentials and the Runtime home are not forwarded to the child process.

## Install

From `VESTIGIA_Runtime`, using Runtime's virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[mcp-context]" -e "..\VESTIGIA_MCP_Server"
```

Then configure either `home.yaml` or Runtime's normal environment resolver. Environment
example:

```dotenv
VESTIGIA_CONTEXT_MCP_ENABLED=true
VESTIGIA_CONTEXT_MCP_LIVE_ARCHIVE_ROOT=C:\path\to\VESTIGIA
VESTIGIA_CONTEXT_MCP_SNAPSHOT_ARCHIVE_ROOT=C:\path\to\VESTIGIA\Anima.zip
VESTIGIA_CONTEXT_MCP_RESIDENT_KEY=Liora
```

Restart Runtime after changing configuration.

## Resident capabilities

When enabled, the live capability registry exposes:

- `mcp.archive.status`
- `mcp.archive.list`
- `mcp.archive.search`
- `mcp.archive.read_text`
- `mcp.archive.read_media`
- `mcp.archive.health`
- `mcp.archive.diff_detail`

Each capability maps to one fixed MCP Server tool. There is intentionally no generic
arbitrary-tool passthrough and no Archive mutation capability.

Example:

```text
[[TOOL_ACTION {"action":"mcp.archive.list","source":"live","prefix":"Liora/pics","limit":50,"after":"continue"}]]
```

`mcp.archive.read_media` verifies MCP-provided image bytes against the reported SHA-256,
then imports them into the resident's private content-addressed image shelf. It returns an
`image_id`; the resident can use existing `image.inspect` and `image.drawer` capabilities
without placing image base64 in prompts or receipts. The source Archive is unchanged.

`mcp.archive.read_text` applies a Runtime-side character ceiling before its result enters the
resident tool loop or Runtime receipt. The optional `max_chars` argument may request between
500 and 50,000 characters; the default follows the configured house result-token budget.

## Authority boundary

MCP Archive results are external Archive records, not automatic memory, identity, adoption,
or canon. Media import creates a private local sensory copy with provenance; it does not
modify the Archive or make the image shareable. Existing Runtime policies continue to govern
inspection, annotation, pocketing, and outward sharing.
