# API Architecture

The API package is organized by responsibility rather than by framework file:

```text
http -> application -> domain
             |
             v
       application ports
             ^
             |
      infrastructure adapters
```

- `http/` contains FastAPI routers, request schemas, and SSE transport helpers.
- `application/` contains use-case orchestration and ports such as repositories,
  session storage, task brokers, and workflow execution.
- `domain/` contains response-run state and workflow input types without database
  or FastAPI dependencies.
- `infrastructure/` contains Postgres, in-memory, configuration, messaging, and
  SDK workflow adapters.
- `main.py` remains a small ASGI compatibility entry point; app construction is
  implemented in `app/factory.py`.

Background execution should depend on `application.ports.TaskBroker`. The current
`InProcessTaskBroker` is suitable for tests and local development. A RabbitMQ
adapter can later be added under `infrastructure/messaging/` without changing
routers or application services. When the worker becomes a separate deployable,
add `packages/worker` and have it consume the same application ports.

Persistence follows the same boundary. `RunRepository` is the application port,
while the in-memory and Postgres repositories live under
`infrastructure/persistence/`.
