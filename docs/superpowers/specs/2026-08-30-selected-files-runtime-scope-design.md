# Selected Files Runtime Scope Design

## Goal

When a chat selects workspace files, the general-purpose agent must be able to
retrieve data only from those files. Selected workspace files will not be copied
into the general agent's Python sandbox.

## Naming

The external request contract remains `selected_files`. The runtime uses a typed
`SelectedFilesScope` value and exposes it as `selected_files_scope` on the
runtime context. Its authoritative identifiers are `document_ids`; display
names, object keys, and bucket names are not authorization inputs.

## Architecture

The intelligence service validates that selected file references belong to the
authorized workspace before constructing the runtime request. The SDK converts
the validated request into an immutable selected-files scope and gives that
scope to the engine runtime.

The SDK creates request-scoped Method Hub tools. For retrieval methods, the
tool adapter injects the selected `document_ids` and removes caller-provided
document selectors. For single-document methods, it accepts only a selector
contained in the scope. Calls that cannot be safely restricted are rejected or
not exposed to the agent.

The prompt is guidance only. It tells the agent to use retrieval tools and not
search the local filesystem, but the tool adapter is the enforcement boundary.
The selected workspace files are therefore not staged as Python sandbox inputs.

## Retrieval behavior

In selected mode:

- `corpus_vector_search`, `corpus_bm25_search`, and `corpus_retrieve_context`
  always receive the selected `document_ids`.
- `corpus_get_file_ingested_data` may receive only a selected `document_id`.
- `get_neighbor_chunk` may receive only a selected document as `file_id`.
- Any unsupported broad retrieval call fails closed instead of falling back to
  workspace-wide search.

In all-workspace mode, existing workspace-level retrieval behavior is retained.
The runtime scope is request-local and must not be persisted as mutable agent
state between turns.

## Data flow

```text
selected_files.resource_ids
  -> workspace authorization
  -> SelectedFilesScope(document_ids)
  -> EngineRuntimeContext.selected_files_scope
  -> scoped Method Hub tool adapter
  -> corpus retrieval filtered by document_ids
```

## Error handling

If a selected-mode call supplies a document outside the scope, the adapter
returns a structured tool error and records the rejected call in the runtime
trace. It must not silently broaden the query or use a filename/object key as
a substitute for the document ID.

If no selected file is available, the agent receives no local workspace file
path. It should answer from ordinary conversation context or explain that the
requested source is unavailable.

## Testing

SDK tests cover injection for corpus search, enforcement for single-document
lookup and neighbor retrieval, rejection of out-of-scope IDs, and preservation
of all-workspace behavior. API/intelligence-service tests cover propagation of
the selected file IDs and ensure selected workspace files are not staged for
the general engine.
