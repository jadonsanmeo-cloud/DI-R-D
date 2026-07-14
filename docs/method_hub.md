# Method Hub Contract

This repository treats Method Hub as the engine-facing registry for reusable
capabilities. Engines should discover and invoke capabilities through this
boundary instead of hard-coding tool logic.

## Responsibilities

Method Hub owns four concerns:

1. Registration of callable methods with discovery metadata.
2. Trust gating for execution.
3. Deterministic selection when multiple methods can satisfy a capability.
4. Discovery artifacts for LLMs, loaders, exporters, and proposal workflows.

## Core Types

### `RegisteredMethod`

Each registered method carries:

- `name`
- `method`
- `capability_names`
- `trust_level`
- `metadata`
- `version`
- `description`
- `tags`
- `status`
- `priority`
- `source`

The current implementation keeps the contract intentionally simple:

- `trust_level` controls whether the method may execute.
- `status` describes lifecycle state.
- `priority` breaks ties during selection and catalog ordering.
- `metadata` stores extra discovery information and must stay JSON-serializable
  when exported to the catalog.

### Trust levels

Executable methods are currently:

- `builtin`
- `user_approved`
- `generated_validated`

Non-executable methods remain visible through `get_definition()` and catalog
views, but `get()` rejects them with a trust error.

### Method statuses

Supported statuses are:

- `draft`
- `experimental`
- `stable`
- `deprecated`

`deprecated` methods are excluded from execution.

## Registration Contract

`MethodHub.register()` validates and normalizes inputs:

- method names must be non-empty strings
- method objects must be callable
- capability names and tags are deduplicated and stripped
- trust level and status must be valid enum-like strings
- priority must be an integer
- metadata is deep-copied before storage

Registering an existing name without `replace=True` raises a duplicate error.

## Lookup and Selection

### `get(name)`

Returns the callable for an executable method only.

### `get_definition(name)`

Returns the full definition even when the method is blocked or deprecated.

### `list_methods(executable_only=False, statuses=None)`

Returns deterministic ordering:

1. higher `priority` first
2. higher trust rank first
3. name ascending

### `resolve(requirement)`

Returns the best executable method whose `capability_names` contain the
requirement name.

### `select_for_requirements(requirements)`

Returns unique methods across multiple requirements, preserving deterministic
selection order.

### `search(query, top_k=5)`

Performs lightweight lexical discovery over:

- name
- capability names
- tags
- description
- `metadata.use_when`
- `metadata.do_not_use_when`
- `metadata.category`

## Catalog Contract

`build_llm_catalog()` produces a JSON-serializable payload with:

- `format = "child-method-hub-catalog-v1"`
- a sorted `methods` list

The catalog includes only executable methods by default.

## YAML Manifest Contract

The runtime loader accepts YAML manifests with at least:

- `name`
- `version`
- `entrypoint`
- `description`
- `capability_names`
- `trust_level`

Optional but supported fields include:

- `tags`
- `status`
- `priority`
- `source`
- `metadata`
- `use_when`
- `do_not_use_when`

The loader resolves `entrypoint` to a Python callable and registers the method
into a `MethodHub`.

## Related Runtime Modules

- `runtime/method_loader.py` loads manifests into a hub.
- `runtime/method_catalog.py` writes catalog JSON for LLM discovery.
- `runtime/method_proposals.py` stores generated-unvalidated proposals in a
  file-backed workflow.
- `runtime/method_exporter.py` exports a single method bundle with manifest,
  source, and checksums.
- `runtime/method_tools.py` wraps search, description, and execution helpers.

## NAPH DataHub

The repository also includes a static parsed-file catalog in
`data_intelligence_sdk.datahub.NAPH_DATAHUB`. It is separate from Method Hub
but follows the same idea: a deterministic, inspectable contract around
metadata, discovery, and context generation.

