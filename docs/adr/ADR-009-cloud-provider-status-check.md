# ADR-009: Cloud Provider Status Check Node

## Status
Accepted

## Context
During incident triage, one of the first questions a senior SRE asks is
"is this us or is this the cloud provider?" Checking the cloud provider
status page is a real, manual step that on-call engineers perform on
every significant incident. Encoding this check into the agent as a
deterministic node eliminates a manual step and gives Claude full context
before it reasons over the firing signals.

The cloud provider is identified from the normalized tag structure
(ADR-007) using the cloud:* tag key. Multiple cloud providers are
supported via multiple cloud:* tag values on a single incident.

## Decision
I implement check_cloud_status as a deterministic node that executes
after assess_slo_impact and before triage_firing_signals. The node:

  1. Reads cloud:* and region:* tags from unified_tags (ADR-007)
  2. For each cloud provider tag, fetches the provider status feed
  3. Filters results by region if a region:* tag is present
  4. Normalizes the result into a CloudProviderStatus object
  5. Writes a list of CloudProviderStatus objects to agent state

If no cloud:* tag is present, the node is skipped via a conditional
edge and cloud_provider_statuses is set to an empty list. This is
documented in the draft_summary output so the on-call engineer knows
the check was not performed.

## Provider Implementation

### AWS
  Feed:       https://status.aws.amazon.com/data.json
  Auth:       none — unauthenticated public JSON feed
  Granularity: regional (per-service RSS feeds available for deeper
               granularity, not implemented in v1)
  AZ level:   not available on public status page

### GCP
  Feed:       https://status.cloud.google.com/incidents.json
  Auth:       none — unauthenticated public JSON feed
  Granularity: regional (recently added to the feed)
  Key note:   use product IDs (affected_products.id), not display
              names — display names are marked Unstable and may change
              without warning per GCP documentation
  AZ level:   not available on public status page

### Azure
  Feed:       no unauthenticated public JSON feed available
  Public page: azure.status.microsoft — coarse-grained only, no
               programmatic regional filtering without authentication
  Auth path:  Azure Resource Health API requires Subscription ID
              or Tenant ID:
              GET https://management.azure.com/subscriptions/{id}/
                  providers/Microsoft.ResourceHealth/events
                  ?api-version=2022-10-01&$filter=region eq 'East US'
  v1 behavior: returns status UNKNOWN with a note explaining the
               authentication requirement
  Production path: Azure Resource Health API with managed identity
               authentication — no API key required when running
               inside Azure infrastructure
  See also:   FRICTION_LOG.md Friction #1

## Tag Schema for Cloud Status Routing

  cloud     required — drives which provider(s) to check
            values: aws, gcp, azure
            multiple values supported: cloud:aws + cloud:gcp

  region    optional — narrows the status check to a specific region
            values follow provider conventions:
              AWS:   us-east-1, us-west-2, eu-west-1
              GCP:   us-central1, europe-west1, asia-east1
              Azure: eastus, westeurope, southeastasia

  az        optional — accepted in payload, noted as best-effort
            no public status page exposes AZ-level granularity
            included in state for future use when providers expose it

## CloudProviderStatus Schema

  provider              str         aws / gcp / azure
  region                str | None  region from tag or None
  az                    str | None  az from tag, best-effort
  status                str         OPERATIONAL / DEGRADED /
                                    OUTAGE / UNKNOWN
  affected_services     list[str]   services with incidents
  incident_url          str | None  link to the status incident
  checked_at            str         ISO timestamp of the check
  note                  str | None  explanation if UNKNOWN

## Severity and Remediation Behavior

A confirmed cloud provider outage informs but never caps severity.
The agent classifies severity based on customer impact regardless
of whether the root cause is internal or external. Rationale:

  - The service is still down from the customer's perspective
  - The team may have remediation options even during a provider
    outage: failover to another region, fallback to disk storage,
    cross-provider failover, graceful degradation
  - Downgrading severity because "it's AWS's fault" delays response
    to a customer-impacting incident

The generate_remediation node receives cloud_provider_statuses and
is explicitly prompted to suggest failover and degradation options
when a provider outage is confirmed.

## Alternatives Considered

### Third-party status aggregators (statusgator, isitdownrightnow)
  Rejected — introduces an external dependency that may lag behind
  authoritative provider feeds, change APIs without notice, or
  become unavailable. Direct provider feeds are authoritative and free.

### Atlassian Statuspage API
  Rejected — Azure does not use Statuspage. AWS and GCP have their
  own native JSON feeds that are more reliable and authoritative than
  any Statuspage-backed aggregator.

### Single cloud provider only
  Rejected — the tag-driven routing model supports multi-cloud
  incidents without additional complexity. The node iterates over
  all cloud:* tag values.

## Consequences
- check_cloud_status executes on every incident where cloud:* tag
  is present — this is a live HTTP call with latency implications
- Timeout must be enforced: 5 seconds per provider, fail to UNKNOWN
- The node must handle network errors gracefully — a failed status
  check returns UNKNOWN, never raises an exception that halts the graph
- Azure support is limited to UNKNOWN in v1 — document this clearly
  in the draft_summary output when cloud:azure is present
- Adding a new cloud provider requires updating this ADR and adding
  a provider handler in check_cloud_status node implementation
- LangSmith traces will show the HTTP fetch timing per provider,
  making latency observable and debuggable
