# ADR-013: FastAPI and Graph Boundary

## Status
Accepted

## Context
The agent is exposed as a FastAPI HTTP service. A key design decision
is where the boundary sits between the HTTP layer (FastAPI) and the
reasoning layer (LangGraph). Specifically, who is responsible for
mapping and validating the raw incoming payload before the graph
executes?

Three options were evaluated:

Option A: FastAPI maps fully to IncidentState
  FastAPI validates the full payload using a Pydantic model that
  mirrors IncidentState. The graph receives a fully mapped state
  object. Requires either a separate Pydantic model or TypeAdapter
  to derive one from the TypedDict.

Option B: TypeAdapter (DRY mapping)
  FastAPI validates using Pydantic's TypeAdapter(IncidentState),
  eliminating schema duplication. Still maps at the HTTP layer.

Option C (chosen): Thin FastAPI validation, ingest_incident maps
  FastAPI validates only that the payload is structurally valid JSON
  with required top-level fields present, using a thin Pydantic model.
  The raw dict is passed to the graph. ingest_incident handles all
  semantic mapping, translation table application, and canonical
  state construction.

## Decision
FastAPI performs thin structural validation only. ingest_incident
owns semantic mapping and normalization.

FastAPI validates:
  - Required top-level fields are present (incident_id, service,
    triggered_at, slo, firing_monitors, quiet_monitors)
  - Payload is valid JSON
  - Basic type correctness (str, list, dict)

ingest_incident handles:
  - Translation table application (ADR-010)
  - Tag unification (ADR-007)
  - Canonical field mapping
  - Unknown value detection and flagging

The thin Pydantic model in api.py:

  class TriageRequest(BaseModel):
      incident_id: str
      triggered_at: str
      service: str
      slo: dict
      firing_monitors: list[dict]
      quiet_monitors: list[dict]

## Rationale
The graph must be self-contained and independently invokable.
LangGraph graphs are invoked from multiple entry points: FastAPI,
CLI (make run), tests, and LangSmith evals. If payload mapping
lives in FastAPI only, every other entry point needs its own
mapping logic. Keeping mapping in ingest_incident means the graph
works correctly regardless of how it is invoked.

The translation tables in ingest_incident already handle
provider-specific field names. Mapping and normalization are
the same operation — separating them into two layers adds
complexity without meaningful benefit.

## Known Tradeoffs

### Single responsibility principle violation
ingest_incident is responsible for two things: HTTP payload
parsing and state normalization. The single responsibility
principle suggests these should be separate concerns. This
tradeoff is accepted because the alternative (mapping in FastAPI)
breaks graph portability across entry points.

### Loss of deep field-level 422 validation
FastAPI's Pydantic validation provides free 422 error responses
with field-level error messages when deep schema validation is
configured. With thin validation, field-level errors inside slo,
firing_monitors, and quiet_monitors are not caught at the HTTP
layer. They are caught by ingest_incident instead, which returns
normalization_warnings rather than HTTP 422 responses.

This tradeoff is accepted because: the agent's payload schema
is intentionally flexible to support multiple providers (ADR-010),
and strict field-level validation at the HTTP layer would reject
valid payloads from providers whose field values differ from
the canonical schema.

## Consequences
- The graph is self-contained and independently testable
  without FastAPI or Pydantic in the test path
- ingest_incident unit tests cover both parsing and normalization
- Field-level payload errors surface as normalization_warnings
  in the agent response rather than HTTP 422 responses
- Adding a new entry point (Lambda, CLI, test fixture) requires
  no mapping logic beyond constructing the raw dict
- api.py remains thin: receive request, invoke graph, return response
