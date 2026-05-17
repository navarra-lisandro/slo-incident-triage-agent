# ADR-005: FastAPI Webhook Service Architecture

## Status
Accepted

## Context
The agent needs an entry point that reflects how incident triage works
in production — alerts arrive as HTTP webhook payloads from monitoring
systems, not as command-line arguments. A web service architecture
is required to receive, validate, and route these payloads to the
LangGraph agent.

## Decision
I implemented the agent as an HTTP service using FastAPI 0.136.1,
exposed via a POST /triage endpoint. The service receives a normalized
incident payload validated by Pydantic 2.13.4, passes it to the
LangGraph agent, and returns a structured triage response. The service
is served by uvicorn 0.47.0 as the ASGI server.

## Components

### FastAPI 0.136.1
**Purpose:** HTTP framework for receiving and routing webhook payloads

**Pros:**
- Async by default — non-blocking request handling suitable for
  LangGraph agent execution which involves multiple LLM API calls
- Automatic OpenAPI/Swagger documentation at /docs — useful for
  testing webhook payloads during development
- Native Pydantic integration for request/response validation
- Lightweight — minimal overhead for a single-endpoint service

**Cons:**
- Requires uvicorn or similar ASGI server to run — not self-contained
- Overkill for a single endpoint — a simpler framework like Flask
  would technically suffice
- async/await patterns require care when integrating with synchronous
  LangGraph execution paths

### uvicorn 0.47.0
**Purpose:** ASGI server that runs the FastAPI application

**Pros:**
- Production-grade performance with minimal configuration
- --reload flag enables hot reloading during local development
- Lightweight footprint inside the Docker container
- Standard pairing with FastAPI — well-documented integration

**Cons:**
- Runs as a single process by default — production deployments should
  use gunicorn with uvicorn workers for multi-process handling
- Not included in FastAPI itself — separate dependency to manage

### Pydantic 2.13.4
**Purpose:** Request payload validation and response serialization

**Pros:**
- Validates incoming webhook payloads against defined schemas at
  the API boundary before they reach the LangGraph agent
- Python type hints as the schema definition — no separate schema
  language to learn
- v2 offers significant performance improvements over v1 via
  pydantic-core (Rust-based)
- Already a transitive dependency of FastAPI, LangChain, and LangSmith
  — no additional install cost

**Cons:**
- Pydantic v2 introduced breaking changes from v1 — third-party
  libraries pinning v1 can cause conflicts (not an issue here as all
  dependencies require v2)
- Schema validation errors at the API boundary require explicit error
  handling to avoid exposing internal model details in responses

## Payload Source Agnosticism
The /triage endpoint accepts a normalized incident payload schema that
is monitoring-system agnostic. While the payload format is modeled on
Datadog monitor-based SLO webhooks, any alerting system capable of
firing an HTTP POST — including PagerDuty, Prometheus Alertmanager,
or Grafana — can trigger the agent by conforming to the same schema.
This decouples the agent from the alert source in the same way LangGraph
decouples the orchestration from the LLM provider.

## Alternatives Considered
- Flask 3.x — simpler, synchronous, widely understood. Ruled out
  because FastAPI's native async support and Pydantic integration
  better fit the LangGraph execution model.
- Django REST Framework — significant overhead for a single-endpoint
  service. Ruled out immediately.
- AWS Lambda + API Gateway — the production promotion path. The FastAPI
  service is portable to Lambda via the Mangum adapter with minimal
  changes. Documented as the next infrastructure layer but not
  implemented in this iteration to keep scope manageable.
- CLI script only (make run) — simpler but not representative of
  production usage. Retained as a development convenience target
  in the Makefile.

## Consequences
- The agent requires uvicorn to run — it is no longer a standalone script
- Local development: make serve (uvicorn with --reload)
- Container deployment: same FastAPI service via Docker
- Kubernetes deployment: service exposed via K8s Service and Ingress
- Production scaling: gunicorn + uvicorn workers, or Lambda via Mangum
- The /docs endpoint provides a built-in testing interface for
  webhook payload validation during development

## References
- FastAPI documentation: https://fastapi.tiangolo.com
- uvicorn documentation: https://www.uvicorn.org
- Pydantic v2 documentation: https://docs.pydantic.dev/latest
- Mangum (Lambda adapter): https://mangum.fastapiexpert.com
