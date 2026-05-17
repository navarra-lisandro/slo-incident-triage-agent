# ADR-003: LLM Provider Selection

## Status
Accepted

## Context
The agent requires a large language model to perform multi-step reasoning
over correlated SLO signals, classify incident severity, and generate
structured remediation recommendations. The primary candidates evaluated
were Anthropic Claude, OpenAI GPT-4, and Google Gemini.

## Decision
I selected Anthropic Claude (claude-sonnet-4-20250514) as the LLM provider,
accessed via the `langchain-anthropic` integration package.

## Rationale
- Claude demonstrates strong performance on structured reasoning tasks
  involving correlated signals and multi-step analysis
- Claude's extended context window handles complex incident payloads
  with multiple firing monitors without truncation
- `langchain-anthropic` is a first-class LangChain integration,
  maintaining full compatibility with LangGraph's node execution model
- Anthropic's API pricing is competitive at the scale of an eval dataset

## Alternatives Considered
- OpenAI GPT-4o — strong reasoning capability but higher cost at scale
  and vendor concentration risk given Microsoft's investment stake
- Google Gemini — competitive context window and pricing, strong
  candidate for future evaluation especially in GCP-native deployments

## Consequences
- The agent is currently configured for Claude but is not locked to it
- Switching to OpenAI, Gemini, or any other provider requires changing
  one line in `nodes.py` where the LLM is instantiated — the LangGraph
  orchestration layer is provider-agnostic by design (see ADR-004)
- API key must be provisioned and stored as `ANTHROPIC_API_KEY` in
  `.env` locally and as a Kubernetes secret in production
