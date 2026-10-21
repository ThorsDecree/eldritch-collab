# Roadmap Build Slices — Approved Scope

This specification captures Jeff's approval to implement three slices in order: Lanternslide recovery, native Claude/Gemini provider adapters, then the first resident-tunable Recall Workbench slice. Each slice stays independently reviewable.

## Lanternslide recovery

An incomplete scan whose candidate path set changes must remain resumable under its existing scan ID. Reconcile removals and renames by removing stale paths and their indexed/omitted records, preserving results for paths still present, and queueing additions for later batches. Reconcile before advancing the batch offset. Wrong scan IDs and incompatible source configuration remain errors. Pending files are read from their current contents when reached. This slice does not promise a filesystem-wide atomic snapshot or detect edits to an already-indexed path whose name remains unchanged.

## Native providers

Add native text completion adapters for Anthropic Claude and Google Gemini, selectable with `provider.kind` and credentials resolved through the existing secret boundary. Preserve the Runtime's `ProviderRequest`/`ProviderReply` contract, `model_for` routes, usage and response IDs, and the current house-owned text action-envelope loop. Move developer instructions into each vendor's system-instruction field. Do not enable provider-native tool execution, streaming, images, retries, or automatic provider fallback in this slice. Missing SDKs, missing credentials, malformed responses, and API errors must be reported without exposing secrets.

## Recall Workbench — first slice

Extend the existing resident `context.control` and `retrieval.inspect` surfaces. Residents can tune the Runtime memory candidate limit and memory-layer token budget for future turns, within operator ceilings. Effective and requested values remain visible in the durable context receipt and inspection result. Existing provenance and inclusion/omission explanations remain authoritative. Settings do not change memory records, adoption, identity, source authority, ranking weights, or authorization.

## Cross-cutting constraints

- Keep the provider selection default as OpenAI and preserve existing OpenAI behavior.
- No API key or private-turn content may appear in diagnostics, receipts, or normal errors.
- Each slice is test-first and must keep its owning package suite green.
- Make external/API behavior deterministic in tests; no live provider keys are required.
- Resident controls may reduce or tune within operator limits; they cannot raise those limits.
