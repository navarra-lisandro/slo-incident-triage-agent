# ADR-006: Deterministic vs LLM Node Separation

## Status
Accepted

## Context
The agent graph contains multiple nodes that perform different categories
of work — some nodes parse and calculate, others reason and synthesize.
A key architectural decision is whether to use the LLM for all node
execution or to separate deterministic computation from LLM reasoning.

## Decision
I explicitly separate nodes into two categories:

Deterministic nodes — pure Python, no LLM call
  ingest_incident       parse and normalize the incoming payload
  assess_slo_impact     calculate derived metrics from burn rate
  check_cloud_status    fetch and parse provider status pages

LLM nodes — Claude reasoning
  triage_firing_signals correlate signals and identify patterns
  classify_severity     P1-P4 judgment with written justification
  query_runbook         match symptom pattern to runbook steps
  generate_remediation  synthesize MTTC-focused action plan
  draft_summary         structured output for Slack/PagerDuty

## Rationale
- LLMs are unreliable at arithmetic and deterministic logic. Asking
  Claude to calculate time-to-exhaustion from burn rate introduces
  unnecessary variance into a calculation that has an exact answer.
- Deterministic nodes produce stable, testable outputs. A unit test
  can assert that a burn rate of 6.2x with 12.4% budget remaining
  always produces time_to_exhaustion of 120 minutes. No LLM call,
  no variance, no cost.
- LLM nodes receive pre-calculated, normalized inputs from deterministic
  nodes. This keeps Claude focused on judgment and reasoning rather
  than arithmetic and parsing — tasks where it adds the most value.
- LangSmith trace granularity is maximized. Each node produces a
  discrete trace entry. When a severity classification is wrong,
  the trace immediately shows whether the error originated in a
  deterministic calculation or in Claude's reasoning.
- Token cost is minimized. Deterministic work costs zero tokens.

## Derived Metrics Produced by Deterministic Nodes

assess_slo_impact produces:
  time_to_exhaustion_minutes  float   (budget_remaining / burn_rate) * window
  urgency_score               str     HIGH / MEDIUM / LOW (rule-based thresholds)
  budget_state                str     HEALTHY / DEGRADED / EXHAUSTED / DEBT

budget_state rules:
  HEALTHY    error_budget_remaining > 50%
  DEGRADED   error_budget_remaining > 0% and <= 50%
  EXHAUSTED  error_budget_remaining = 0%
  DEBT       error_budget_remaining < 0%

## Consequences
- Deterministic nodes must be unit tested — they have no variance
  to hide behind
- LLM nodes are evaluated via LangSmith experiments, not unit tests
- Any logic that has a correct answer belongs in a deterministic node
- Any logic that requires judgment, synthesis, or contextual reasoning
  belongs in an LLM node
- This separation is enforced by convention, not by the framework —
  discipline is required to maintain it
