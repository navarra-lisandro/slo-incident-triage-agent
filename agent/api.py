"""
agent/api.py

FastAPI application for the SLO incident triage agent.

Exposes a single endpoint:
  POST /triage    receive incident payload, invoke agent, return summary

Architecture (ADR-013):
  FastAPI performs thin structural validation only.
  ingest_incident owns semantic mapping and normalization.
  The graph is self-contained and independently invokable.

Request body validation:
  TriageRequest validates required top-level fields are present
  and structurally correct. Deep field validation is intentionally
  omitted — ingest_incident handles semantic normalization.

Response model:
  TriageResponse returns the incident_summary from state plus
  metadata fields useful for the caller (incident_id, severity,
  normalization_warnings).

FastAPI request body pattern:
  Reference: https://fastapi.tiangolo.com/tutorial/body/

Pydantic v2 model_dump() pattern:
  Reference: https://fastapi.tiangolo.com/how-to/
             migrate-from-pydantic-v1-to-pydantic-v2/

Health check pattern:
  GET /health returns service status for K8s liveness probe
"""

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent.graph import graph


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="SLO Incident Triage Agent",
    description=(
        "LangGraph agent that triages SLO burn rate incidents by reasoning "
        "over correlated APM golden signals, synthetics, and monitor states "
        "to classify severity and generate remediation recommendations."
    ),
    version="0.1.0",
)


# ---------------------------------------------------------------------------
# Request model — thin structural validation (ADR-013)
# Reference: https://fastapi.tiangolo.com/tutorial/body/
# ---------------------------------------------------------------------------

class MonitorPayload(BaseModel):
    """
    A single constituent monitor in the incoming payload.
    Field values may use provider-specific conventions at this layer.
    ingest_incident applies translation tables to canonical values.
    See ADR-010 for provider translation tables.
    """
    type: str
    signal: str
    metric: str
    current_value: Optional[float] = None
    threshold: Optional[float] = None
    status: str
    tags: list[str] = Field(default_factory=list)


class SLOPayloadRequest(BaseModel):
    """
    SLO fields from the incoming webhook payload.
    burn_rate is required — see Friction Log #2 for provider paths.
    """
    name: str
    target_pct: float
    burn_rate: float
    error_budget_remaining_pct: float
    window: str
    tags: list[str] = Field(default_factory=list)


class TriageRequest(BaseModel):
    """
    Thin structural validation of the POST /triage request body.
    Per ADR-013, only top-level structural correctness is validated here.
    Deep field validation is handled by ingest_incident.

    Provider-agnostic — accepts Datadog, Prometheus, Grafana payloads.
    See ADR-010 for the full provider translation table reference.
    """
    incident_id: str
    triggered_at: str
    service: str
    slo: SLOPayloadRequest
    firing_monitors: list[MonitorPayload]
    quiet_monitors: list[MonitorPayload] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Response model
# Reference: https://fastapi.tiangolo.com/tutorial/extra-models/
# ---------------------------------------------------------------------------

class TriageResponse(BaseModel):
    """
    Structured response from the POST /triage endpoint.
    Returns the incident_summary from agent state plus metadata.
    """
    incident_id: str
    severity: str
    severity_justification: str
    service: str
    budget_state: str
    time_to_exhaustion_minutes: Optional[float] = None
    firing_signals: list[str]
    failure_pattern: str
    cloud_provider_impact: Optional[str] = None
    responsible_teams: list[str]
    downstream_impact: list[str]
    recommended_steps: list[str]
    includes_failover: bool
    normalization_warnings: list[str]
    summary_narrative: str
    title: str
    created_at: str
    agent_completed_at: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health_check() -> dict[str, str]:
    """
    Health check endpoint for Kubernetes liveness probe.
    Returns 200 OK with service status.
    """
    return {
        "status": "ok",
        "service": "slo-incident-triage-agent",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/triage", response_model=TriageResponse)
async def triage_incident(request: TriageRequest) -> TriageResponse:
    """
    Receive an incident payload, invoke the LangGraph agent,
    and return a structured triage response.

    The graph is invoked synchronously. ingest_incident maps
    the raw payload to canonical IncidentState. All downstream
    nodes receive normalized state.

    Per ADR-013: FastAPI validates structure, ingest_incident
    maps semantics. The graph is self-contained.

    Returns 500 if the agent fails to produce an incident_summary.
    """
    # map TriageRequest to raw IncidentState input dict
    # ingest_incident handles semantic mapping and normalization
    initial_state: dict[str, Any] = {
        "incident_id": request.incident_id,
        "triggered_at": request.triggered_at,
        "service": request.service,
        "raw_slo": {
            "name": request.slo.name,
            "target_pct": request.slo.target_pct,
            "burn_rate": request.slo.burn_rate,
            "error_budget_remaining_pct": request.slo.error_budget_remaining_pct,
            "window": request.slo.window,
            "tags": request.slo.tags,
        },
        "firing_monitors": [m.model_dump() for m in request.firing_monitors],
        "quiet_monitors": [m.model_dump() for m in request.quiet_monitors],
        # initialize optional fields to safe defaults
        "unified_tags": {},
        "has_unknown_values": False,
        "normalization_warnings": [],
        "cloud_provider_statuses": [],
        "signal_correlation": None,
        "severity": None,
        "severity_justification": None,
        "runbook_steps": None,
        "remediation_plan": None,
        "incident_summary": None,
        "recent_history": None,
    }

    try:
        result = await graph.ainvoke(initial_state)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Agent invocation failed: {type(e).__name__}: {str(e)}"
        )

    summary = result.get("incident_summary")
    if not summary:
        raise HTTPException(
            status_code=500,
            detail="Agent completed but did not produce an incident summary"
        )

    return TriageResponse(
        incident_id=request.incident_id,
        severity=summary["severity"],
        severity_justification=summary["severity_justification"],
        service=summary["service"],
        budget_state=summary["budget_state"],
        time_to_exhaustion_minutes=summary.get("time_to_exhaustion_minutes"),
        firing_signals=summary["firing_signals"],
        failure_pattern=summary["failure_pattern"],
        cloud_provider_impact=summary.get("cloud_provider_impact"),
        responsible_teams=summary["responsible_teams"],
        downstream_impact=summary["downstream_impact"],
        recommended_steps=summary["recommended_steps"],
        includes_failover=summary["includes_failover"],
        normalization_warnings=summary.get("normalization_warnings", []),
        summary_narrative=summary["summary_narrative"],
        title=summary["title"],
        created_at=summary["created_at"],
        agent_completed_at=datetime.now(timezone.utc).isoformat(),
    )
