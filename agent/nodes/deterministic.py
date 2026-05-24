"""
agent/nodes/deterministic.py

Deterministic nodes for the SLO incident triage agent.
No LLM calls in this module — pure Python logic only.

Nodes:
  ingest_incident       parse payload, apply translation tables,
                        unify tags, flag unknown values
  assess_slo_impact     burn rate calculations
  check_cloud_status    fetch and parse cloud provider status feeds

Private helpers are co-located with the node that uses them.
Convention: prefix with underscore (_) for private module-level helpers.

Design decisions:
  ADR-006   deterministic vs LLM node separation
  ADR-007   tag normalization strategy
  ADR-009   cloud provider status check with service dependency filtering
  ADR-010   payload schema design and translation tables
  ADR-013   FastAPI and graph boundary
"""

import httpx
from datetime import datetime, timezone
from typing import Any

from agent.state import IncidentState, CloudProviderStatus


# ---------------------------------------------------------------------------
# Translation tables — used by ingest_incident (ADR-010)
# ---------------------------------------------------------------------------

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
    # pass-through if already int-like string
    "3600": 3600,
    "21600": 21600,
}

# Cloud provider status feed URLs (ADR-009)
CLOUD_STATUS_FEEDS: dict[str, str] = {
    "aws": "https://status.aws.amazon.com/data.json",
    "gcp": "https://status.cloud.google.com/incidents.json",
    # Azure has no unauthenticated JSON feed (Friction Log #1)
    "azure": None,
}

# Cloud service metric prefix maps (ADR-009)
# Metric names are system-generated and cannot be stale — primary
# dependency signal for cloud service filtering
AWS_METRIC_PREFIXES: dict[str, str] = {
    "aws.ec2":              "EC2",
    "aws.rds":              "RDS",
    "aws.elasticache":      "ElastiCache",
    "aws.s3":               "S3",
    "aws.lambda":           "Lambda",
    "aws.sqs":              "SQS",
    "aws.alb":              "ALB",
    "aws.elb":              "ELB",
    "aws.cloudfront":       "CloudFront",
    "aws.dynamodb":         "DynamoDB",
    "aws.kinesis":          "Kinesis",
    "aws.eks":              "EKS",
    "aws.secretsmanager":   "Secrets Manager",
    "aws.kms":              "KMS",
    "kubernetes.":          "EKS",
}

GCP_METRIC_PREFIXES: dict[str, str] = {
    "gcp.compute":          "Compute Engine",
    "gcp.cloudsql":         "Cloud SQL",
    "gcp.storage":          "Cloud Storage",
    "gcp.pubsub":           "Pub/Sub",
    "gcp.kubernetes":       "GKE",
    "gcp.run":              "Cloud Run",
    "gcp.functions":        "Cloud Functions",
    "gcp.secretmanager":    "Secret Manager",
    "gcp.cloudkms":         "Cloud KMS",
    # note: AWS "Secrets Manager" (plural) vs GCP "Secret Manager" (singular)
    "kubernetes.":          "GKE",
}


# ---------------------------------------------------------------------------
# Private helpers — ingest_incident
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

    for field, table in [
        ("status", STATUS_TRANSLATION),
        ("type", TYPE_TRANSLATION),
        ("signal", SIGNAL_TRANSLATION),
    ]:
        raw_value = str(raw.get(field, "")).lower().strip()
        if raw_value in table:
            normalized[field] = table[raw_value]
        else:
            warnings.append(
                f"unknown {field} value '{raw_value}' on metric "
                f"'{metric}' — flagged for normalization"
            )
            has_unknown = True

    return normalized, has_unknown, warnings


def _normalize_window(
    raw_window: str,
    warnings: list[str]
) -> tuple[int, bool]:
    """
    Translate a window string to canonical window_seconds int.
    Returns (window_seconds, has_unknown).
    Falls back to 3600 (1h) if value is unrecognized.
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


def _merge_tags(
    tags: list[str],
    unified: dict[str, list[str]],
    warnings: list[str]
) -> None:
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
# Private helpers — check_cloud_status
# ---------------------------------------------------------------------------

def _infer_service_from_metric(provider: str, metric: str) -> str | None:
    """
    Infer cloud service name from metric name prefix.
    Returns None if no match found.
    Metric names are system-generated and cannot be stale — primary
    dependency signal per ADR-009.
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
    Returns (services, note).

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
    service_tags = unified_tags.get(f"{provider}-service", [])
    if service_tags:
        return service_tags, None

    # Priority 3 — no signal available
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
    raw_affected: list[str] = []
    incident_url: str | None = None
    status = "OPERATIONAL"

    for entry in data.get("archive", []):
        service_name = entry.get("service_name", "")
        summary = entry.get("summary", "").lower()
        entry_region = entry.get("region", "")

        if region and entry_region and region not in entry_region:
            continue
        if entry.get("end"):
            continue

        raw_affected.append(service_name)
        if not incident_url:
            incident_url = entry.get("url")
        status = "OUTAGE" if "disruption" in summary else "DEGRADED"

    affected_services = raw_affected
    if used_services and raw_affected:
        filtered = [
            s for s in raw_affected
            if any(svc.lower() in s.lower() for svc in used_services)
        ]
        if filtered:
            affected_services = filtered
        else:
            filter_note = (
                f"service filter {used_services} produced no matches "
                f"in feed — returning all affected services"
            )

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
    raw_affected: list[str] = []
    incident_url: str | None = None
    status = "OPERATIONAL"

    for incident in data:
        if incident.get("end"):
            continue

        for product in incident.get("affected_products", []):
            product_id = product.get("id", "")
            product_title = product.get("title", "")

            if region:
                updates = incident.get("updates", [])
                if not any(region in u.get("text", "") for u in updates):
                    continue

            raw_affected.append(product_id or product_title)
            if not incident_url:
                incident_url = incident.get("uri")
            severity = incident.get("severity", "").lower()
            status = "OUTAGE" if severity == "high" else "DEGRADED"

    affected_services = raw_affected
    if used_services and raw_affected:
        filtered = [
            s for s in raw_affected
            if any(svc.lower() in s.lower() for svc in used_services)
        ]
        if filtered:
            affected_services = filtered
        else:
            filter_note = (
                f"service filter {used_services} produced no matches "
                f"in feed — returning all affected services"
            )

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

    Translation tables applied (ADR-010).
    Tag normalization per ADR-007.
    Raw payload mapping per ADR-013.
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
    for monitor in normalized_firing + normalized_quiet:
        _merge_tags(monitor.get("tags", []), unified, warnings)

    return {
        "raw_slo": raw_slo,
        "firing_monitors": normalized_firing,
        "quiet_monitors": normalized_quiet,
        "unified_tags": unified,
        "has_unknown_values": has_unknown,
        "normalization_warnings": warnings,
    }


def assess_slo_impact(state: IncidentState) -> dict[str, Any]:
    """
    Pure arithmetic. Derives urgency metrics from burn rate and
    error budget fields. No LLM call.

    Writes to state:
      time_to_exhaustion_minutes
      urgency_score               HIGH / MEDIUM / LOW
      budget_state                HEALTHY / DEGRADED / EXHAUSTED / DEBT
    """
    slo = state["raw_slo"]
    burn_rate: float = slo["burn_rate"]
    budget_remaining: float = slo["error_budget_remaining_pct"]
    window_minutes: float = slo.get("window_seconds", 3600) / 60

    # budget_state
    if budget_remaining > 50.0:
        budget_state = "HEALTHY"
    elif budget_remaining > 0.0:
        budget_state = "DEGRADED"
    elif budget_remaining == 0.0:
        budget_state = "EXHAUSTED"
    else:
        budget_state = "DEBT"

    # time_to_exhaustion_minutes
    if budget_remaining <= 0.0 or burn_rate <= 0.0:
        time_to_exhaustion: float | None = None
    else:
        time_to_exhaustion = round(
            (budget_remaining / (burn_rate * 100)) * window_minutes, 2
        )

    # urgency_score
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


def check_cloud_status(state: IncidentState) -> dict[str, Any]:
    """
    Conditional deterministic node. Only invoked when a cloud:* tag
    is present in unified_tags.

    Service dependency filtering priority chain (ADR-009):
      1. metric name prefix inference
      2. {provider}-service: tags
      3. return all affected services with note

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
