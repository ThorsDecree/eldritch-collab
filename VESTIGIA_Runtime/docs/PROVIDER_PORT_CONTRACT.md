# Provider-port capability contract

Status: design contract for issue #9

The provider port is a replaceable execution boundary. Provider identity must not be used as a
proxy for capability, cost, context size, error semantics, or availability. The Runtime asks an
adapter what it can do, applies local policy, validates live configuration, and only then exposes
a route as callable.

## Four distinct states

Every model route and provider feature reports four independent states:

1. `supported`: the adapter implements the operation.
2. `enabled`: operator and resident policy permit the operation.
3. `configured`: required credentials and route configuration are present.
4. `callable_now`: current health, limits, and dependency checks allow an invocation now.

A capability may be supported but disabled, enabled but unconfigured, or configured but
temporarily unavailable. Resident-facing capability panels and `doctor` must preserve those
distinctions.

## Provider descriptor

Each adapter exposes one immutable descriptor per adapter version:

```json
{
  "schema_version": "vestigia.provider-capabilities.v0.1",
  "provider_id": "openai",
  "adapter_version": "0.1.0",
  "api_styles": ["responses", "chat_completions"],
  "features": {
    "text_generation": true,
    "structured_output": true,
    "native_tool_calling": true,
    "parallel_tool_calls": true,
    "streaming": true,
    "vision_input": true,
    "image_generation": true,
    "image_editing": true,
    "reasoning_effort": true,
    "prompt_caching": true,
    "request_idempotency": false,
    "continuation_handle": true,
    "usage_accounting": true
  },
  "error_contract": "vestigia.provider-errors.v0.1"
}
```

The descriptor states adapter support, not operator authorization or live service status.
Unknown fields are ignored only when the schema version explicitly permits forward-compatible
extensions.

## Model routes

The Runtime resolves symbolic routes such as `default`, `big`, `thinking`, `vision`, and `image`
into explicit route descriptors:

```json
{
  "route_id": "thinking",
  "provider_id": "openai",
  "model": "gpt-5.6",
  "context_window_tokens": 128000,
  "maximum_output_tokens": 16384,
  "recommended_output_reserve_tokens": 12000,
  "supports": {
    "text_generation": true,
    "native_tool_calling": true,
    "parallel_tool_calls": true,
    "structured_output": true,
    "vision_input": true,
    "reasoning_effort": true,
    "prompt_caching": true,
    "continuation_handle": true
  },
  "limits": {
    "maximum_tool_calls": 128,
    "maximum_images": 8,
    "maximum_input_files": 20
  },
  "cost_class": "high",
  "status": {
    "supported": true,
    "enabled": true,
    "configured": true,
    "callable_now": true,
    "reason": null
  }
}
```

Context assembly uses the resolved route, not a universal token constant:

```text
usable input = context window
             - output/reasoning reserve
             - provider framing reserve
             - tool-result reserve
             - safety margin
```

The resident's configured prompt ceiling is a soft ceiling beneath that calculated maximum.

## Required adapter methods

A provider adapter implements:

```python
class ProviderPort(Protocol):
    def describe(self) -> ProviderDescriptor: ...
    def routes(self) -> list[ModelRoute]: ...
    def resolve_route(self, route_id: str) -> ModelRoute: ...
    def check(self, route_id: str) -> ProviderHealth: ...
    def generate(self, request: ProviderRequest) -> ProviderResult: ...
    def normalize_error(self, error: BaseException) -> ProviderFailure: ...
```

Image and vision operations may use focused subports, but they must return the same status,
usage, receipt, and error envelopes.

`describe`, `routes`, `resolve_route`, and static validation are side-effect free. `check` may
perform a bounded health probe only when explicitly requested; ordinary capability inspection
must not spend provider tokens.

## Request contract

A normalized request includes:

- stable request and turn IDs;
- selected route and resolved model;
- messages or provider-neutral input items;
- declared tool schemas;
- tool-call and output ceilings;
- reasoning, streaming, and structured-output preferences;
- optional continuation handle;
- optional provider idempotency key;
- privacy classification and data-retention preference;
- timeout and cancellation deadline.

The port rejects requests that require unsupported features before any outward provider action.
A downgrade, such as removing structured output or parallel tool calling, requires an explicit
Runtime policy and must be visible in the pre-call receipt.

## Result contract

Every provider call returns one envelope:

```json
{
  "status": "succeeded",
  "provider_request_id": "req_...",
  "continuation_handle": "resp_...",
  "output": [],
  "tool_calls": [],
  "usage": {
    "input_tokens": 1200,
    "cached_input_tokens": 800,
    "output_tokens": 300,
    "reasoning_tokens": 0,
    "estimated": false
  },
  "result_complete": true,
  "outward_effect": "provider_call_confirmed",
  "warnings": []
}
```

Valid statuses are `succeeded`, `partial`, `failed`, `cancelled`, and `not_run`.

`partial` means a provider call occurred and returned incomplete output. `not_run` proves the
adapter rejected or failed before an outward provider action. The Runtime must not collapse these
states into one generic exception.

## Normalized failures

Provider-specific errors map to:

- `authentication`;
- `authorization`;
- `invalid_request`;
- `unsupported_capability`;
- `context_limit`;
- `rate_limit`;
- `quota_exhausted`;
- `content_policy`;
- `timeout`;
- `connection`;
- `provider_unavailable`;
- `cancelled`;
- `malformed_response`;
- `unknown`.

A failure includes:

```json
{
  "category": "rate_limit",
  "retryable": true,
  "retry_after_seconds": 12,
  "outward_effect": "provider_call_possible",
  "provider_request_id": null,
  "safe_message": "Provider rate limit reached.",
  "diagnostic_code": "openai:429",
  "details_private": true
}
```

Adapters never place credentials, raw authorization headers, or private provider payloads in
resident-facing errors or support bundles.

## Continuation and retry

Continuation is capability-gated. A provider continuation handle is evidence of provider state,
not permission for another call.

Automatic retry is allowed only when:

- the error is explicitly retryable;
- the deadline and cost budget remain;
- the request has no unconfirmed outward side effects beyond the provider call itself;
- provider idempotency or Runtime reconciliation makes duplication acceptable;
- resident or operator policy permits retry.

The result records every attempt. The Runtime must not call a retry `exactly once` unless the
provider contract and implementation can prove it.

## Capability negotiation

Effective capability is the intersection of:

```text
adapter support
∩ route/model support
∩ installed dependencies
∩ valid configuration
∩ operator policy
∩ resident policy
∩ current health and budget
```

The capability registry consumes the effective result and preserves the reason a capability is
not callable.

## Contract tests

Every provider adapter must pass provider-neutral tests proving:

- descriptor and route schema validation;
- unsupported operations fail before outward action;
- route context limits constrain prompt assembly;
- all provider errors normalize deterministically;
- partial, failed, cancelled, and not-run states remain distinct;
- usage fields tolerate unavailable or estimated data;
- retries obey retryability and idempotency policy;
- continuation handles are route/provider bound;
- secrets do not enter receipts, logs, or support bundles;
- fake-provider tests make no network calls.

Live smoke tests remain separate, explicit, and opt-in.

## Non-goals

This contract does not promise identical output across providers, silently emulate unsupported
features, infer model limits from marketing names, or make billing exactly once. It makes the
boundary inspectable enough that routing and future tool batching can be optimized without
provider-specific assumptions leaking through the house.
