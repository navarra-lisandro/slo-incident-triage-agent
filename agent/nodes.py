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

import os  # noqa: F401
import json  # noqa: F401
import httpx  # noqa: F401
from datetime import datetime, timezone  # noqa: F401
from typing import Any

from langchain_anthropic import ChatAnthropic  # noqa: F401
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: F401

from agent.state import (
    IncidentState,
    Monitor,  # noqa: F401
    CloudProviderStatus,  # noqa: F401
    SignalAssessment,  # noqa: F401
    SignalCorrelation,  # noqa: F401
    RemediationStep,  # noqa: F401
    RemediationPlan,  # noqa: F401
    IncidentSummary,  # noqa: F401
)
from agent.tools import read_runbook  # noqa: F401


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
# Private helpers — used by ingest_incident
# ---------------------------------------------------------------------------

def _normalize_monitor(raw: dict) -> tuple[dict, bool, list[str]]:
    """
    Apply translation tables to a single monitor dict.
    Returns (normalized_monitor, has_unknown, warnings).

    Translates status, type, and signal values to canonical
    internal schema values per ADR-010.
    """
    warnings: list[str] = []
    has_unknown = False
    normalized = dict(raw)
    metric = raw.get("metric", "unknown")

    # status
    raw_status = str(raw.get("status", "")).lower().strip()
    if raw_status in STATUS_TRANSLATION:
        normalized["status"] = STATUS_TRANSLATION[raw_status]
    else:
        warnings.append(
            f"unknown status value '{raw_status}' on metric "
            f"'{metric}' — flagged for normalization"
        )
        has_unknown = True

    # type
    raw_type = str(raw.get("type", "")).lower().strip()
    if raw_type in TYPE_TRANSLATION:
        normalized["type"] = TYPE_TRANSLATION[raw_type]
    else:
        warnings.append(
            f"unknown type value '{raw_type}' on metric "
            f"'{metric}' — flagged for normalization"
        )
        has_unknown = True

    # signal
    raw_signal = str(raw.get("signal", "")).lower().strip()
    if raw_signal in SIGNAL_TRANSLATION:
        normalized["signal"] = SIGNAL_TRANSLATION[raw_signal]
    else:
        warnings.append(
            f"unknown signal value '{raw_signal}' on metric "
            f"'{metric}' — flagged for normalization"
        )
        has_unknown = True

    return normalized, has_unknown, warnings


def _normalize_window(raw_window: str, warnings: list[str]) -> tuple[int, bool]:
    """
    Translate a window string to canonical window_seconds int.
    Returns (window_seconds, has_unknown).

    Falls back to 3600 (1h) if value is unrecognized and
    appends a warning.
    """
    normalized = str(raw_window).lower().strip()
    if normalized in WINDOW_TRANSLATION:
        return WINDOW_TRANSLATION[normalized], False

    try:
        return int(normalized), False
    except ValueError:
        warnings.append(
            f"unknown window value '{raw_window}' — defaulting to 3600"
        )
        return 3600, True


def _merge_tags(tags: list[str], unified: dict[str, list[str]], warnings: list[str]) -> None:
    """
    Merge a flat tag list into a unified dict[str, list[str]].

    Tag format: "key:value" (e.g. "team:payments")
    Malformed tags without a colon separator are logged and skipped.
    Duplicate values within the same key are deduplicated.
    Keys are lowercased. Values preserve original casing.
    """
    for tag in tags:
        if ":" not in tag:
            warnings.append(
                f"malformed tag '{tag}' has no colon separator — skipped"
            )
            continue
        key, value = tag.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key not in unified:
            unified[key] = []
        if value not in unified[key]:
            unified[key].append(value)


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
    """
    warnings: list[str] = []
    has_unknown = False

    # Step 1 — normalize window in SLO payload
    raw_slo = dict(state["raw_slo"])
    window_seconds, window_unknown = _normalize_window(
        raw_slo.get("window", ""), warnings
    )
    raw_slo["window_seconds"] = window_seconds
    if window_unknown:
        has_unknown = True

    # Step 2 — normalize firing monitors
    normalized_firing: list[dict] = []
    for monitor in state.get("firing_monitors", []):
        norm, unknown, mon_warnings = _normalize_monitor(monitor)
        normalized_firing.append(norm)
        warnings.extend(mon_warnings)
        if unknown:
            has_unknown = True

    # Step 3 — normalize quiet monitors
    normalized_quiet: list[dict] = []
    for monitor in state.get("quiet_monitors", []):
        norm, unknown, mon_warnings = _normalize_monitor(monitor)
        normalized_quiet.append(norm)
        warnings.extend(mon_warnings)
        if unknown:
            has_unknown = True

    # Step 4 — unify tags from all sources (ADR-007)
    unified: dict[str, list[str]] = {}
    _merge_tags(raw_slo.get("tags", []), unified, warnings)
    for monitor in normalized_firing:
        _merge_tags(monitor.get("tags", []), unified, warnings)
    for monitor in normalized_quiet:
        _merge_tags(monitor.get("tags", []), unified, warnings)

    # Step 5 — return state updates
    return {
        "raw_slo": raw_slo,
        "firing_monitors": normalized_firing,
        "quiet_monitors": normalized_quiet,
        "unified_tags": unified,
        "has_unknown_values": has_unknown,
        "normalization_warnings": warnings,
    }


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
      budget_state                HEALTHY / DEGRADED / EXHAUSTED / DEBT
    """
    slo = state["raw_slo"]
    burn_rate: float = slo["burn_rate"]
    budget_remaining: float = slo["error_budget_remaining_pct"]
    window_seconds: int = slo.get("window_seconds", 3600)
    window_minutes: float = window_seconds / 60

    # ------------------------------------------------------------------
    # budget_state — classify current error budget health
    # ------------------------------------------------------------------
    if budget_remaining > 50.0:
        budget_state = "HEALTHY"
    elif budget_remaining > 0.0:
        budget_state = "DEGRADED"
    elif budget_remaining == 0.0:
        budget_state = "EXHAUSTED"
    else:
        budget_state = "DEBT"

    # ------------------------------------------------------------------
    # time_to_exhaustion_minutes — how long until budget reaches zero
    # None if already exhausted or in debt
    # ------------------------------------------------------------------
    if budget_remaining <= 0.0:
        time_to_exhaustion: float | None = None
    elif burn_rate <= 0.0:
        # burn rate of zero means no consumption — budget never exhausts
        time_to_exhaustion = None
    else:
        time_to_exhaustion = round(
            (budget_remaining / (burn_rate * 100)) * window_minutes, 2
        )

    # ------------------------------------------------------------------
    # urgency_score — rule-based classification from burn rate
    # ------------------------------------------------------------------
    if burn_rate >= 5.0:
        urgency_score = "HIGH"
    elif burn_rate >= 2.0:
        urgency_score = "MEDIUM"
    else:
        urgency_score = "LOW"

    return {
        "time_to_exhaustion_minutes": time_to_exhaustion,
        "urgency_score": urgency_score,
        "budget_state": budget_state,
    }


# ---------------------------------------------------------------------------
# Private helpers — used by check_cloud_status
# ---------------------------------------------------------------------------

AWS_METRIC_PREFIXES: dict[str, str] = {
    "aws.ec2":            "EC2",
    "aws.rds":            "RDS",
    "aws.elasticache":    "ElastiCache",
    "aws.s3":             "S3",
    "aws.lambda":         "Lambda",
    "aws.sqs":            "SQS",
    "aws.alb":            "ALB",
    "aws.elb":            "ELB",
    "aws.cloudfront":     "CloudFront",
    "aws.dynamodb":       "DynamoDB",
    "aws.kinesis":        "Kinesis",
    "kubernetes.":        "EKS",
    "aws.eks":            "EKS",
    "aws.secretsmanager": "Secrets Manager",
    "aws.kms":            "KMS",
}

GCP_METRIC_PREFIXES: dict[str, str] = {
    "gcp.compute":        "Compute Engine",
    "gcp.cloudsql":       "Cloud SQL",
    "gcp.storage":        "Cloud Storage",
    "gcp.pubsub":         "Pub/Sub",
    "gcp.kubernetes":     "GKE",
    "gcp.run":            "Cloud Run",
    "gcp.functions":      "Cloud Functions",
    "kubernetes.":        "GKE",
    "gcp.kubernetes":     "GKE",
    "gcp.secretmanager":  "Secret Manager",
    "gcp.cloudkms":       "Cloud KMS",
}


def _infer_service_from_metric(provider: str, metric: str) -> str | None:
    """
    Infer cloud service name from metric name prefix.
    Returns None if no match found.
    Metric names are system-generated and cannot be stale — this is
    the primary dependency signal per ADR-009.
    """
    prefix_map = (
        AWS_METRIC_PREFIXES if provider == "aws"
        else GCP_METRIC_PREFIXES
    )
    for prefix, service in prefix_map.items():
        if metric.startswith(prefix):
            return service
    return None


def _extract_cloud_services(
    provider: str,
    unified_tags: dict[str, list[str]],
    firing_monitors: list[dict]
) -> tuple[list[str], str | None]:
    """
    Extract cloud services used by firing monitors.
    Returns (services, note) where note explains the source or
    any fallback behavior.

    Priority chain (ADR-009):
      1. metric name prefix inference (objective, system-generated)
      2. {provider}-service: tags (human annotation, fallback)
      3. empty list + note (Claude reasons contextually)
    """
    # Priority 1 — metric prefix inference
    inferred: list[str] = []
    for monitor in firing_monitors:
        metric = monitor.get("metric", "").lower()
        service = _infer_service_from_metric(provider, metric)
        if service and service not in inferred:
            inferred.append(service)

    if inferred:
        return inferred, None

    # Priority 2 — explicit service tags
    tag_key = f"{provider}-service"
    service_tags = unified_tags.get(tag_key, [])
    if service_tags:
        return service_tags, None

    # Priority 3 — no signal available, return all with note
    return [], (
        "service dependency could not be determined from metric prefixes "
        "or service tags — returning all affected services for Claude "
        "to reason over contextually"
    )


def _check_azure_status(
    region: str | None,
    checked_at: str
) -> CloudProviderStatus:
    """
    Azure has no unauthenticated public JSON feed (Friction Log #1).
    Returns UNKNOWN with a note explaining the authentication requirement.
    Production path: Azure Resource Health API with managed identity.
    """
    return CloudProviderStatus(
        provider="azure",
        region=region,
        az=None,
        status="UNKNOWN",
        affected_services=[],
        incident_url="https://azure.status.microsoft",
        checked_at=checked_at,
        note=(
            "Azure status requires authentication via the Resource Health API. "
            "Unauthenticated public JSON feed is not available. "
            "Check https://azure.status.microsoft manually. "
            "Production path: Azure Resource Health API with managed identity."
        )
    )


def _parse_aws_status(
    data: dict,
    region: str | None,
    checked_at: str,
    used_services: list[str],
    filter_note: str | None
) -> CloudProviderStatus:
    """
    Parse AWS status feed JSON.
    Filters by used_services and region if provided.
    Returns OPERATIONAL if no active incidents match.
    """
    affected_services: list[str] = []
    incident_url: str | None = None
    status = "OPERATIONAL"
    raw_affected: list[str] = []

    archive = data.get("archive", [])

    for entry in archive:
        service_name = entry.get("service_name", "")
        summary = entry.get("summary", "").lower()
        entry_region = entry.get("region", "")

        # filter by region if tag present
        if region and entry_region and region not in entry_region:
            continue

        # only active incidents (no end time)
        if entry.get("end"):
            continue

        raw_affected.append(service_name)
        if not incident_url:
            incident_url = entry.get("url")
        status = "OUTAGE" if "disruption" in summary else "DEGRADED"

    # filter by used services if known
    if used_services and raw_affected:
        affected_services = [
            s for s in raw_affected
            if any(svc.lower() in s.lower() for svc in used_services)
        ]
        # if filtering produces no matches, return all with note
        if not affected_services:
            affected_services = raw_affected
            filter_note = (
                f"service filter {used_services} produced no matches "
                f"in feed — returning all affected services"
            )
    else:
        affected_services = raw_affected

    return CloudProviderStatus(
        provider="aws",
        region=region,
        az=None,
        status=status if affected_services else "OPERATIONAL",
        affected_services=affected_services,
        incident_url=incident_url,
        checked_at=checked_at,
        note=filter_note
    )


def _parse_gcp_status(
    data: list,
    region: str | None,
    checked_at: str,
    used_services: list[str],
    filter_note: str | None
) -> CloudProviderStatus:
    """
    Parse GCP status feed JSON.
    Uses product IDs (not display names) per GCP documentation.
    Filters by used_services and region if provided.
    Only reads stable fields per GCP feed documentation.
    """
    affected_services: list[str] = []
    incident_url: str | None = None
    status = "OPERATIONAL"
    raw_affected: list[str] = []

    for incident in data:
        # only active incidents (no end time)
        if incident.get("end"):
            continue

        affected_products = incident.get("affected_products", [])
        for product in affected_products:
            product_id = product.get("id", "")
            product_title = product.get("title", "")

            if region:
                updates = incident.get("updates", [])
                region_mentioned = any(
                    region in update.get("text", "")
                    for update in updates
                )
                if not region_mentioned:
                    continue

            raw_affected.append(product_id or product_title)
            if not incident_url:
                incident_url = incident.get("uri")

            severity = incident.get("severity", "").lower()
            status = "OUTAGE" if severity == "high" else "DEGRADED"

    # filter by used services if known
    if used_services and raw_affected:
        affected_services = [
            s for s in raw_affected
            if any(svc.lower() in s.lower() for svc in used_services)
        ]
        if not affected_services:
            affected_services = raw_affected
            filter_note = (
                f"service filter {used_services} produced no matches "
                f"in feed — returning all affected services"
            )
    else:
        affected_services = raw_affected

    return CloudProviderStatus(
        provider="gcp",
        region=region,
        az=None,
        status=status if affected_services else "OPERATIONAL",
        affected_services=affected_services,
        incident_url=incident_url,
        checked_at=checked_at,
        note=filter_note
    )


def _fetch_provider_status(
    provider: str,
    region: str | None,
    checked_at: str,
    used_services: list[str],
    filter_note: str | None
) -> CloudProviderStatus:
    """
    Fetch and parse the provider status feed for AWS or GCP.
    Returns UNKNOWN on any network error or parse failure.
    Enforces 5 second timeout per request.
    """
    feed_url = CLOUD_STATUS_FEEDS[provider]

    try:
        response = httpx.get(feed_url, timeout=5.0)
        response.raise_for_status()
        data = response.json()

        if provider == "aws":
            return _parse_aws_status(
                data, region, checked_at, used_services, filter_note
            )
        elif provider == "gcp":
            return _parse_gcp_status(
                data, region, checked_at, used_services, filter_note
            )

    except httpx.TimeoutException:
        return CloudProviderStatus(
            provider=provider,
            region=region,
            az=None,
            status="UNKNOWN",
            affected_services=[],
            incident_url=None,
            checked_at=checked_at,
            note="status feed request timed out after 5 seconds"
        )
    except Exception as e:
        return CloudProviderStatus(
            provider=provider,
            region=region,
            az=None,
            status="UNKNOWN",
            affected_services=[],
            incident_url=None,
            checked_at=checked_at,
            note=f"status feed fetch failed: {type(e).__name__}"
        )


def check_cloud_status(state: IncidentState) -> dict[str, Any]:
    """
    Conditional deterministic node. Only invoked when a cloud:* tag
    is present in unified_tags. Fetches the provider status feed for
    each cloud tag value and writes CloudProviderStatus objects to state.

    Service dependency filtering (ADR-009):
      Priority 1: metric name prefix inference
      Priority 2: {provider}-service: tags
      Priority 3: return all affected services with note

    Supports: aws, gcp
    Azure: returns UNKNOWN with note (Friction Log #1)
    Timeout: 5 seconds per provider. Failure returns UNKNOWN.

    Writes to state:
      cloud_provider_statuses   list[CloudProviderStatus]
    """
    unified_tags = state.get("unified_tags", {})
    cloud_tags = unified_tags.get("cloud", [])
    region_tags = unified_tags.get("region", [])
    firing_monitors = state.get("firing_monitors", [])

    if not cloud_tags:
        return {"cloud_provider_statuses": []}

    statuses: list[CloudProviderStatus] = []
    region = region_tags[0] if region_tags else None
    checked_at = datetime.now(timezone.utc).isoformat()

    for provider in cloud_tags:
        provider = provider.lower().strip()

        if provider == "azure":
            statuses.append(_check_azure_status(region, checked_at))
            continue

        if provider not in CLOUD_STATUS_FEEDS:
            statuses.append(CloudProviderStatus(
                provider=provider,
                region=region,
                az=None,
                status="UNKNOWN",
                affected_services=[],
                incident_url=None,
                checked_at=checked_at,
                note=f"unsupported provider '{provider}' — no status feed configured"
            ))
            continue

        used_services, filter_note = _extract_cloud_services(
            provider, unified_tags, firing_monitors
        )

        statuses.append(
            _fetch_provider_status(
                provider, region, checked_at, used_services, filter_note
            )
        )

    return {"cloud_provider_statuses": statuses}

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
