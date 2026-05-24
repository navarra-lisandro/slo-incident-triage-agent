"""
agent/nodes/schemas.py

Pydantic output schemas for all LLM nodes.

Pydantic BaseModel is used instead of TypedDict for LLM output schemas
to get runtime field validation on Claude's structured responses.

with_structured_output pattern:
  Reference: https://python.langchain.com/docs/how-to/structured_output/

Each schema mirrors its corresponding TypedDict in state.py but adds
Field descriptions that guide Claude's structured output generation.
"""

from pydantic import BaseModel, Field


class NormalizedField(BaseModel):
    """
    Claude's resolution of a single unknown monitor field value.
    Used by normalize_incident via with_structured_output.

    confidence values: HIGH, MEDIUM, LOW
    canonical_value:   must be one of the valid values for the field type
                       or "unknown" if Claude cannot confidently resolve
    """
    canonical_value: str = Field(
        description=(
            "The canonical internal value this field should map to. "
            "Must be one of the valid values for this field type."
        )
    )
    confidence: str = Field(
        description="Confidence in the normalization: HIGH, MEDIUM, or LOW"
    )
    reasoning: str = Field(
        description="One sentence explaining the normalization decision"
    )


class SignalAssessmentOutput(BaseModel):
    """
    Claude's assessment of a single firing signal.
    Mirrors SignalAssessment TypedDict in state.py.
    Pydantic used here for runtime validation of Claude's response.

    role taxonomy (ADR-011):
      PRIMARY       signal is the primary driver of SLO burn
      CONTRIBUTING  signal is independently degraded, adding to burn
      UPSTREAM      signal is causing another signal to degrade
      DOWNSTREAM    signal is a symptom of another signal
    """
    signal: str = Field(
        description="Signal type: latency, errors, saturation, traffic, synthetic_check"
    )
    role: str = Field(
        description="Role: PRIMARY, CONTRIBUTING, UPSTREAM, or DOWNSTREAM"
    )
    current_value: float = Field(
        description="Current metric value at time of alert"
    )
    threshold: float = Field(
        description="Configured threshold for this signal"
    )
    deviation_factor: float = Field(
        description="current_value divided by threshold"
    )
    observation: str = Field(
        description="One sentence assessment of this signal's behavior"
    )


class SignalCorrelationOutput(BaseModel):
    """
    Claude's full correlation analysis of all firing signals.
    Mirrors SignalCorrelation TypedDict in state.py.
    Option C design — structured per-signal data + narrative explanation.
    """
    signal_assessments: list[SignalAssessmentOutput] = Field(
        description="Per-signal structured assessment for each firing monitor"
    )
    failure_pattern: str = Field(
        description=(
            "The identified cross-signal failure pattern. "
            "Examples: 'resource exhaustion', 'upstream dependency failure', "
            "'cascading latency degradation', 'synthetic flap — SLO healthy'"
        )
    )
    correlation_narrative: str = Field(
        description=(
            "2-3 sentence prose explanation of how the signals relate "
            "to each other and why the SLO is burning"
        )
    )


class SeverityOutput(BaseModel):
    """
    Claude's severity classification for the incident.
    Mirrors severity and severity_justification fields in state.py.

    Severity guidance (ADR-011):
      P1    burn_rate >= 14.4x OR budget exhausted/debt
            customer-facing impact confirmed
      P2    burn_rate >= 5x, budget degraded
            risk of SLO breach within hours
      P3    burn_rate < 5x, single signal degraded
            SLO healthy, monitoring warranted
      P4    burn_rate < 2x, no customer impact
            noise, likely synthetic flap or transient spike
    """
    severity: str = Field(
        description="Severity classification: P1, P2, P3, or P4"
    )
    justification: str = Field(
        description=(
            "2-3 sentence explanation of the severity classification "
            "referencing specific signal values, burn rate, and budget state"
        )
    )


class RunbookStepsOutput(BaseModel):
    """
    Claude's extracted and prioritized runbook steps.
    Mirrors runbook_steps field in state.py.
    """
    steps: list[str] = Field(
        description=(
            "Ordered list of runbook steps relevant to the current "
            "firing signals and failure pattern. Most critical steps first."
        )
    )
    runbook_sections_used: list[str] = Field(
        description="Which runbook sections were used to extract these steps"
    )


class RemediationStepOutput(BaseModel):
    """
    A single remediation step produced by generate_remediation.
    Mirrors RemediationStep TypedDict in state.py.

    responsible_teams and downstream_impact are inferred by Claude
    from all available tag values and notification literals.
    See Friction Log #4 for tag ownership inference rationale.
    """
    actions: str = Field(
        description=(
            "Pipe-separated list of actions for this step. "
            "Example: 'Check CPU on prod-db-01 | Scale deployment | Monitor burn rate'"
        )
    )
    responsible_teams: list[str] = Field(
        description=(
            "Teams responsible for this step, inferred from owner:, team:, "
            "notify:, pagerduty: tags and notification literals"
        )
    )
    downstream_impact: list[str] = Field(
        description=(
            "Services or teams impacted if this step is not taken, "
            "inferred from downstream: tags and service dependencies"
        )
    )
    urgency: str = Field(
        description="Step urgency: immediate, short-term, or monitor"
    )
    rationale: str = Field(
        description="One sentence explaining why this step is recommended"
    )


class RemediationPlanOutput(BaseModel):
    """
    Claude's full MTTC-focused remediation plan.
    Mirrors RemediationPlan TypedDict in state.py.
    """
    steps: list[RemediationStepOutput] = Field(
        description="Ordered remediation steps, most urgent first"
    )
    includes_failover: bool = Field(
        description="True if cloud provider outage informed failover recommendations"
    )
    estimated_resolution_minutes: int | None = Field(
        description="Estimated time to resolution in minutes, or None if unknown",
        default=None
    )


class IncidentSummaryOutput(BaseModel):
    """
    Claude's final structured incident summary.
    Mirrors IncidentSummary TypedDict in state.py.
    Ready for Slack or PagerDuty consumption.
    """
    title: str = Field(
        description="One-line incident title suitable for a Slack alert or PD incident"
    )
    severity: str = Field(
        description="Severity: P1, P2, P3, or P4"
    )
    severity_justification: str = Field(
        description="One sentence justification for the severity"
    )
    service: str = Field(
        description="The affected service name"
    )
    budget_state: str = Field(
        description="SLO budget state: HEALTHY, DEGRADED, EXHAUSTED, or DEBT"
    )
    time_to_exhaustion_minutes: float | None = Field(
        description="Minutes until error budget is exhausted, or None if already exhausted",
        default=None
    )
    firing_signals: list[str] = Field(
        description="List of signal types currently firing"
    )
    failure_pattern: str = Field(
        description="The identified cross-signal failure pattern"
    )
    cloud_provider_impact: str | None = Field(
        description="Cloud provider impact summary if applicable, else None",
        default=None
    )
    responsible_teams: list[str] = Field(
        description="Teams responsible for responding to this incident"
    )
    downstream_impact: list[str] = Field(
        description="Services or teams impacted by this incident"
    )
    recommended_steps: list[str] = Field(
        description="Flattened prioritized action list from the remediation plan"
    )
    includes_failover: bool = Field(
        description="True if failover options are included in recommended steps"
    )
    normalization_warnings: list[str] = Field(
        description="Any schema translation warnings from payload ingestion",
        default_factory=list
    )
    summary_narrative: str = Field(
        description=(
            "3-4 sentence narrative summary of the incident suitable "
            "for an on-call engineer reading at 2am"
        )
    )
    created_at: str = Field(
        description="ISO 8601 timestamp when this summary was generated"
    )
