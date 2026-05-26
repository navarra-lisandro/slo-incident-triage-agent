"""
agent/nodes/llm.py

LLM nodes for the SLO incident triage agent.
All nodes in this module make at least one Claude API call.

Nodes:
  normalize_incident      conditional — resolve unknown field values
  triage_firing_signals   correlate firing signals, identify pattern
  classify_severity       P1-P4 judgment with justification
  query_runbook           extract relevant runbook steps
  generate_remediation    MTTC-focused action plan
  draft_summary           structured output for Slack/PagerDuty

Private context builders are co-located with this module.
All Pydantic output schemas live in schemas.py.

LLM client pattern:
  Reference: https://reference.langchain.com/python/integrations/
             langchain_anthropic/ChatAnthropic/

with_structured_output pattern:
  Reference: https://python.langchain.com/docs/how-to/structured_output/

SystemMessage + HumanMessage pattern:
  Reference: https://reference.langchain.com/python/integrations/
             langchain_anthropic/ChatAnthropic/

Design decisions:
  ADR-006   deterministic vs LLM node separation
  ADR-009   cloud outage informs but never caps severity
  ADR-011   graph topology and signal role taxonomy
  ADR-012   runbook architecture
"""
from datetime import datetime, timezone
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from agent.state import IncidentState
from agent.tools import read_runbook
from agent.nodes.schemas import (
    NormalizedField,
    SignalCorrelationOutput,
    SeverityOutput,
    RunbookStepsOutput,
    RemediationPlanOutput,
    IncidentSummaryOutput,
)

from dotenv import load_dotenv

# load_dotenv must be called before importing agent modules
# Reference: options-income-advisor-agent friction log — load_dotenv timing
load_dotenv()

# ---------------------------------------------------------------------------
# LLM client — shared across all LLM nodes
# temperature=0 for deterministic, consistent reasoning
# Reference: https://reference.langchain.com/python/integrations/
#            langchain_anthropic/ChatAnthropic/
# ---------------------------------------------------------------------------
llm = ChatAnthropic(
    model="claude-sonnet-4-6",
    temperature=0,
)


# ---------------------------------------------------------------------------
# Valid canonical values — used by normalize_incident
# ---------------------------------------------------------------------------

_NORMALIZE_VALID_VALUES: dict[str, list[str]] = {
    "status":  ["firing", "healthy"],
    "type":    ["performance", "synthetic"],
    "signal":  [
        "latency", "errors", "saturation",
        "traffic", "synthetic_check"
    ],
}


# ---------------------------------------------------------------------------
# Private helpers — normalize_incident
# ---------------------------------------------------------------------------

def _resolve_unknown_field(
    field_name: str,
    raw_value: str,
    metric: str,
    monitor_context: dict,
    structured_llm: Any
) -> tuple[str, str | None]:
    """
    Ask Claude to resolve a single unknown field value to a canonical value.
    Returns (canonical_value, warning_or_None).

    LOW confidence or "unknown" result appends a warning.
    Exception falls back to raw value — never halts the graph.
    """
    valid_options = _NORMALIZE_VALID_VALUES.get(field_name, [])

    prompt = (
        f"You are normalizing a monitoring alert payload to a canonical schema.\n\n"
        f"Field: {field_name}\n"
        f"Raw value from provider: '{raw_value}'\n"
        f"Monitor metric: '{metric}'\n"
        f"Valid canonical values: {valid_options}\n\n"
        f"Additional monitor context:\n"
        f"  type: {monitor_context.get('type', 'unknown')}\n"
        f"  signal: {monitor_context.get('signal', 'unknown')}\n"
        f"  status: {monitor_context.get('status', 'unknown')}\n\n"
        f"Map the raw value to the most appropriate canonical value. "
        f"If you cannot confidently map it, use 'unknown'."
    )

    try:
        result: NormalizedField = structured_llm.invoke(prompt)

        if result.confidence == "LOW" or result.canonical_value == "unknown":
            return result.canonical_value, (
                f"low confidence normalization on metric '{metric}' "
                f"field '{field_name}': '{raw_value}' -> "
                f"'{result.canonical_value}' ({result.reasoning})"
            )

        return result.canonical_value, None

    except Exception as e:
        return raw_value, (
            f"normalization failed on metric '{metric}' "
            f"field '{field_name}': '{raw_value}' — "
            f"{type(e).__name__}: keeping raw value"
        )


def _normalize_monitors(
    monitors: list[dict],
    structured_llm: Any
) -> tuple[list[dict], list[str]]:
    """
    Resolve unknown field values in a list of monitors using Claude.
    Skips fields already at canonical values.
    Returns (normalized_monitors, warnings).
    """
    resolved: list[dict] = []
    warnings: list[str] = []

    for monitor in monitors:
        normalized = dict(monitor)
        metric = monitor.get("metric", "unknown")

        for field_name in ["status", "type", "signal"]:
            raw_value = str(monitor.get(field_name, "")).lower().strip()
            if raw_value in _NORMALIZE_VALID_VALUES.get(field_name, []):
                continue

            canonical, warning = _resolve_unknown_field(
                field_name, raw_value, metric, monitor, structured_llm
            )
            normalized[field_name] = canonical
            if warning:
                warnings.append(warning)

        resolved.append(normalized)

    return resolved, warnings


# ---------------------------------------------------------------------------
# Private helpers — triage_firing_signals
# ---------------------------------------------------------------------------

def _build_triage_context(state: IncidentState) -> str:
    """
    Assemble the incident context string for the triage_firing_signals prompt.
    Includes SLO state, derived metrics, firing monitors, quiet monitors,
    and cloud provider status if available.
    """
    slo = state["raw_slo"]
    lines = [
        f"SERVICE: {state['service']}",
        f"INCIDENT ID: {state['incident_id']}",
        f"TRIGGERED AT: {state['triggered_at']}",
        "",
        "SLO STATE:",
        f"  name: {slo['name']}",
        f"  target: {slo['target_pct']}%",
        f"  burn rate: {slo['burn_rate']}x",
        f"  error budget remaining: {slo['error_budget_remaining_pct']}%",
        f"  window: {slo.get('window_seconds', 3600)} seconds",
        "",
        "DERIVED METRICS:",
        f"  budget_state: {state.get('budget_state', 'unknown')}",
        f"  urgency_score: {state.get('urgency_score', 'unknown')}",
        f"  time_to_exhaustion_minutes: {state.get('time_to_exhaustion_minutes', 'N/A')}",
        "",
        "FIRING MONITORS (hot signals):",
    ]

    for m in state.get("firing_monitors", []):
        lines.append(
            f"  [{m['signal'].upper()}] {m['metric']} = {m.get('current_value')} "
            f"(threshold: {m.get('threshold')}) status: {m['status']}"
        )

    lines.append("")
    lines.append("QUIET MONITORS (healthy signals — context only):")
    for m in state.get("quiet_monitors", []):
        lines.append(f"  [{m['signal'].upper()}] {m['metric']} status: {m['status']}")

    cloud_statuses = state.get("cloud_provider_statuses", [])
    if cloud_statuses:
        lines.append("")
        lines.append("CLOUD PROVIDER STATUS:")
        for cs in cloud_statuses:
            lines.append(
                f"  {cs['provider'].upper()} "
                f"({cs.get('region', 'global')}): {cs['status']}"
            )
            if cs.get("affected_services"):
                lines.append(f"    affected: {', '.join(cs['affected_services'])}")
            if cs.get("note"):
                lines.append(f"    note: {cs['note']}")

    tags = state.get("unified_tags", {})
    if tags:
        lines.append("")
        lines.append("UNIFIED TAGS:")
        for key, values in tags.items():
            lines.append(f"  {key}: {', '.join(values)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM NODES
# ---------------------------------------------------------------------------

def normalize_incident(state: IncidentState) -> dict[str, Any]:
    """
    Conditional LLM node. Only invoked when ingest_incident sets
    has_unknown_values = True.

    Resolves unknown field values to canonical schema values using Claude.
    Only processes fields not already resolved by the translation table.

    Uses with_structured_output with NormalizedField Pydantic schema.
    Reference: https://python.langchain.com/docs/how-to/structured_output/

    Writes to state:
      firing_monitors         unknown values resolved
      quiet_monitors          unknown values resolved
      normalization_warnings  appended via operator.add reducer
    """
    structured_llm = llm.with_structured_output(NormalizedField)

    resolved_firing, firing_warnings = _normalize_monitors(
        state.get("firing_monitors", []), structured_llm
    )
    resolved_quiet, quiet_warnings = _normalize_monitors(
        state.get("quiet_monitors", []), structured_llm
    )

    return {
        "firing_monitors": resolved_firing,
        "quiet_monitors": resolved_quiet,
        "normalization_warnings": firing_warnings + quiet_warnings,
    }


def triage_firing_signals(state: IncidentState) -> dict[str, Any]:
    """
    First Claude reasoning node. Receives the full normalized state
    including derived metrics and cloud provider status.

    Produces per-signal structured assessments, identifies the
    cross-signal failure pattern, and writes a correlation narrative.
    Option C design: structured data + narrative (ADR-011).

    Uses with_structured_output with SignalCorrelationOutput schema.
    Reference: https://python.langchain.com/docs/how-to/structured_output/

    Writes to state:
      signal_correlation    SignalCorrelation (as dict for TypedDict state)
    """
    structured_llm = llm.with_structured_output(SignalCorrelationOutput)

    system_prompt = """You are a senior SRE performing incident triage.
Your job is to analyze the firing signals contributing to an SLO burn rate
alert and produce a structured correlation analysis.

For each firing monitor, assess:
  - Its role in the incident (PRIMARY, CONTRIBUTING, UPSTREAM, DOWNSTREAM)
  - How far it has deviated from its threshold
  - A one-sentence observation about its behavior

Then identify the cross-signal failure pattern and write a 2-3 sentence
narrative explaining how the signals relate to each other and why the
SLO is burning.

Key reasoning principles:
  - UPSTREAM signals cause other signals to degrade
    (e.g. saturation causing latency)
  - DOWNSTREAM signals are symptoms of upstream causes
    (e.g. latency caused by saturation)
  - Quiet monitors are context — knowing what is NOT broken
    is half the diagnostic picture
  - Cloud provider degradation informs but does not determine root cause
  - The SLO budget_state and urgency_score provide the severity context

Be precise and concise. Your output feeds directly into severity
classification and remediation planning."""

    context = _build_triage_context(state)
    result: SignalCorrelationOutput = structured_llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Analyze this incident:\n\n{context}"),
    ])

    return {
        "signal_correlation": {
            "signal_assessments": [
                {
                    "signal": a.signal,
                    "role": a.role,
                    "current_value": a.current_value,
                    "threshold": a.threshold,
                    "deviation_factor": a.deviation_factor,
                    "observation": a.observation,
                }
                for a in result.signal_assessments
            ],
            "failure_pattern": result.failure_pattern,
            "correlation_narrative": result.correlation_narrative,
        }
    }


def classify_severity(state: IncidentState) -> dict[str, Any]:
    """
    Classifies incident severity as P1-P4 with written justification.
    Cloud provider outage informs but never caps severity (ADR-009).

    Severity guidance (ADR-011):
      P1    burn_rate >= 14.4x OR budget exhausted/debt
      P2    burn_rate >= 5x, budget degraded
      P3    burn_rate < 5x, single signal degraded
      P4    burn_rate < 2x, no customer impact

    Uses with_structured_output with SeverityOutput schema.
    Reference: https://python.langchain.com/docs/how-to/structured_output/

    Writes to state:
      severity              P1 / P2 / P3 / P4
      severity_justification str
    """
    structured_llm = llm.with_structured_output(SeverityOutput)

    correlation = state.get("signal_correlation", {})
    context = _build_triage_context(state)

    system_prompt = """You are a senior SRE classifying incident severity.

Severity definitions:
  P1    burn_rate >= 14.4x OR error budget exhausted/in debt
        customer-facing impact confirmed. Page immediately.
  P2    burn_rate >= 5x, budget degraded (>0% remaining)
        risk of SLO breach within hours. Investigate urgently.
  P3    burn_rate < 5x, single signal degraded, SLO healthy
        monitoring warranted. No immediate page required.
  P4    burn_rate < 2x, no customer impact
        noise or transient spike. Log and monitor.

Important principles:
  - Cloud provider outages inform but never cap severity
  - The service may still be down from the customer perspective
    even if a provider is at fault
  - Failover options exist even during provider outages
  - A healthy SLO with a single degraded signal warrants P3 not P1
  - Synthetic flaps with healthy SLO and low burn rate warrant P4

Provide a 2-3 sentence justification referencing specific values."""

    result: SeverityOutput = structured_llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(
            content=(
                f"Classify the severity of this incident.\n\n"
                f"Signal correlation analysis:\n"
                f"  failure_pattern: {correlation.get('failure_pattern', 'unknown')}\n"
                f"  narrative: {correlation.get('correlation_narrative', 'none')}\n\n"
                f"Full incident context:\n{context}"
            )
        ),
    ])

    return {
        "severity": result.severity,
        "severity_justification": result.justification,
    }


def query_runbook(state: IncidentState) -> dict[str, Any]:
    """
    Reads the service runbook using the read_runbook tool and extracts
    relevant steps based on the current firing signals and failure pattern.

    Claude does not invent steps — it reads the runbook, identifies
    applicable sections based on signal correlation context, and
    synthesizes a prioritized action list adapted to the specific incident.
    See ADR-012 for runbook architecture rationale.

    Uses with_structured_output with RunbookStepsOutput schema.
    Reference: https://python.langchain.com/docs/how-to/structured_output/

    Writes to state:
      runbook_steps   list[str] — prioritized steps from runbook
    """
    structured_llm = llm.with_structured_output(RunbookStepsOutput)

    service = state["service"]
    runbook_content = read_runbook.invoke({"service": service})
    correlation = state.get("signal_correlation", {})

    system_prompt = """You are a senior SRE extracting relevant runbook steps
for an active incident.

Your job is NOT to invent steps — read the provided runbook and extract
only the steps relevant to the current firing signals and failure pattern.

Prioritization rules:
  - Steps for the PRIMARY signal come first
  - Steps for UPSTREAM signals before DOWNSTREAM signals
  - Immediate actions before escalation steps
  - Most critical steps at the top

Return only steps from the runbook. If no runbook exists or no steps
are relevant, say so clearly."""

    result: RunbookStepsOutput = structured_llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(
            content=(
                f"Service: {service}\n"
                f"Severity: {state.get('severity', 'unknown')}\n"
                f"Failure pattern: {correlation.get('failure_pattern', 'unknown')}\n"
                f"Firing signals: "
                f"{[m['signal'] for m in state.get('firing_monitors', [])]}\n\n"
                f"Signal correlation:\n"
                f"{correlation.get('correlation_narrative', 'none')}\n\n"
                f"Runbook:\n{runbook_content}"
            )
        ),
    ])

    return {"runbook_steps": result.steps}


def generate_remediation(state: IncidentState) -> dict[str, Any]:
    """
    Synthesizes a MTTC-focused remediation plan from all upstream context.
    When a cloud provider outage is confirmed, explicitly includes
    failover and degradation options the team controls (ADR-009).

    Ownership inference (Friction Log #4):
      Infers responsible_teams and downstream_impact from all available
      tag values and notification literals without requiring rigid schema.

    Uses with_structured_output with RemediationPlanOutput schema.
    Reference: https://python.langchain.com/docs/how-to/structured_output/

    Writes to state:
      remediation_plan    RemediationPlan
    """
    structured_llm = llm.with_structured_output(RemediationPlanOutput)

    correlation = state.get("signal_correlation", {})
    cloud_statuses = state.get("cloud_provider_statuses", [])
    cloud_degraded = any(
        cs["status"] in ("DEGRADED", "OUTAGE")
        for cs in cloud_statuses
    )

    context = _build_triage_context(state)
    tags = state.get("unified_tags", {})

    system_prompt = """You are a senior SRE generating a MTTC-focused
remediation plan for an active incident.

MTTC (Mean Time To Clue) is the goal — give the on-call engineer
the fastest path to understanding and resolving the incident.

Remediation plan rules:
  - Steps ordered by urgency: immediate first, then short-term, then monitor
  - Each step has pipe-separated actions, responsible teams, downstream impact
  - Infer responsible teams from available tags and notification literals:
      owner:, team:, notify:, pagerduty: tags and email/Slack handles
  - If cloud provider is degraded or in outage, include failover options
    the team controls — even during provider outages, options exist:
      moving to another region, promoting read replicas, enabling
      circuit breakers, graceful degradation modes
  - Never say "wait for provider to resolve" as the only step
  - Be specific — reference the service name and signal values
  - Estimate resolution time based on the failure pattern and severity"""

    result: RemediationPlanOutput = structured_llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(
            content=(
                f"Generate a remediation plan for this incident.\n\n"
                f"Severity: {state.get('severity', 'unknown')}\n"
                f"Failure pattern: {correlation.get('failure_pattern', 'unknown')}\n"
                f"Cloud provider degraded: {cloud_degraded}\n\n"
                f"Runbook steps:\n"
                f"{chr(10).join(state.get('runbook_steps', []))}\n\n"
                f"Available tags for ownership inference:\n"
                f"{tags}\n\n"
                f"Full incident context:\n{context}"
            )
        ),
    ])

    return {
        "remediation_plan": {
            "steps": [
                {
                    "actions": s.actions,
                    "responsible_teams": s.responsible_teams,
                    "downstream_impact": s.downstream_impact,
                    "urgency": s.urgency,
                    "rationale": s.rationale,
                }
                for s in result.steps
            ],
            "includes_failover": result.includes_failover,
            "estimated_resolution_minutes": result.estimated_resolution_minutes,
        }
    }


def draft_summary(state: IncidentState) -> dict[str, Any]:
    """
    Produces the final structured incident summary ready for
    Slack or PagerDuty consumption.

    Surfaces normalization_warnings so the on-call engineer is
    aware of any schema translation uncertainty from ingestion.

    Uses with_structured_output with IncidentSummaryOutput schema.
    Reference: https://python.langchain.com/docs/how-to/structured_output/

    Writes to state:
      incident_summary    IncidentSummary
    """
    structured_llm = llm.with_structured_output(IncidentSummaryOutput)

    correlation = state.get("signal_correlation", {})
    remediation = state.get("remediation_plan", {})
    cloud_statuses = state.get("cloud_provider_statuses", [])
    warnings = state.get("normalization_warnings", [])

    cloud_impact = None
    if cloud_statuses:
        degraded = [
            cs for cs in cloud_statuses
            if cs["status"] in ("DEGRADED", "OUTAGE")
        ]
        if degraded:
            parts = [
                f"{cs['provider'].upper()} {cs['status']}"
                f"{' (' + ', '.join(cs['affected_services']) + ')' if cs['affected_services'] else ''}"
                for cs in degraded
            ]
            cloud_impact = "; ".join(parts)

    # flatten remediation steps for summary
    flat_steps: list[str] = []
    for step in remediation.get("steps", []):
        flat_steps.extend(step["actions"].split(" | "))

    system_prompt = """You are a senior SRE writing an incident summary
for the on-call engineer. This summary will appear in Slack and PagerDuty.

Write for someone who just woke up at 2am. Be clear, direct, and actionable.

Summary rules:
  - Title: one line, service name + failure pattern + severity
  - narrative: 3-4 sentences covering what is happening,
    why the SLO is burning, what signals are firing,
    and the recommended immediate action
  - Use the pre-calculated values from the incident context
  - Surface normalization warnings if present so the engineer
    knows about any schema translation uncertainty"""

    context = _build_triage_context(state)
    result: IncidentSummaryOutput = structured_llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(
            content=(
                f"Write an incident summary.\n\n"
                f"Severity: {state.get('severity')}\n"
                f"Severity justification: {state.get('severity_justification')}\n"
                f"Service: {state['service']}\n"
                f"Budget state: {state.get('budget_state')}\n"
                f"Time to exhaustion: {state.get('time_to_exhaustion_minutes')} minutes\n"
                f"Failure pattern: {correlation.get('failure_pattern')}\n"
                f"Correlation narrative: {correlation.get('correlation_narrative')}\n"
                f"Cloud impact: {cloud_impact or 'none'}\n"
                f"Remediation includes failover: {remediation.get('includes_failover', False)}\n"
                f"Normalization warnings: {warnings or 'none'}\n\n"
                f"Recommended steps:\n"
                f"{chr(10).join(flat_steps)}\n\n"
                f"Full context:\n{context}"
            )
        ),
    ])

    return {
        "incident_summary": {
            "title": result.title,
            "severity": result.severity,
            "severity_justification": result.severity_justification,
            "service": result.service,
            "budget_state": result.budget_state,
            "time_to_exhaustion_minutes": result.time_to_exhaustion_minutes,
            "firing_signals": result.firing_signals,
            "failure_pattern": result.failure_pattern,
            "cloud_provider_impact": cloud_impact,
            "responsible_teams": result.responsible_teams,
            "downstream_impact": result.downstream_impact,
            "recommended_steps": result.recommended_steps,
            "includes_failover": result.includes_failover,
            "normalization_warnings": warnings,
            "summary_narrative": result.summary_narrative,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    }
