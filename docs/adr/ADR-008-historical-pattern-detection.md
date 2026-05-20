# ADR-008: Historical Pattern Detection — v1 Exclusion

## Status
Accepted

## Context
During state design, a question arose about whether the agent should
reason over historical firing patterns — for example, whether the same
monitors fired at the same time yesterday, or on the same day last week,
suggesting a recurring pattern such as a scheduled job, a maintenance
window, or a known capacity issue during peak traffic hours.

Historical pattern detection would allow the agent to:
- Identify recurring incidents and suppress unnecessary pages
- Flag known patterns to the on-call engineer before they investigate
- Distinguish between a novel incident and a known recurring degradation

## Decision
I explicitly exclude historical pattern detection from v1. The agent
reasons exclusively over the current incident payload — the SLO state,
firing monitors, and cloud provider status at the time of the alert.
No historical data is queried, stored, or referenced.

## Rationale
The agent in its current form is stateless between invocations. It
receives a payload, reasons over it, and returns a response. There is
no persistence layer between runs.

Implementing historical pattern detection would require one of:

  Option A   Query Datadog API for monitor firing history
             Requires live Datadog credentials, external API dependency,
             rate limit handling, and pagination logic. Out of scope
             for v1.

  Option B   Maintain a local database of past incident payloads
             Requires a persistence layer (Postgres, DynamoDB, etc.),
             schema design, and query logic. Significant scope increase.
             Introduces infrastructure dependency not justified by v1
             requirements.

  Option C   Webhook enrichment layer — append recent firing history
             to the payload before it reaches /triage
             This is the designed future path. A pre-processing service
             queries the incident history store and enriches the payload
             with a recent_history field before forwarding to the agent.
             The agent state schema already reserves space for this field
             as an optional input.

Neither Option A nor Option B is appropriate for v1. Option C is the
correct architectural path and is documented as the next iteration.

## LangGraph Consideration
LangGraph does not provide built-in persistence between separate graph
invocations. Each invocation is stateless by default. LangGraph's
checkpointer feature provides persistence within a single graph run
(for human-in-the-loop and resumable workflows) but does not address
cross-invocation memory of past incidents. This limitation is worth
flagging to developers building incident management systems who may
expect framework-level persistence.

## Future Path — Option C Webhook Enrichment
The designed production path:

  1. Incident payload arrives at an enrichment service
  2. Enrichment service queries incident history store for the same
     service + signal combination within the last 7 days
  3. Enrichment service appends recent_history to the payload
  4. Enriched payload is forwarded to POST /triage
  5. ingest_incident node reads recent_history if present
  6. triage_firing_signals node receives historical context
     alongside current signal data

The agent state schema includes recent_history as an optional field
(None by default in v1) to make this future path a additive change,
not a breaking one.

## Consequences
- v1 agent has no memory of past incidents
- Recurring incidents will be triaged as novel each time
- The on-call engineer must apply their own pattern recognition
- recent_history is reserved in state as Optional[list] = None
- Adding historical detection in v2 requires the enrichment service
  and a history store — the agent graph itself requires minimal changes
- This decision should be revisited when the agent is deployed to
  production and recurring incident patterns are observed
