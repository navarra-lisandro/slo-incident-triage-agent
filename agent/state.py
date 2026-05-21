"""
agent/state.py

Agent state definition for the SLO incident triage agent.
This is the single data structure that flows between all nodes in the graph.

Structure mirrors commonly used pattern:
- TypedDict for the main state
- Nested TypedDicts for complex sub-structures
- All fields explicitly typed

Node responsibility map:
  ingest_incident         writes: normalized_slo, firing_monitors,
                                  quiet_monitors, unified_tags
  assess_slo_impact       writes: time_to_exhaustion_minutes,
                                  urgency_score, budget_state
  check_cloud_status      writes: cloud_provider_statuses
  triage_firing_signals   writes: signal_correlation  (TBD - design in progress)
  classify_severity       writes: severity, severity_justification
  query_runbook           writes: runbook_steps
  generate_remediation    writes: remediation_plan
  draft_summary           writes: incident_summary
"""

from typing import Optional
from typing_extensions import TypedDict


# ---------------------------------------------------------------------------
# Sub-structures — input payload
# ---------------------------------------------------------------------------

class SLOPayload(TypedDict):
    """Normalized SLO fields from the incoming webhook payload."""
    name: str
    target_pct: float
    burn_rate: float
    error_budget_remaining_pct: float
    window: str                         # "1h" or "6h"


class Monitor(TypedDict):
    """A single constituent monitor — either firing or quiet."""
    type: str                           # "apm" or "synthetics"
    signal: str                         # "latency", "errors", "saturation",
                                        # "traffic", "synthetic_check"
    metric: str                         # e.g. "p99_response_ms"
    current_value: Optional[float]
    threshold: Optional[float]
    status: str                         # "triggered" or "ok"
    tags: list[str]                     # raw tags from this monitor


# ---------------------------------------------------------------------------
# Sub-structures — derived by deterministic nodes
# ---------------------------------------------------------------------------

class CloudProviderStatus(TypedDict):
    """Result of a cloud provider status check (ADR-009)."""
    provider: str                       # "aws", "gcp", "azure"
    region: Optional[str]
    az: Optional[str]                   # best-effort, see ADR-009
    status: str                         # "OPERATIONAL", "DEGRADED",
                                        # "OUTAGE", "UNKNOWN"
    affected_services: list[str]
    incident_url: Optional[str]
    checked_at: str                     # ISO timestamp
    note: Optional[str]                 # explanation if UNKNOWN


# ---------------------------------------------------------------------------
# Sub-structures — produced by LLM nodes (TBD)
# ---------------------------------------------------------------------------

class SignalCorrelation(TypedDict):
    """
    Output of triage_firing_signals node.
    Design in progress — see state design conversation.

    Decided:
      - Option C: structured data + narrative explanation
      - Per-signal assessment (role, deviation)
      - Cross-signal failure pattern
      - Both machine-readable fields AND narrative string

    TBD:
      - Exact field names for per-signal assessment
      - How to represent signal relationships
    """
    # Per-signal structured assessment
    # TBD: list[SignalAssessment] — design not finalized
    signal_assessments: list[dict]      # placeholder until design finalized

    # Cross-signal failure pattern identified by Claude
    failure_pattern: str                # e.g. "resource exhaustion",
                                        # "upstream dependency failure"

    # Narrative explanation — human readable
    correlation_narrative: str          # Claude's reasoning in prose


class RemediationPlan(TypedDict):
    """
    Output of generate_remediation node.
    MTTC-focused — prioritized action steps including failover options
    when cloud provider outage is confirmed (ADR-009).
    """
    # TBD: exact structure of steps
    steps: list[dict]                   # placeholder until design finalized
    includes_failover: bool             # True if cloud outage informed steps
    estimated_resolution_minutes: Optional[int]


class IncidentSummary(TypedDict):
    """
    Final structured output of draft_summary node.
    Ready for Slack / PagerDuty consumption.
    """
    title: str
    severity: str                       # "P1", "P2", "P3", "P4"
    severity_justification: str
    service: str
    budget_state: str
    time_to_exhaustion_minutes: Optional[float]
    firing_signals: list[str]
    cloud_provider_impact: Optional[str]
    recommended_steps: list[str]
    summary_narrative: str
    created_at: str                     # ISO timestamp


# ---------------------------------------------------------------------------
# Main agent state
# ---------------------------------------------------------------------------

class IncidentState(TypedDict):
    """
    Single state object that flows between all nodes in the graph.
    Every field is written by exactly one node and read by one or more
    downstream nodes.

    Fields marked TBD are reserved but design not yet finalized.
    """

    # ------------------------------------------------------------------
    # Raw input — written by ingest_incident
    # Source: POST /triage request payload
    # ------------------------------------------------------------------
    incident_id: str
    triggered_at: str                   # ISO timestamp from payload
    service: str                        # e.g. "checkout-service"
    raw_slo: SLOPayload
    raw_firing_monitors: list[Monitor]
    raw_quiet_monitors: list[Monitor]

    # ------------------------------------------------------------------
    # Normalized tags — written by ingest_incident (ADR-007)
    # Unified dict[str, list[str]] across all payload sources
    # ------------------------------------------------------------------
    unified_tags: dict[str, list[str]]

    # ------------------------------------------------------------------
    # Derived metrics — written by assess_slo_impact (ADR-006)
    # Deterministic calculations, no LLM
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
    # TBD: design in progress
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
    # Reserved for future use — historical pattern detection (ADR-008)
    # None in v1 — populated by enrichment layer in future iterations
    # ------------------------------------------------------------------
    recent_history: Optional[list]
