# ADR-000: Project Overview and Design Intent

## Status
Accepted

## What This Project Does
slo-incident-triage-agent is a LangGraph-based HTTP service that
receives SLO burn rate alerts and reasons over the correlated signals
contributing to the burn to produce a structured incident triage
response — including severity classification, signal correlation
analysis, and prioritized remediation recommendations.

## Why This Project Exists

### The Problem
SLO burn rate alerts are among the most actionable but cognitively
demanding alerts an on-call engineer faces. A single SLO alert tells
you the error budget is burning — it does not tell you why. The
engineer must manually correlate multiple signals across APM golden
signals (Latency, Traffic, Errors, Saturation) and Synthetics to
understand which signals are contributing to the burn, assess the
blast radius, classify the severity, and determine the right
remediation path.

At 2am, this cognitive load is where incidents escalate unnecessarily.

### Why LLMs Are the Right Tool
Simple threshold alerts (CPU > 90%) have deterministic runbooks — a
script can handle those. The value of an LLM is in reasoning over
correlated, multi-signal degradation where no single rule captures
the full picture:

  "Error rate is 2.3%, burn rate is 4.2x, p99 latency spiked 800ms,
  and there was a deployment 12 minutes ago — is this a P1 or is the
  SLO window absorbing it?"

This is exactly the reasoning a senior SRE does intuitively. This
project encodes that reasoning into a traceable, evaluable agent.

## How This Project Works

### Alert Model
The project uses Datadog monitor-based SLOs as the alert model.
A monitor-based SLO aggregates multiple constituent monitors
(one per golden signal + synthetics) under a single SLO. When the
SLO burn rate exceeds a threshold, the agent receives:

  1. The SLO payload — burn rate, error budget remaining, window
  2. A snapshot of constituent monitor states — which monitors are
     firing (hot) and which are healthy (quiet)

The agent triages only the firing monitors. Quiet monitors are
included in the payload to give Claude the full diagnostic picture —
knowing what is NOT broken is half the triage.

### Signal Sources
APM Golden Signals
Latency      p50/p95/p99 response times
Traffic      requests/sec, deviation from baseline
Errors       error rate %, HTTP 5xx count
Saturation   CPU, memory, connection pool utilization
SLO Layer
Burn rate              current vs threshold (e.g. 14.4x = P1)
Error budget remaining percentage remaining in the window
SLO window             1h fast burn, 6h slow burn
Synthetics
Synthetic check status passing or failing
RUM vitals             LCP, FID, CLS degradation

### Agent Graph Topology

START
ingest_incident         parse SLO payload + monitor snapshot
assess_slo_impact       burn rate + time-to-exhaustion + urgency
triage_firing_signals   reason over only the hot monitors
classify_severity       P1-P4 with written justification
query_runbook           match service + signal pattern to steps
generate_remediation    synthesize prioritized action plan
draft_summary           structured output for Slack/PagerDuty
END

### Service Architecture
The agent is exposed as an HTTP service via FastAPI. Incident payloads
arrive as POST requests to /triage. The service is containerized via
Docker and deployable to Kubernetes via Helm. See ADR-005 for detail.

### Observability
All agent runs are traced in LangSmith at the node level. Each node
in the graph produces a discrete trace entry, enabling precise
identification of which reasoning step produced an incorrect result.
See ADR-006 (LangSmith Observability Strategy) for detail.

### Payload Source Agnosticism
While modeled on Datadog monitor-based SLO webhooks, the /triage
endpoint accepts any normalized incident payload conforming to the
defined schema. PagerDuty, Prometheus Alertmanager, and Grafana are
all viable trigger sources without changes to the agent logic.

## Key Design Principles
- Triage only what is firing — quiet monitors inform context, not action
- The LLM reasons, the graph orchestrates, the engineer decides
- Every reasoning step is observable and evaluable in LangSmith
- The orchestration layer is LLM-agnostic — provider is a config change
- The service layer is alert-source agnostic — trigger is a schema contract
