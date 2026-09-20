# Sense Organs and Receipt Garden

VESTIGIA MCP describes bounded external perception through declarative sense-organ manifests.
The registry is a contract surface, not a generic observation API.

## Sense Organ Registry

The read-only tools are:

- `sense.list` — list registered organs and manifest digests.
- `sense.show` — inspect one organ's modality, activation topology, consent basis, payload
  boundary, retention, destination, limits, and semantic policy.
- `sense.can_perceive` — check a proposed capture against the manifest before observation.

Porchlight is the first organ. It is explicitly invoked by the resident, accepts readable text
and page metadata, and may include a screenshot only when requested. Raw HTML, cookies,
credentials, hidden page state, and browser history are outside its contract. Registry metadata
does not authorize a generic `sense.observe` operation.

## Receipt Garden

MCP-owned provenance is stored in the deployment state directory as bounded JSONL, separate from
the canonical Archive. `receipts.trace` joins records by request ID and exposes typed edges such
as `observed`, `authorized`, `queried`, `included`, `omitted`, `dispatched`, `stored`, and
`returned`.

Receipts contain safe summaries, hashes, references, omissions, and policy facts. Raw arguments,
page payloads, cookies, credentials, and arbitrary response bodies are not persisted. A receipt
explicitly reports `causal_influence: unknown`: inclusion is evidence of availability and routing,
not proof that an item caused a response.

Use `receipts.recent` for recent MCP audit events and `receipts.trace` for a joined provenance
trace. These are operational evidence surfaces, not memory or identity.

## Runtime join

Runtime remains the authority for bell retrieval and attention facts. A Runtime read projection
can expose Bell Observatory actions such as `bell.run.inspect` and `bell.run.replay`; MCP does
not read the Runtime database directly. The request ID returned by `runtime.call` joins the MCP
receipt with the Runtime result.
