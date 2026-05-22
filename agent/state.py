"""
agent/state.py

Agent state definition for the SLO incident triage agent.
This is the single data structure that flows between all nodes in the graph.

Node responsibility map:
  ingest_incident         writes: normalized_slo, firing_monitors,
                                  quiet_monitors, unified_tags,
                                  has_unknown_values,
                                  normalization_warnings
  normalize_incident      writes: updates firing_monitors and
                                  quiet_monitors with canonical values
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

Design decisions documented in:
  ADR-006   deterministic vs LLM node separation
  ADR-007   tag normalization strategy
  ADR-008   historical pattern detection exclusion
  ADR-009   cloud provider status check
  ADR-010   payload schema design
  ADR-011   graph topology
"""

from typing import Optional
from typing_extensions import TypedDict


# ---------------------------------------------------------------------------
# Sub-structures — input payload
# ---------------------------------------------------------------------------

class SLOPayload(TypedDict):
    """
    Normalized SLO fields from the incoming webhook payload.
    Modeled on Datadog monitor-based SLO webhooks.
    See ADR-010 for provider translation tables.
    """
    name: str
    target_pct: float
    burn_rate: float                    # required — see Friction Log #2
    error_budget_remaining_pct: float
    window_seconds: int                 # canonical internal format
                                        # 1h fast burn  = 3600
                                        # 6h slow burn  = 21600


class Monitor(TypedDict):
    """
    A single constituent monitor — either firing or quiet.
    Field values use canonical internal schema after ingest_incident.
    See ADR-010 for translation tables.
    """
    type: str                           # "performance" or "synthetic"
    signal: str                         # "latency", "errors", "saturation",
                                        # "traffic", "synthetic_check"
    metric: str                         # e.g. "p99_response_ms"
    current_value: Optional[float]
    threshold: Optional[float]
    status: str                         # "firing" or "healthy"
    tags: list[str]                     # raw tags from this monitor
                                        # includes notification literals
                                        # e.g. notify:@team@company.com


# ---------------------------------------------------------------------------
# Sub-structures — derived by deterministic nodes
# ---------------------------------------------------------------------------

class CloudProviderStatus(TypedDict):
    """
    Result of a cloud provider status check.
    See ADR-009 for provider implementation details.
    """
    provider: str                       # "aws", "gcp", "azure"
    region: Optional[str]
    az: Optional[str]                   # best-effort — not available
                                        # on any public status page
    status: str                         # "OPERATIONAL", "DEGRADED",
                                        # "OUTAGE", "UNKNOWN"
    affected_services: list[str]
    incident_url: Optional[str]
    checked_at: str                     # ISO timestamp
    note: Optional[str]                 # explanation if UNKNOWN
                                        # e.g. Azure auth requirement


# ---------------------------------------------------------------------------
# Sub-structures — produced by LLM nodes
# ---------------------------------------------------------------------------

class SignalAssessment(TypedDict):
    """
    Per-signal assessment produced by triage_firing_signals.

    role taxonomy:
      PRIMARY       signal is the primary driver of SLO burn
      CONTRIBUTING  signal is independently degraded, adding to burn
      UPSTREAM      signal is causing another signal to degrade
                    e.g. saturation causing latency spike
      DOWNSTREAM    signal is a symptom of another signal
                    e.g. latency spike caused by saturation

    Note: UPSTREAM/DOWNSTREAM assignment requires Claude to reason
    about causal direction — a harder task than simple classification.
    Confidence may be lower on complex multi-signal incidents.
    See ADR-011 for role taxonomy rationale.
    """
    signal: str                         # latency, errors, saturation,
                                        # traffic, synthetic_check
    role: str                           # PRIMARY, CONTRIBUTING,
                                        # UPSTREAM, DOWNSTREAM
    current_value: float
    threshold: float
    deviation_factor: float             # current_value / threshold
    observation: str                    # Claude's one-line assessment


class SignalCorrelation(TypedDict):
    """
    Output of triage_firing_signals node.
    Option C design — structured data + narrative explanation.
    Both machine-readable fields AND human-readable narrative.
    """
    signal_assessments: list[SignalAssessment]
    failure_pattern: str                # e.g. "resource exhaustion",
                                        # "upstream dependency failure",
                                        # "cascading latency degradation"
    correlation_narrative: str          # Claude's reasoning in prose


class RemediationStep(TypedDict):
    """
    A single remediation step produced by generate_remediation.

    responsible_teams and downstream_impact are inferred by Claude
    from all available tag values and notification literals
    (owner:, team:, notify:, pagerduty:, downstream:, email addresses,
    Slack handles) without requiring a rigid tag schema.

    This is an explicit design decision — tag ownership inference
    requires human judgment at scale and is offloaded to Claude.
    See Friction Log #4.
    """
    actions: str                        # pipe-separated action list
                                        # "Check CPU | Scale deployment"
    responsible_teams: list[str]        # inferred from owner:/team: tags
                                        # and notification literals
    downstream_impact: list[str]        # inferred from downstream: tags
                                        # and service dependencies
    urgency: str                        # "immediate", "short-term",
                                        # "monitor"
    rationale: str                      # why this step is recommended


class RemediationPlan(TypedDict):
    """
    Output of generate_remediation node.
    MTTC-focused — includes failover options when cloud outage confirmed.
    See ADR-009 for cloud outage severity behavior.
    """
    steps: list[RemediationStep]
    includes_failover: bool             # True if cloud outage informed
                                        # failover recommendations
    estimated_resolution_minutes: Optional[int]


class IncidentSummary(TypedDict):
    """
    Final structured output of draft_summary node.
    Ready for Slack / PagerDuty consumption.
    Surfaces normalization_warnings if any values were flagged
    during ingestion.
    """
    title: str
    severity: str                       # "P1", "P2", "P3", "P4"
    severity_justification: str
    service: str
    budget_state: str                   # HEALTHY/DEGRADED/EXHAUSTED/DEBT
    time_to_exhaustion_minutes: Optional[float]
    firing_signals: list[str]
    failure_pattern: str
    cloud_provider_impact: Optional[str]
    responsible_teams: list[str]
    downstream_impact: list[str]
    recommended_steps: list[str]        # flattened from remediation_plan
    includes_failover: bool
    normalization_warnings: list[str]   # surfaced from ingestion
    summary_narrative: str
    created_at: str                     # ISO timestamp


# ---------------------------------------------------------------------------
# Main agent state
# ---------------------------------------------------------------------------

class IncidentState(TypedDict):
    """
    Single state object that flows between all nodes in the graph.
    Every field is written by exactly one node and read by one or
    more downstream nodes.

    See ADR-011 for the full graph topology and node responsibility map.
    """

    # ------------------------------------------------------------------
    # Raw input — written by ingest_incident
    # Source: POST /triage request payload
    # ------------------------------------------------------------------
    incident_id: str
    triggered_at: str                   # ISO timestamp from payload
    service: str                        # e.g. "checkout-service"
    raw_slo: SLOPayload
    firing_monitors: list[Monitor]      # canonical values after ingestion
    quiet_monitors: list[Monitor]       # canonical values after ingestion

    # ------------------------------------------------------------------
    # Normalized tags — written by ingest_incident (ADR-007)
    # Unified dict[str, list[str]] across all payload sources
    # Includes notification literals inferred for ownership (Friction #4)
    # ------------------------------------------------------------------
    unified_tags: dict[str, list[str]]

    # ------------------------------------------------------------------
    # Normalization state — written by ingest_incident
    # Drives conditional edge to normalize_incident node
    # ------------------------------------------------------------------
    has_unknown_values: bool
    normalization_warnings: list[str]   # populated by ingest_incident
                                        # and normalize_incident
                                        # surfaced in draft_summary

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
