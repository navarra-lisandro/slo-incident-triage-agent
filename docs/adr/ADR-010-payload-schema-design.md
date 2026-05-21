# ADR-010: Payload Schema Design

## Status
Accepted

## Context
The agent receives incident payloads via POST /triage. A key design
decision is how to handle the reality that different monitoring providers
use different field names, status strings, and metric conventions — while
the agent's internal reasoning depends on a consistent canonical schema.

A secondary constraint is that the agent's core reasoning depends on
SLO burn rate, which is not a universal concept across monitoring
providers (see Friction Log #2).

## Decision
I implement a two-layer payload model:

  Layer 1 — External schema (provider-facing)
    Accepts any monitoring provider payload
    Field values may use provider-specific conventions
    Burn rate is a required field regardless of provider

  Layer 2 — Internal canonical schema (agent-facing)
    Consistent field names and values across all providers
    Used by all nodes downstream of ingest_incident
    Never exposed externally

Translation between layers is handled by ingest_incident
(deterministic translation table for known values) and
normalize_incident (conditional LLM normalization for unknown
values). See ADR-006 for the separation rationale.

## Canonical Internal Schema

### Signal values
  "latency"         p50/p95/p99 response time monitors
  "traffic"         requests/sec, throughput monitors
  "errors"          error rate, HTTP 5xx monitors
  "saturation"      CPU, memory, connection pool monitors
  "synthetic_check" synthetic check and RUM monitors

  Note: monitor type and signal are independent dimensions.
  All four golden signals fall under type "performance".
  type "synthetic" always maps to signal "synthetic_check".

### Status values
  "firing"    monitor is currently triggered
  "healthy"   monitor is currently ok

### Monitor type values
  "performance"   APM / metric-based monitor
  "synthetic"     synthetic check / RUM monitor

### Window values
  window_seconds: int   burn rate evaluation window in seconds
                        1h fast burn  = 3600
                        6h slow burn  = 21600

### Budget state values (derived by assess_slo_impact)
  "HEALTHY"     error_budget_remaining > 50%
  "DEGRADED"    error_budget_remaining > 0% and <= 50%
  "EXHAUSTED"   error_budget_remaining = 0%
  "DEBT"        error_budget_remaining < 0%

### Urgency score values (derived by assess_slo_impact)
  "HIGH"        burn_rate >= 5x
  "MEDIUM"      burn_rate >= 2x and < 5x
  "LOW"         burn_rate < 2x

## Known Provider Translation Tables

### Datadog
  status:
    "triggered"   → "firing"
    "ok"          → "healthy"
    "warn"        → "firing"
    "no data"     → "healthy"

  type:
    "apm"         → "performance"
    "metric"      → "performance"
    "query"       → "performance"
    "synthetics"  → "synthetic"
    "rum"         → "synthetic"

  window:
    "1h"          → 3600
    "6h"          → 21600

  signal:
      "latency"         → "latency"
      "errors"          → "errors"
      "error_rate"      → "errors"
      "traffic"         → "traffic"
      "throughput"      → "traffic"
      "saturation"      → "saturation"
      "cpu"             → "saturation"
      "memory"          → "saturation"
      "synthetic_check" → "synthetic_check"
      "rum"             → "synthetic_check"

### Prometheus (via Alertmanager)
  status:
    "firing"      → "firing"
    "resolved"    → "healthy"

  type:
    not native — inferred from alert labels
    job="blackbox" → "synthetic"
    default        → "performance"

  window:
    not native — must be included in annotations
    by pyrra or sloth before reaching /triage
    see Friction Log #2 for Prometheus burn rate path

signal:
    not native — inferred from alert labels and metric names
    metric contains "latency" or "duration" → "latency"
    metric contains "error" or "5xx"        → "errors"
    metric contains "request" or "rps"      → "traffic"
    metric contains "cpu" or "memory"
      or "saturation"                       → "saturation"
    job="blackbox"                          → "synthetic_check"
    unknown                                 → flagged for
                                              normalize_incident

### Grafana
  Note: Grafana Alerting typically sits on top of Prometheus or
  another time-series data source. Signal inference follows the
  same pattern as Prometheus — derived from metric names and
  alert labels, not explicit signal fields.
  
  status:
    "alerting"    → "firing"
    "ok"          → "healthy"
    "pending"     → "firing"
    "no_data"     → "healthy"

  type:
    not native — inferred from datasource
    default       → "performance"

  window:
    available if Grafana SLO plugin configured
    see Friction Log #2

  signal:
      not native — inferred from panel or alert name
      follows same inference pattern as Prometheus
      unknown values flagged for normalize_incident

## SLO Burn Rate Requirement

Burn rate is a required field in the external payload schema.
The source of the burn rate calculation is explicitly out of scope —
the agent does not care which system calculated it, only that it
is present and accurate.

Provider-specific burn rate paths:
  Datadog       native — available in SLO webhook payload
  Prometheus    external calculation required via pyrra or sloth
                https://github.com/pyrra-dev/pyrra
                https://github.com/slok/sloth
  Grafana       available if SLO plugin configured
  New Relic     available via SLI/SLO tracking feature
  PagerDuty     not applicable — PagerDuty is incident management,
                not a monitoring source. Payloads should originate
                from the upstream monitoring provider.

## Unknown Value Handling

When ingest_incident encounters a field value not in the translation
table, it sets has_unknown_values = True in state without halting.
The normalize_incident node is then conditionally invoked to resolve
the unknown values via Claude.

Claude's normalization prompt provides:
  - The unknown field value and its context
  - The canonical schema options for that field
  - The full monitor payload for additional context

Claude returns the canonical value. The result is written to state
and the graph continues on the normal path.

If Claude cannot confidently normalize a value, it sets the field
to "unknown" and adds a normalization_warning to state. The
draft_summary surfaces all normalization warnings to the on-call
engineer.

## External Payload Schema (Required Fields)

  incident_id           str       unique identifier
  triggered_at          str       ISO 8601 timestamp
  service               str       service name
  slo.name              str       SLO name
  slo.target_pct        float     SLO target percentage
  slo.burn_rate         float     current burn rate (required)
  slo.error_budget_     float     remaining budget percentage
    remaining_pct
  slo.window            str       evaluation window
  firing_monitors       list      at least one required
  quiet_monitors        list      may be empty

## Alternatives Considered

### Single schema (internal = external)
  Rejected — forces all providers to use our internal field names.
  Breaks provider-agnosticism and requires providers to understand
  our canonical schema rather than sending their native payload.

### LLM normalization for all values
  Rejected — makes the common case (known Datadog payload)
  probabilistic. Deterministic translation for known values
  is faster, cheaper, and more reliable. See ADR-006.

### Provider-specific endpoints (/triage/datadog, /triage/prometheus)
  Rejected — splits the API surface and duplicates routing logic.
  A single /triage endpoint with a two-layer schema is cleaner
  and more maintainable.

## Consequences
- ingest_incident owns the translation table and must be updated
  when new known providers are onboarded
- burn rate is always required — providers without native burn rate
  support must calculate it externally before calling /triage
- normalization_warnings in state are surfaced in draft_summary
- The translation table is unit testable — each mapping has a
  correct answer
- Adding a new known provider requires updating this ADR and the
  translation table in ingest_incident
