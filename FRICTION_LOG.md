# Friction Log

Items are logged chronologically as discovered during development.
Each item includes the discovery context and a suggested improvement.

---

## Friction #1 — Azure status granularity requires authentication

**Discovery:** During design of the cloud provider status check node (ADR-009).

**Problem:** AWS and GCP provide unauthenticated public JSON feeds with
regional granularity:
  AWS: https://status.aws.amazon.com/data.json
  GCP: https://status.cloud.google.com/incidents.json

Azure's equivalent (Resource Health Events API) requires a Subscription
ID or Tenant ID, making it unavailable for unauthenticated public status
checks:
  GET https://management.azure.com/subscriptions/{subscriptionId}/
      providers/Microsoft.ResourceHealth/events
      ?api-version=2022-10-01&$filter=region eq 'East US'

Azure's public status page (azure.status.microsoft) exists but provides
only coarse-grained status without programmatic regional filtering.
This asymmetry across providers is undocumented and only discoverable
through trial and error.

**v1 approach:** Azure status returns UNKNOWN with a note explaining
the authentication requirement.

**Production path:** Azure Resource Health API with managed identity
authentication — no API key required if running inside Azure infrastructure.

**Suggested improvement:** Azure should expose an unauthenticated public
JSON endpoint with regional granularity equivalent to AWS and GCP, or
at minimum clearly document the gap and the managed identity path for
developers building multi-cloud status checkers.
