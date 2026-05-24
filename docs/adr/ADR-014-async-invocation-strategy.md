# ADR-014: Async Invocation Strategy

## Status
Accepted

## Context
The agent graph involves 5-6 LLM API calls and potentially one
external HTTP fetch (cloud provider status). Total execution time
on the happy path is 8-12 seconds. The full path with cloud status
check can reach 10-15 seconds.

This creates two production concerns:

  Client timeout: most webhook senders have 5-10 second timeouts.
  Datadog's webhook timeout is 5 seconds. A synchronous /triage
  endpoint that takes 8-12 seconds will time out before responding.

  Server concurrency: a synchronous graph.invoke() call blocks
  the uvicorn worker for the full duration of the agent run.
  A spike of concurrent alerts starves the server.

## Decision
The /triage endpoint uses async def and graph.ainvoke() for non-blocking
graph invocation. This is the v1 implementation.

The production path is a webhook acknowledgement pattern (Option B
below) documented here for future implementation.

## Options Evaluated

### Option A: Async invocation (v1 — implemented)
  POST /triage is async def, uses graph.ainvoke()
  Non-blocking: uvicorn worker is free during LLM calls
  LangGraph manages thread execution internally
  Still has client timeout problem for slow full-path runs
  Correct for development and evaluation

### Option B: Async + webhook acknowledgement (production path)
  POST /triage returns 202 Accepted immediately
  Agent runs as a FastAPI BackgroundTask
  Result posted to a callback URL or stored for polling
  Eliminates client timeout problem entirely
  Requires callback URL in the request payload
  or a polling endpoint (GET /triage/{incident_id}/result)

### Option C: Queue-based (future scale path)
  POST /triage enqueues the incident to SQS or similar
  Worker process consumes queue and runs the agent
  Most scalable, most complex
  Appropriate when incident volume exceeds single-server capacity

## v1 Implementation

  async def triage_incident(request: TriageRequest) -> TriageResponse:
      result = await graph.ainvoke(initial_state)

  graph.ainvoke() is the async equivalent of graph.invoke().
  Reference: https://langchain-ai.github.io/langgraph/reference/graphs/

  Note: LLM nodes currently use synchronous llm.invoke() internally.
  graph.ainvoke() runs the graph in a thread pool, providing
  concurrency at the FastAPI level. Full async node implementations
  using llm.ainvoke() are a future iteration improvement.

## Production Path — Option B

  The /triage endpoint should be updated to:
    1. Accept an optional callback_url in the request payload
    2. Return 202 Accepted with a job_id immediately
    3. Run the agent as a BackgroundTask
    4. POST the TriageResponse to callback_url when complete
    5. Store result keyed by job_id for polling fallback

  This eliminates the Datadog 5-second webhook timeout constraint
  and decouples the monitoring system from agent execution time.

  FastAPI BackgroundTasks pattern:
    Reference: https://fastapi.tiangolo.com/tutorial/background-tasks/

## Payload Size Consideration
  The /triage payload is estimated at 8-15KB per incident.
  FastAPI and uvicorn handle this comfortably — the default
  uvicorn request size limit is 1MB. Payload parsing is not
  a performance concern. The bottleneck is LLM API call latency.

## Consequences
  - v1 /triage may time out for Datadog webhooks on the full path
  - Development and LangSmith evaluation are unaffected
  - FastAPI BackgroundTasks is the documented production upgrade path
  - Full async node implementations (llm.ainvoke()) improve
    concurrency further but are not required for v1 correctness
  - Option C (queue-based) should be evaluated when incident
    volume exceeds single-server capacity
