# ADR-012: Runbook Architecture

## Status
Accepted

## Context
The query_runbook node needs access to service-specific remediation
knowledge to produce actionable incident response steps. A key design
decision is whether Claude generates remediation steps from its training
knowledge, or retrieves and synthesizes from existing runbook documents.

## Decision
I implement a retrieval-based runbook architecture. Claude does not
invent remediation steps — it reads the relevant service runbook,
identifies applicable sections based on the current firing signals
and failure pattern, and synthesizes a prioritized action plan
adapted to the specific incident context.

## Rationale

### Why retrieval beats generation for runbooks

Claude's training knowledge is generic:
  - knows kubectl exists, not your cluster configuration
  - knows Redis exists, not your eviction policy
  - knows PagerDuty exists, not your escalation tree
  - knows database connection pools exist, not your
    specific metrics endpoint or connection string

Service runbooks encode institutional knowledge:
  - exact kubectl commands for your cluster and namespaces
  - your specific deployment names and configurations
  - your company's escalation paths and on-call handles
  - your DR procedures for your specific infrastructure
  - your circuit breaker and failover patterns

A purely generative agent produces generic steps —
useful, but not immediately actionable without context.
A retrieval-based agent produces specific, actionable
steps drawn from institutional knowledge.

### Claude's actual role in query_runbook

Claude is not a step generator — it is a step selector
and synthesizer:

  1. Read the full service runbook
  2. Identify which sections are relevant to the current
     firing signals and failure pattern from state
  3. Extract and prioritize applicable steps
  4. Adapt steps to the specific incident context
     e.g. "given CPU saturation is the PRIMARY signal,
     start with the saturation section before latency"
  5. Surface the most critical immediate actions first

This is where the LLM adds value over a keyword search —
Claude understands which steps are relevant given the
correlated signal context, not just which sections match
a keyword.

## v1 Implementation

Runbooks are local Markdown files in data/runbooks/,
one file per service:

  data/runbooks/payments-service.md
  data/runbooks/auth-service.md
  data/runbooks/order-api.md

The read_runbook tool reads the appropriate file based
on the service name from agent state. Missing runbooks
return a graceful fallback message — the agent continues
with general SRE principles rather than halting.

## Production Path

In production, runbooks rarely live as local files.
The read_runbook tool is designed to be extended to
fetch from internal documentation systems:

  Confluence    fetch via Confluence REST API
                filter by service label or space key

  Notion        fetch via Notion API
                filter by service database property

  GitHub Wiki   fetch via GitHub API
                read service-specific wiki page

  Custom docs   fetch via internal documentation API

The tool interface remains identical — only the data
source changes. This is a configuration change, not
an architectural change. The agent graph and node
logic require no modification.

## Testing vs Production

  Testing       synthetic runbook files with realistic
                but fictional commands and configurations
                agent behavior is identical to production
                only the runbook content differs

  Production    real runbooks from internal documentation
                same agent, same graph, real institutional
                knowledge and real escalation paths

The swap from test to production runbooks is a data
change, not a code change.

## Consequences
- Runbook quality directly determines remediation quality
  — stale or incomplete runbooks produce weaker output
- Missing runbooks degrade gracefully — agent falls back
  to general SRE principles with a warning in the summary
- Adding a new service requires creating a new runbook file
  before the agent can produce service-specific steps
- Updating runbooks requires no code changes — data only
- The production path to internal documentation systems
  requires authentication handling in the read_runbook tool
- LangSmith traces show exactly which runbook was read
  and what content Claude received — fully observable
