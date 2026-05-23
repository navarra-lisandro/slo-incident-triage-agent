"""
agent/nodes.py

Node implementations for the SLO incident triage agent graph.

Nodes are organized into two categories per ADR-006:

  DETERMINISTIC NODES (no LLM call)
    ingest_incident         parse payload, apply translation table,
                            unify tags, flag unknown values
    normalize_incident      resolve unknown values (conditional LLM node)
    assess_slo_impact       burn rate calculations
    check_cloud_status      fetch provider status pages

  LLM NODES (Claude reasoning)
    triage_firing_signals   correlate signals, identify failure pattern
    classify_severity       P1-P4 judgment with justification
    query_runbook           match signal pattern to runbook steps
    generate_remediation    MTTC-focused action plan
    draft_summary           structured output for Slack/PagerDuty

Each node receives the full IncidentState and returns a dict of
the fields it writes to state. LangGraph merges the returned dict
into the existing state automatically.

Design decisions:
  ADR-006   deterministic vs LLM node separation
  ADR-007   tag normalization strategy
  ADR-009   cloud provider status check
  ADR-010   payload schema design and translation tables
  ADR-011   graph topology and node responsibility map
  ADR-012   runbook architecture
"""

import os
import json
import httpx
from datetime import datetime, timezone
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from agent.state import (
    IncidentState,
    Monitor,
    CloudProviderStatus,
    SignalAssessment,
    SignalCorrelation,
    RemediationStep,
    RemediationPlan,
    IncidentSummary,
)
from agent.tools import read_runbook


# ---------------------------------------------------------------------------
# LLM client — shared across all LLM nodes
# ---------------------------------------------------------------------------

# TODO: move model name to environment variable
llm = ChatAnthropic(model="claude-sonnet-4-20250514")


# ---------------------------------------------------------------------------
# Translation tables — used by ingest_incident (ADR-010)
# ---------------------------------------------------------------------------

# TODO: expand with additional providers as needed
STATUS_TRANSLATION: dict[str, str] = {
    # Datadog
    "triggered": "firing",
    "ok": "healthy",
    "warn": "firing",
    "no data": "healthy",
    # Prometheus
    "firing": "firing",
    "resolved": "healthy",
    # Grafana
    "alerting": "firing",
    "pending": "firing",
    "no_data": "healthy",
}

TYPE_TRANSLATION: dict[str, str] = {
    # Datadog
    "apm": "performance",
    "metric": "performance",
    "query": "performance",
    "synthetics": "synthetic",
    "rum": "synthetic",
}

SIGNAL_TRANSLATION: dict[str, str] = {
    # Datadog
    "latency": "latency",
    "errors": "errors",
    "error_rate": "errors",
    "traffic": "traffic",
    "throughput": "traffic",
    "saturation": "saturation",
    "cpu": "saturation",
    "memory": "saturation",
    "synthetic_check": "synthetic_check",
    "rum": "synthetic_check",
}

WINDOW_TRANSLATION: dict[str, int] = {
    # Datadog
    "1h": 3600,
    "6h": 21600,
    # Prometheus / Grafana (pass-through if already int-like string)
    "3600": 3600,
    "21600": 21600,
}

# Cloud provider status feed URLs (ADR-009)
CLOUD_STATUS_FEEDS: dict[str, str] = {
    "aws": "https://status.aws.amazon.com/data.json",
    "gcp": "https://status.cloud.google.com/incidents.json",
    # Azure has no unauthenticated JSON feed (see Friction Log #1)
    "azure": None,
}


# ---------------------------------------------------------------------------
# DETERMINISTIC NODES
# ---------------------------------------------------------------------------

def ingest_incident(state: IncidentState) -> dict[str, Any]:
    """
    Parse the incoming payload, apply translation tables for known
    values, unify tags across all payload sources, and flag unknown
    values for conditional LLM normalization.

    Writes to state:
      firing_monitors         canonical values applied
      quiet_monitors          canonical values applied
      unified_tags            dict[str, list[str]] across all sources
      has_unknown_values      True if any unknown values flagged
      normalization_warnings  list of flagged unknown values

    Translation tables applied (ADR-010):
      STATUS_TRANSLATION      triggered/ok/alerting -> firing/healthy
      TYPE_TRANSLATION        apm/synthetics -> performance/synthetic
      SIGNAL_TRANSLATION      error_rate/cpu -> errors/saturation
      WINDOW_TRANSLATION      "1h"/"6h" -> 3600/21600

    Tag normalization (ADR-007):
      Merges tags from SLO payload, all firing monitors, and all
      quiet monitors into unified dict[str, list[str]].
      Duplicate values within the same key are deduplicated.
      Malformed tags (no colon separator) are logged and skipped.

    TODO: implement translation table application
    TODO: implement tag unification logic
    TODO: implement unknown value detection and flagging
    """
    pass


def normalize_incident(state: IncidentState) -> dict[str, Any]:
    """
    Conditional LLM node. Only invoked when ingest_incident sets
    has_unknown_values = True.

    Resolves unknown field values to canonical schema values using
    Claude. Only processes fields flagged as unknown -- known values
    from the translation table are never re-processed.

    Writes to state:
      firing_monitors         unknown values resolved
      quiet_monitors          unknown values resolved
      normalization_warnings  updated with any values Claude
                              could not confidently resolve

    TODO: implement LLM normalization prompt
    TODO: implement unknown value resolution logic
    TODO: handle Claude uncertainty (set to "unknown", add warning)
    """
    pass


def assess_slo_impact(state: IncidentState) -> dict[str, Any]:
    """
    Pure arithmetic. Derives urgency metrics from burn rate and
    error budget fields. No LLM call.

    Writes to state:
      time_to_exhaustion_minutes  (error_budget_remaining / burn_rate)
                                  * (window_seconds / 60)
                                  None if budget already exhausted
      urgency_score               HIGH / MEDIUM / LOW
                                  HIGH:   burn_rate >= 5x
                                  MEDIUM: burn_rate >= 2x and < 5x
                                  LOW:    burn_rate < 2x
      budget_state                HEALTHY / DEGRADED / EXHAUSTED / DEBT
                                  HEALTHY:   remaining > 50%
                                  DEGRADED:  remaining > 0% and <= 50%
                                  EXHAUSTED: remaining = 0%
                                  DEBT:      remaining < 0%

    TODO: implement time_to_exhaustion calculation
    TODO: implement urgency_score rule logic
    TODO: implement budget_state rule logic
    """
    pass


def check_cloud_status(state: IncidentState) -> dict[str, Any]:
    """
    Conditional deterministic node. Only invoked when a cloud:* tag
    is present in unified_tags. Fetches the provider status feed for
    each cloud tag value and writes CloudProviderStatus objects to state.

    Supports: aws, gcp
    Azure: returns UNKNOWN with note (see Friction Log #1)

    Timeout: 5 seconds per provider request. Failure returns UNKNOWN,
    never raises an exception that halts the graph.

    Writes to state:
      cloud_provider_statuses   list[CloudProviderStatus]
                                empty list if no cloud tag present

    TODO: implement AWS status feed parser
    TODO: implement GCP status feed parser
    TODO: implement Azure UNKNOWN fallback with note
    TODO: implement region filtering from unified_tags
    TODO: enforce 5 second timeout per provider
    """
    pass


# ---------------------------------------------------------------------------
# LLM NODES
# ---------------------------------------------------------------------------

def triage_firing_signals(state: IncidentState) -> dict[str, Any]:
    """
    First Claude reasoning node. Receives the full normalized state
    including derived metrics and cloud provider status.

    Produces a per-signal structured assessment (SignalAssessment),
    identifies the cross-signal failure pattern, and writes a
    correlation narrative. Option C design: structured data + narrative.

    Signal role taxonomy (ADR-011):
      PRIMARY       signal is the primary driver of SLO burn
      CONTRIBUTING  signal is independently degraded, adding to burn
      UPSTREAM      signal is causing another signal to degrade
      DOWNSTREAM    signal is a symptom of another signal

    Writes to state:
      signal_correlation    SignalCorrelation
        signal_assessments  list[SignalAssessment]
        failure_pattern     str
        correlation_narrative str

    TODO: implement system prompt
    TODO: implement state context assembly for prompt
    TODO: implement structured output parsing
    """
    pass


def classify_severity(state: IncidentState) -> dict[str, Any]:
    """
    Classifies incident severity as P1-P4 with written justification.
    Cloud provider outage informs but never caps severity (ADR-009).

    Severity guidance:
      P1    burn_rate >= 14.4x OR budget exhausted/debt
            customer-facing impact confirmed
      P2    burn_rate >= 5x, budget degraded
            risk of SLO breach within hours
      P3    burn_rate < 5x, single signal degraded
            SLO healthy, monitoring warranted
      P4    burn_rate < 2x, no customer impact
            noise, likely synthetic flap or transient spike

    Writes to state:
      severity              P1 / P2 / P3 / P4
      severity_justification str

    TODO: implement system prompt with severity guidance
    TODO: implement state context assembly
    TODO: implement structured output parsing
    """
    pass


def query_runbook(state: IncidentState) -> dict[str, Any]:
    """
    Reads the service runbook using the read_runbook tool and extracts
    relevant steps based on the current firing signals and failure pattern.

    Claude does not invent steps -- it reads the runbook, identifies
    applicable sections based on signal correlation context, and
    synthesizes a prioritized action list adapted to the specific incident.

    See ADR-012 for runbook architecture rationale.

    Writes to state:
      runbook_steps   list[str] -- prioritized steps from runbook

    TODO: implement read_runbook tool call
    TODO: implement prompt that provides signal context for section selection
    TODO: implement step extraction and prioritization logic
    """
    pass


def generate_remediation(state: IncidentState) -> dict[str, Any]:
    """
    Synthesizes a MTTC-focused remediation plan from all upstream context.
    When a cloud provider outage is confirmed, explicitly includes failover
    and degradation options the team controls (ADR-009).

    Ownership inference (Friction Log #4):
      Infers responsible_teams and downstream_impact from all available
      tag values and notification literals without requiring rigid schema.
      Tags considered: owner:, team:, notify:, pagerduty:, downstream:
      plus email addresses and Slack handles in tag values.

    Writes to state:
      remediation_plan    RemediationPlan
        steps             list[RemediationStep]
        includes_failover bool
        estimated_resolution_minutes Optional[int]

    TODO: implement system prompt with MTTC focus
    TODO: implement ownership inference from unified_tags
    TODO: implement failover step injection when cloud outage confirmed
    TODO: implement structured output parsing
    """
    pass


def draft_summary(state: IncidentState) -> dict[str, Any]:
    """
    Produces the final structured incident summary ready for
    Slack or PagerDuty consumption.

    Surfaces normalization_warnings if any values were flagged
    during ingestion so the on-call engineer is aware of
    any schema translation uncertainty.

    Writes to state:
      incident_summary    IncidentSummary

    TODO: implement system prompt
    TODO: implement full state assembly for summary context
    TODO: implement structured output parsing
    TODO: ensure normalization_warnings are surfaced
    """
    pass
