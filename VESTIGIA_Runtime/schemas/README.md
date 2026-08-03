# Workshop Within schemas

These Draft 2020-12 JSON Schemas define the serialized design contracts for resident rituals,
resident scripts, effective workshop grants, execution state, and receipts.

Schema validation is necessary but not sufficient. Implementations must also perform semantic
validation for:

- unique ritual step IDs;
- finite acyclic control-flow and dependency graphs;
- reference resolution and path-sensitive value availability;
- capability existence, callability, effect, and scope intersection;
- source, schema, grant, plan, object, backend, and environment hash binding;
- checkpoint freshness and resume safety;
- aggregate nested budget limits;
- sandbox-backend guarantee requirements;
- outward confirmation boundaries;
- privacy and provenance non-escalation.

External `$ref` values use repository-local sibling schema names. Packaged distributions should
ship the schema set together and register canonical `$id` values without network retrieval.
