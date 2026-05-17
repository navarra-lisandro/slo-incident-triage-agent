# ADR-004: LangGraph vs Vendor-Specific Agent Frameworks

## Status
Accepted

## Context
Building a multi-step incident triage agent requires an orchestration
framework capable of managing stateful workflows, conditional routing,
and multi-node execution. Several frameworks were evaluated, including
vendor-specific agent SDKs and framework-agnostic orchestration libraries.

## Decision
I selected LangGraph as the agent orchestration framework over
vendor-specific agent frameworks such as the Claude Agent SDK,
OpenAI Agents SDK, and Google Gemini Agent SDK.

## Rationale
- LangGraph is LLM-agnostic — the orchestration layer is fully decoupled
  from the model provider. Switching from Anthropic to OpenAI or Gemini
  requires changing one line in nodes.py, not rewriting the agent framework
- The agent topology requires explicit graph control with 7 nodes and
  conditional routing based on burn rate severity — complexity that
  vendor-specific SDKs optimized for simpler tool-use loops cannot
  express cleanly
- LangGraph's native integration with LangSmith provides deep trace
  granularity at the node level, enabling precise evaluation of each
  reasoning step independently
- Vendor-specific SDKs create provider lock-in by design. LangGraph
  does not.

## Alternatives Considered
- Claude Agent SDK — Anthropic-native, simpler setup for tool use,
  but creates vendor lock-in and lacks explicit graph topology control
- OpenAI Agents SDK — similar tradeoff, optimized for OpenAI models,
  not suitable for multi-provider strategy
- Google Gemini Agent SDK — strong GCP integration but same lock-in
  concern, limited LangSmith observability support

## Consequences
- The agent orchestration is portable across LLM providers by design
- LangGraph's explicit node/edge model requires more upfront design
  than vendor SDKs but produces a more maintainable and testable graph
- LangSmith tracing is available natively without additional instrumentation
- Any future migration to a different LLM provider is a configuration
  change, not an architectural change
