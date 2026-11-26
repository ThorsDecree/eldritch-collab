# MCP Archive client

VESTIGIA Runtime can use the sibling VESTIGIA MCP Server as a local Archive window.
The credential-minimizing stdio transport serves three Runtime needs:

- an optional context source that contributes bounded Archive evidence to relevant turns;
- explicit resident capabilities for intentional Archive browsing;
- optional staged live Archive text changes under operator-granted paths.

The client launches `python -m vestigia_mcp.cli` with only Archive paths, an MCP-owned
receipt directory, byte ceilings, and a deployment ID. Runtime/provider/Discord/tunnel
credentials and the Runtime home are not forwarded to the child process.

## Install

The Windows `Start VESTIGIA.cmd` launcher checks the Runtime virtual
environment each time it starts. With the sibling MCP Server checkout present,
it installs or repairs both the `mcp-context` extra and the local server
package. Use `Start VESTIGIA.cmd --prepare` to prepare the environment without
starting an interface. The repository's [Windows suite launcher](../../README.md#start-a-windows-pilot)
can prepare both environments and start the Runtime and tunnel together.

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

Each capability maps to one fixed MCP Server tool.

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

## Opt-in live Archive writes

Set `VESTIGIA_CONTEXT_MCP_WRITE_PREFIXES=Vex` in the pilot **Runtime** environment,
or put `write_prefixes: [Vex]` under `context_sources` → `mcp_archive` in its
`home.yaml`, and restart Runtime. The prefix is relative to
`VESTIGIA_CONTEXT_MCP_LIVE_ARCHIVE_ROOT`.
An empty grant keeps the write tools out of the registry. The tunnel's
`VESTIGIA_MCP_ARCHIVE_WRITE_PREFIXES` belongs to the separate tunnel process.

The write tools are available in a local CLI or web conversation and in allowlisted
one-to-one Discord DMs. The prefix grant authorizes these conversations to stage
and promote text; promotion requires a separate tool action with the exact digest.
Bell, curation, group DM, and Discord server-channel turns are excluded.

1. Use `mcp.archive.write_capabilities` to check the grant, allowed file types, and
   byte ceiling.
2. Use `mcp.archive.stage_text` with the relative path, replacement text, and
   `expected_base_sha256`. Pass `"absent"` for a create or the live
   `mcp.archive.read_text` result's `content_sha256` for a replacement. The parent
   directory must already exist. Staging stores a proposal in MCP state.
3. Use `mcp.archive.stage_inspect` to check the proposal and its live base.
4. Use `mcp.archive.promote` with the exact `stage_id` and `proposal_sha256`, then
   read the live file again and compare its `content_sha256` with the receipt's
   `result_sha256`.

`mcp.archive.stage_list` locates proposals after restart, and
`mcp.archive.stage_discard` clears a pending proposal. If a promotion response is
interrupted, inspect the stage and read back the live file before retrying.
MCP checks the configured prefix and captured base before an atomic replacement.
Concurrent writers using independent MCP processes can race between that check
and replacement. Use one writer at a time for the pilot.

Proposal state lives under the Runtime home's `traces/`, scoped to the configured
live root, so proposals staged for one root cannot be applied after switching to
another root. The write child receives no snapshot path or tunnel credentials.
Retrieval and Archive browsing keep using their read-only child.

## Dedicated MCP deployment

For a single resident with a dedicated MCP deployment, point Runtime at the
deployment's own MCP settings file:

```dotenv
VESTIGIA_CONTEXT_MCP_DEPLOYMENT_ENV_FILE=C:\path\to\pilot-mcp.env
```

The file contains literal `VESTIGIA_MCP_*` settings. It requires an absolute live
Archive directory, an absolute state directory outside the Archive, and an explicit
deployment ID. Put the pilot's Archive prefixes and any mount or service bindings in
this file. Use absolute paths for filesystem settings and keep the file outside the
Archive. The file defines the resident's authority, so link only the pilot's resources.
GameTable is available by default with this file. A deployment can explicitly set
`VESTIGIA_MCP_GAMETABLE_ENABLED=0`.

Run the dedicated tunnel's MCP executable with the same file as its
`--deployment-env` argument. Both processes then use the same Lanternslide catalog,
GameTable database, Archive stages and MCP receipts. Their MCP settings come from the
file, and the network tunnel's control-plane API key stays with the tunnel launcher.
Existing Runtime Archive retrieval uses its own read-only configuration.

```text
[[TOOL_ACTION {"action":"mcp.tools","prefix":"lanternslide."}]]
[[TOOL_ACTION {"action":"mcp.tools","tool":"game.create"}]]
[[TOOL_ACTION {"action":"mcp.call","tool":"lanternslide.scan","arguments":{}}]]
```

`mcp.tools` lists all tools advertised by the selected deployment, with optional
`prefix`, `offset` and `limit`. An exact `tool` lookup returns its native input schema.
`mcp.call` dispatches that tool with an `arguments` object; the server enforces its own
policy, path grants, revision checks and seat tokens. Both actions are available in
local web and CLI conversations and allowlisted one-to-one Discord DMs.
The MCP manifest resources are also readable through the native `archive.read_text`
tool at `manifest.md` with the corresponding `live` or `snapshot` source.

To browse pictures, finish a Lanternslide scan, use `lanternslide.deal` or
`lanternslide.search`, and request `lanternslide.contact_sheet` with selected image IDs.
Runtime imports the result into its private image shelf for `image.inspect`.
GameTable returns seat credentials on creation. Give each player their own credential
privately. Delegated MCP receipts keep the tool name, request ID when supplied, and
any private image IDs. Tool bodies, including hands and bearer tokens, are available
to the conversational model in the private tool round. Account for any provider
processing, full prompt capture, or conversational disclosure when handling them.

## Authority boundary

MCP Archive results are external Archive records, not automatic memory, identity, adoption,
or canon. Media import creates a private local sensory copy with provenance; it does not
modify the Archive or make the image shareable. Existing Runtime policies continue to govern
inspection, annotation, pocketing, and outward sharing.
