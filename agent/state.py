"""
agent/state.py

Agent state definition for the SLO incident triage agent.
This is the single data structure that flows between all nodes in the graph.

LangGraph state pattern — nodes return partial dicts, LangGraph merges:
  Reference: https://langchain-ai.github.io/langgraph/reference/graphs/

Reducer pattern for list fields written by multiple nodes:
  Reference: https://langchain-ai.github.io/langgraph/reference/graphs/#langgraph.graph.StateGraph

TypedDict as state schema (preferred over Pydantic for LangGraph):
  Reference: https://medium.com/@sreeni5018/the-architecture-of-agent-memory-how-langgraph-really-works

Node responsibility map:
  ingest_incident         writes: raw_slo, firing_monitors,
                                  quiet_monitors, unified_tags,
                                  has_unknown_values,
                                  normalization_warnings
  normalize_incident      writes: firing_monitors, quiet_monitors
                                  normalization_warnings (appends)
                                  (conditional — only if has_unknown_values)
  assess_slo_impact       writes: time_to_exhaustion_minutes,
                                  urgency_score, budget_state
  check_cloud_status      writes: cloud_provider_statuses
                                  (conditional — only if cloud tag present)
  triage_firing_signals   writes: signal_correlation
  classify_severity       writes: severity, severity_justification
  query_runbook           writes: runbook_steps
  generate_remediation    writes: remediation_plan
  draft_summary           writes: incident_summary

Design decisions:
  ADR-006   deterministic vs LLM node separation
  ADR-007   tag normalization strategy
  ADR-008   historical pattern detection exclusion
  ADR-009   cloud provider status check
  ADR-010   payload schema design
  ADR-011   graph topology
  ADR-012   runbook architecture
  ADR-013   FastAPI and graph boundary
"""

import operator
from typing import Annotated, Optional
from typing_extensions import TypedDict


# ---------------------------------------------------------------------------
# Sub-structures — input payload
# ---------------------------------------------------------------------------

class SLOPayload(TypedDict):
    """
    Normalized SLO fields from the incoming webhook payload.
    Modeled on Datadog monitor-based SLO webhooks.

    burn_rate is a required field regardless of provider.
    See ADR-010 for provider translation tables.
    See Friction Log #2 for SLO burn rate provider agnosticism.
    """
    name: str
    target_pct: float
    burn_rate: float
    error_budget_remaining_pct: float
    window_seconds: int                 # canonical internal format
                                        # 1h fast burn = 3600
                                        # 6h slow burn = 21600
    tags: list[str]                     # raw tags from SLO payload


class Monitor(TypedDict):
    """
    A single constituent monitor — either firing or quiet.
    Field values use canonical internal schema after ingest_incident.

    Canonical values after translation (ADR-010):
      type:    "performance" or "synthetic"
      signal:  "latency", "errors", "saturation",
               "traffic", "synthetic_check"
      status:  "firing" or "healthy"

    tags field includes notification literals for ownership inference:
      e.g. notify:@team@company.com, pagerduty:payments-escalation
      See Friction Log #4 for tag ownership inference rationale.
    """
    type: str
    signal: str
    metric: str
    current_value: Optional[float]
    threshold: Optional[float]
    status: str
    tags: list[str]


# ---------------------------------------------------------------------------
# Sub-structures — derived by deterministic nodes
# ---------------------------------------------------------------------------

class CloudProviderStatus(TypedDict):
    """
    Result of a cloud provider status check.

    status values:
      OPERATIONAL   no active incidents
      DEGRADED      partial service degradation
      OUTAGE        significant service disruption
      UNKNOWN       feed unavailable or Azure (auth required)

    See ADR-009 for provider implementation details.
    See Friction Log #1 for Azure authentication limitation.
    """
    provider: str                       # "aws", "gcp", "azure"
    region: Optional[str]
    az: Optional[str]                   # best-effort — not available
                                        # on any public status page
    status: str
    affected_services: list[str]
    incident_url: Optional[str]
    checked_at: str                     # ISO 8601 timestamp
    note: Optional[str]                 # explanation if UNKNOWN


# ---------------------------------------------------------------------------
# Sub-structures — produced by LLM nodes
# ---------------------------------------------------------------------------

class SignalAssessment(TypedDict):
    """
    Per-signal assessment produced by triage_firing_signals.

    role taxonomy (ADR-011):
      PRIMARY       signal is the primary driver of SLO burn
      CONTRIBUTING  signal is independently degraded, adding to burn
      UPSTREAM      signal is causing another signal to degrade
                    e.g. saturation causing latency spike
      DOWNSTREAM    signal is a symptom of another signal
                    e.g. latency spike caused by saturation

    UPSTREAM/DOWNSTREAM requires Claude to reason about causal
    direction — a harder task than simple classification.
    Confidence may be lower on complex multi-signal incidents.

    with_structured_output pattern used for all LLM nodes:
      Reference: https://python.langchain.com/docs/how-to/structured_output/
    """
    signal: str
    role: str
    current_value: float
    threshold: float
    deviation_factor: float             # current_value / threshold
    observation: str                    # Claude's one-line assessment


class SignalCorrelation(TypedDict):
    """
    Output of triage_firing_signals node.
    Option C design — structured data + narrative explanation.

    failure_pattern examples:
      "resource exhaustion"
      "upstream dependency failure"
      "cascading latency degradation"
      "synthetic flap — SLO healthy"
    """
    signal_assessments: list[SignalAssessment]
    failure_pattern: str
    correlation_narrative: str


class RemediationStep(TypedDict):
    """
    A single remediation step produced by generate_remediation.

    responsible_teams and downstream_impact are inferred by Claude
    from all available tag values and notification literals without
    requiring a rigid tag schema. See Friction Log #4.

    actions uses pipe separator to avoid ambiguity with commas
    inside action descriptions:
      "Check CPU on prod-db-01 | Scale deployment | Monitor burn rate"
    """
    actions: str                        # pipe-separated action list
    responsible_teams: list[str]        # inferred from owner:/team: tags
                                        # and notification literals
    downstream_impact: list[str]        # inferred from downstream: tags
    urgency: str                        # "immediate", "short-term",
                                        # "monitor"
    rationale: str


class RemediationPlan(TypedDict):
    """
    Output of generate_remediation node.
    MTTC-focused — includes failover options when cloud outage confirmed.
    See ADR-009 for cloud outage severity behavior.
    """
    steps: list[RemediationStep]
    includes_failover: bool
    estimated_resolution_minutes: Optional[int]


class IncidentSummary(TypedDict):
    """
    Final structured output of draft_summary node.
    Ready for Slack or PagerDuty consumption.
    Surfaces normalization_warnings so the on-call engineer is
    aware of any schema translation uncertainty.
    """
    title: str
    severity: str                       # "P1", "P2", "P3", "P4"
    severity_justification: str
    service: str
    budget_state: str
    time_to_exhaustion_minutes: Optional[float]
    firing_signals: list[str]
    failure_pattern: str
    cloud_provider_impact: Optional[str]
    responsible_teams: list[str]
    downstream_impact: list[str]
    recommended_steps: list[str]        # flattened from remediation_plan
    includes_failover: bool
    normalization_warnings: list[str]
    summary_narrative: str
    created_at: str                     # ISO 8601 timestamp


# ---------------------------------------------------------------------------
# Main agent state
# ---------------------------------------------------------------------------

class IncidentState(TypedDict):
    """
    Single state object that flows between all nodes in the graph.
    Each node receives the full state and returns a partial dict
    of only the fields it writes. LangGraph merges the returned
    dict into the existing state automatically.

    Reducer annotation on normalization_warnings:
      ingest_incident and normalize_incident both append warnings.
      Without operator.add, the second write overwrites the first.
      operator.add concatenates the lists instead.

      Reference: https://langchain-ai.github.io/langgraph/reference/graphs/
      Pattern:   Annotated[list[str], operator.add]

    See ADR-011 for the full graph topology and node responsibility map.
    """

    # ------------------------------------------------------------------
    # Raw input — written by ingest_incident
    # Source: POST /triage request payload (ADR-013)
    # ------------------------------------------------------------------
    incident_id: str
    triggered_at: str                   # ISO 8601 timestamp from payload
    service: str
    raw_slo: SLOPayload
    firing_monitors: list[Monitor]      # canonical values after ingestion
    quiet_monitors: list[Monitor]       # canonical values after ingestion

    # ------------------------------------------------------------------
    # Normalized tags — written by ingest_incident (ADR-007)
    # Unified dict[str, list[str]] across all payload sources.
    # Includes notification literals for ownership inference (Friction #4)
    # ------------------------------------------------------------------
    unified_tags: dict[str, list[str]]

    # ------------------------------------------------------------------
    # Normalization state — written by ingest_incident
    # has_unknown_values drives the conditional edge to normalize_incident
    # normalization_warnings uses operator.add reducer — both
    # ingest_incident and normalize_incident append to this list
    # without overwriting each other's entries
    # ------------------------------------------------------------------
    has_unknown_values: bool
    normalization_warnings: Annotated[list[str], operator.add]

    # ------------------------------------------------------------------
    # Derived metrics — written by assess_slo_impact (ADR-006)
    # Deterministic calculations, no LLM call
    # ------------------------------------------------------------------
    time_to_exhaustion_minutes: Optional[float]
    urgency_score: str                  # "HIGH", "MEDIUM", "LOW"
    budget_state: str                   # "HEALTHY", "DEGRADED",
                                        # "EXHAUSTED", "DEBT"

    # ------------------------------------------------------------------
    # Cloud provider status — written by check_cloud_status (ADR-009)
    # Empty list if no cloud:* tag present (node skipped)
    # ------------------------------------------------------------------
    cloud_provider_statuses: list[CloudProviderStatus]

    # ------------------------------------------------------------------
    # Signal correlation — written by triage_firing_signals
    # ------------------------------------------------------------------
    signal_correlation: Optional[SignalCorrelation]

    # ------------------------------------------------------------------
    # Severity classification — written by classify_severity
    # ------------------------------------------------------------------
    severity: Optional[str]             # "P1", "P2", "P3", "P4"
    severity_justification: Optional[str]

    # ------------------------------------------------------------------
    # Runbook steps — written by query_runbook
    # ------------------------------------------------------------------
    runbook_steps: Optional[list[str]]

    # ------------------------------------------------------------------
    # Remediation plan — written by generate_remediation
    # ------------------------------------------------------------------
    remediation_plan: Optional[RemediationPlan]

    # ------------------------------------------------------------------
    # Final summary — written by draft_summary
    # ------------------------------------------------------------------
    incident_summary: Optional[IncidentSummary]

    # ------------------------------------------------------------------
    # Reserved for future — historical pattern detection (ADR-008)
    # None in v1 — populated by enrichment layer in future iterations
    # ------------------------------------------------------------------
    recent_history: Optional[list]
