# API Architecture

The API package is organized by responsibility rather than by framework file:

```text
HTTP operation envelope -> application workflow adapter -> SDK pipeline
                                |
                                v
                    corpus / Method Hub / sandbox adapters
```

- `http/` contains FastAPI routers, request schemas, and SSE transport helpers.
- `application/` contains stateless operation and workflow orchestration.
- `domain/` contains workflow input types without response lifecycle state.
- `infrastructure/` contains configuration, corpus, Method Hub, sandbox, and SDK
  workflow adapters.
- `main.py` remains a small ASGI compatibility entry point; app construction is
  implemented in `app/factory.py`.

The API owns no response repository, confirmation token, revision history, or
runtime database. AXIOM supplies a self-contained operation payload and persists
the accepted result. Filesystem runtime artifacts support execution diagnostics;
they are not the response control plane.
