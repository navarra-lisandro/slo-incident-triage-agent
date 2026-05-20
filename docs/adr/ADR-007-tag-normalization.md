# ADR-007: Tag Normalization Strategy

## Status
Accepted

## Context
Datadog monitor payloads include tags as a flat list of strings following
a key:value convention (e.g. "team:payments", "service:checkout"). These
tags are the primary correlation key for identifying blast radius, service
ownership, and environment context during incident triage.

A single tag key can appear multiple times with different values. For
example, a service owned by multiple teams would have:

  ["team:payments", "team:backoffice", "service:checkout", "env:prod"]

The agent must normalize tags from multiple sources — the SLO payload,
each firing monitor, and each quiet monitor — into a unified tag
namespace that all downstream nodes can reason over without string
parsing.

## Decision
I normalize all tags into a unified dict[str, list[str]] structure
during the ingest_incident node. This is the canonical tag representation
used throughout the agent state for all downstream nodes.

Example:
  Input (flat lists from multiple sources):
    SLO tags:     ["service:checkout", "env:prod"]
    Monitor 1:    ["service:checkout", "team:payments", "signal:latency"]
    Monitor 2:    ["service:checkout", "team:backoffice", "signal:saturation"]

  Output (unified normalized structure):
    {
      "service": ["checkout"],
      "env":     ["prod"],
      "team":    ["payments", "backoffice"],
      "signal":  ["latency", "saturation"]
    }

Duplicate values within the same key are deduplicated. Order within
each list is not guaranteed and should not be relied upon.

## Rationale
- A flat list requires string splitting (.split(":")) scattered across
  nodes every time a tag value is needed. This is error-prone and
  untestable as a unit.
- dict[str, list[str]] allows deterministic nodes to extract all values
  for a given key programmatically without string parsing:
    tags.get("team", [])  → ["payments", "backoffice"]
- Multiple values per key are preserved. A flat dict (str → str) would
  silently drop "team:backoffice" if "team:payments" was already present.
- Claude reads the normalized structure directly. A grouped dict is
  more legible to an LLM than a flat list requiring implicit parsing.
- The cloud:* and region:* tags that drive the check_cloud_status node
  (ADR-009) are extracted from this normalized structure:
    tags.get("cloud", [])   → ["aws", "gcp"]
    tags.get("region", [])  → ["us-east-1"]

## Tag Schema Conventions
The following tag keys have first-class meaning in the agent:

  cloud      cloud provider — aws, gcp, azure
             drives check_cloud_status node routing
  region     cloud region — us-east-1, us-central1, eastus
             narrows cloud status check to specific region
  az         availability zone — best-effort, not all providers expose
             AZ-level status on public status pages
  service    service name — primary identifier for runbook lookup
  team       owning team(s) — used in draft_summary for escalation
  env        environment — prod, staging, dev
             used in severity classification context
  signal     golden signal type — latency, traffic, errors, saturation
             populated by monitor-level tags

## Consequences
- ingest_incident is responsible for normalizing all tags from all
  payload sources into the unified structure
- ingest_incident must handle malformed tags gracefully — tags without
  a colon separator are logged and skipped, not raised as errors
- All nodes read tags from state.unified_tags, never from raw payload
- Unit tests must cover tag normalization including duplicate keys,
  missing colons, and multi-source merging
- Adding new first-class tag keys requires updating this ADR
