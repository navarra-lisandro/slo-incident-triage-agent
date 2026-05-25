"""
evals/run_evals.py

LangSmith evaluation runner for the SLO incident triage agent.

Creates a dataset of 5 golden examples from data/incidents/ fixtures,
runs the agent against each example, and scores the results using
LLM-as-a-judge evaluators.

Dataset: slo-incident-triage-agent-golden
  5 examples covering P1/P2/P3 severity cases across 3 services
  happy path and full path (cloud status check) cases included

Evaluators:
  severity_accuracy     exact match on expected severity (P1/P2/P3/P4)
  budget_state_accuracy exact match on expected budget_state
  remediation_quality   LLM-as-a-judge on remediation plan quality
  summary_quality       LLM-as-a-judge on summary narrative quality

LangSmith evaluate() pattern:
  Reference: https://docs.smith.langchain.com/evaluation/how_to_guides/
             evaluate_llm_application

LangSmith dataset creation pattern:
  Reference: https://docs.smith.langchain.com/evaluation/tutorials/evaluation

LLM-as-a-judge evaluator pattern:
  Reference: https://docs.smith.langchain.com/evaluation/how_to_guides/
             llm_as_judge

Usage:
  poetry run python evals/run_evals.py
  make eval
"""

import json
import os
import sys
from pathlib import Path
from typing import Any
from langsmith import Client
from langsmith.evaluation import evaluate
from langsmith.schemas import Example, Run
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from agent.graph import graph
from dotenv import load_dotenv

# load_dotenv must be called before importing agent modules
# Reference: options-income-advisor-agent friction log — load_dotenv timing
load_dotenv()

# LLM judge client — used by LLM-as-a-judge evaluators
# Initialized once at module level, shared across evaluator calls
# Reference: https://reference.langchain.com/python/integrations/
#            langchain_anthropic/ChatAnthropic/
judge_llm = ChatAnthropic(model="claude-sonnet-4-6", temperature=0)

# ---------------------------------------------------------------------------
# Dataset configuration
# ---------------------------------------------------------------------------

DATASET_NAME = "slo-incident-triage-agent-golden"

# Golden Path Examples — fixture path + expected outputs
# Expected outputs are the ground truth for scoring
GOLDEN_EXAMPLES = [
    {
        "fixture": "data/incidents/p1_payments_resource_exhaustion.json",
        "expected_severity": "P1",
        "expected_budget_state": "DEGRADED",
        "description": "P1 payments-service resource exhaustion — latency + saturation + synthetics firing",
    },
    {
        "fixture": "data/incidents/p2_payments_error_rate.json",
        "expected_severity": "P2",
        "expected_budget_state": "DEGRADED",
        "description": "P2 payments-service error rate elevated — single signal, moderate burn",
    },
    {
        "fixture": "data/incidents/p3_auth_latency_spike.json",
        "expected_severity": "P3",
        "expected_budget_state": "HEALTHY",
        "description": "P3 auth-service latency spike — single signal, SLO healthy",
    },
    {
        "fixture": "data/incidents/p3_auth_synthetic_flap.json",
        "expected_severity": "P3",
        "expected_budget_state": "HEALTHY",
        "description": "P3 auth-service synthetic flap — low burn rate, SLO healthy",
    },
    {
        "fixture": "data/incidents/p1_order_api_aws_outage.json",
        "expected_severity": "P1",
        "expected_budget_state": "DEGRADED",
        "description": "P1 order-api AWS outage — full path with cloud status check",
    },
]


# ---------------------------------------------------------------------------
# Dataset management
# Reference: https://docs.smith.langchain.com/evaluation/tutorials/evaluation
# ---------------------------------------------------------------------------

def load_fixture(fixture_path: str) -> dict[str, Any]:
    """Load an incident fixture from disk."""
    path = Path(fixture_path)
    if not path.exists():
        print(f"[ERROR] Fixture not found: {fixture_path}")
        sys.exit(1)
    with open(path, "r") as f:
        return json.load(f)


def build_initial_state(fixture: dict[str, Any]) -> dict[str, Any]:
    """
    Construct the initial IncidentState dict from a fixture.
    Mirrors the mapping in api.py and main.py.
    """
    return {
        "incident_id": fixture["incident_id"],
        "triggered_at": fixture["triggered_at"],
        "service": fixture["service"],
        "raw_slo": fixture["slo"],
        "firing_monitors": fixture["firing_monitors"],
        "quiet_monitors": fixture.get("quiet_monitors", []),
        "unified_tags": {},
        "has_unknown_values": False,
        "normalization_warnings": [],
        "cloud_provider_statuses": [],
        "signal_correlation": None,
        "severity": None,
        "severity_justification": None,
        "runbook_steps": None,
        "remediation_plan": None,
        "incident_summary": None,
        "recent_history": None,
    }


def create_or_get_dataset(client: Client) -> str:
    """
    Create the golden dataset if it does not exist.
    Returns the dataset name for use with evaluate().

    Uses has_dataset() to check existence before creating.
    Avoids duplicate dataset creation on repeated eval runs.
    """
    datasets = list(client.list_datasets(dataset_name=DATASET_NAME))

    if datasets:
        print(f"[OK] Dataset '{DATASET_NAME}' already exists — skipping creation")
        return DATASET_NAME

    print(f"[OK] Creating dataset '{DATASET_NAME}'...")

    dataset = client.create_dataset(
        dataset_name=DATASET_NAME,
        description=(
            "Golden evaluation dataset for the SLO incident triage agent. "
            "5 examples covering P1/P2/P3 severity cases across 3 services. "
            "Includes happy path and full path (cloud status check) cases."
        ),
    )

    examples = []
    for golden in GOLDEN_EXAMPLES:
        fixture = load_fixture(golden["fixture"])
        initial_state = build_initial_state(fixture)
        examples.append({
            "inputs": {"state": initial_state},
            "outputs": {
                "expected_severity": golden["expected_severity"],
                "expected_budget_state": golden["expected_budget_state"],
            },
            "metadata": {
                "description": golden["description"],
                "fixture": golden["fixture"],
            },
        })

    client.create_examples(
        dataset_id=dataset.id,
        examples=examples,
    )

    print(f"[OK] Created {len(examples)} examples in dataset '{DATASET_NAME}'")
    return DATASET_NAME


# ---------------------------------------------------------------------------
# Agent target function
# Reference: https://docs.smith.langchain.com/evaluation/how_to_guides/
#            evaluate_llm_application
# ---------------------------------------------------------------------------

def run_agent(inputs: dict[str, Any]) -> dict[str, Any]:
    """
    Target function for LangSmith evaluate().
    Receives the inputs dict from a dataset example and returns
    the agent output for scoring.

    inputs:  {"state": initial_state_dict}
    outputs: {"severity": "P1", "budget_state": "DEGRADED", ...}
    """
    initial_state = inputs["state"]
    result = graph.invoke(initial_state)
    summary = result.get("incident_summary", {})

    return {
        "severity": summary.get("severity"),
        "budget_state": summary.get("budget_state"),
        "summary_narrative": summary.get("summary_narrative"),
        "recommended_steps": summary.get("recommended_steps", []),
        "failure_pattern": summary.get("failure_pattern"),
        "normalization_warnings": summary.get("normalization_warnings", []),
    }


# ---------------------------------------------------------------------------
# Evaluators
# Reference: https://docs.smith.langchain.com/evaluation/tutorials/evaluation
# LLM-as-a-judge:
#   https://docs.smith.langchain.com/evaluation/how_to_guides/llm_as_judge
# ---------------------------------------------------------------------------

def evaluate_severity_accuracy(run: Run, example: Example) -> dict:
    """
    Exact match evaluator for severity classification.
    Scores 1 if predicted severity matches expected, 0 otherwise.

    This is the most critical evaluator — severity drives paging decisions.
    A wrong severity classification is the most dangerous failure mode.
    """
    predicted = (run.outputs or {}).get("severity", "")
    expected = (example.outputs or {}).get("expected_severity", "")

    score = 1 if predicted == expected else 0
    comment = (
        f"predicted={predicted} expected={expected}"
        if score == 0
        else f"correct: {predicted}"
    )

    return {
        "key": "severity_accuracy",
        "score": float(score),
        "comment": comment,
    }


def evaluate_budget_state_accuracy(run: Run, example: Example) -> dict:
    """
    Exact match evaluator for budget state classification.
    Scores 1 if predicted budget_state matches expected, 0 otherwise.

    Budget state is a deterministic calculation (assess_slo_impact).
    A wrong budget state indicates a problem in the deterministic layer.
    """
    predicted = (run.outputs or {}).get("budget_state", "")
    expected = (example.outputs or {}).get("expected_budget_state", "")

    score = 1 if predicted == expected else 0
    comment = (
        f"predicted={predicted} expected={expected}"
        if score == float(0)
        else f"correct: {predicted}"
    )

    return {
        "key": "budget_state_accuracy",
        "score": float(score),
        "comment": comment,
    }


def evaluate_remediation_quality(run: Run, example: Example) -> dict:
    """
    LLM-as-a-judge evaluator for remediation plan quality.
    Scores 0-1 based on:
      - Are steps specific and actionable (not generic advice)?
      - Are steps ordered by urgency (immediate first)?
      - Are the right signals addressed?
      - Is ownership inferred correctly from context?

    Uses Claude as the judge for consistency with the agent model.

    LLM-as-a-judge pattern:
      Reference: https://docs.smith.langchain.com/evaluation/how_to_guides/
                 llm_as_judge
    """

    steps = (run.outputs or {}).get("recommended_steps", [])
    failure_pattern = (run.outputs or {}).get("failure_pattern", "unknown")
    severity = (run.outputs or {}).get("severity", "unknown")
    description = (example.metadata or {}).get("description", "unknown")

    if not steps:
        return {
            "key": "remediation_quality",
            "score": float(0),
            "comment": "no remediation steps produced",
        }

    prompt = f"""You are evaluating the quality of an incident remediation plan.

Incident description: {description}
Severity: {severity}
Failure pattern: {failure_pattern}

Remediation steps produced:
{chr(10).join(f"{i+1}. {s}" for i, s in enumerate(steps))}

Score the remediation plan on a scale of 0.0 to 1.0 based on:
  - Are steps specific and actionable (not generic advice)?
  - Are immediate actions listed first?
  - Do the steps address the identified failure pattern?
  - Are there appropriate escalation steps?
  - Is the plan appropriately scoped for the severity?

Respond with ONLY a decimal number between 0.0 and 1.0, nothing else."""

    try:
        response = judge_llm.invoke([
            SystemMessage(content="You are an expert SRE evaluating incident response quality."),
            HumanMessage(content=prompt),
        ])
        score = float(response.content.strip())
        score = max(0.0, min(1.0, score))
    except Exception as e:
        score = float(0.5)
        return {
            "key": "remediation_quality",
            "score": float(score),
            "comment": f"judge failed: {type(e).__name__} — defaulting to 0.5",
        }

    return {
        "key": "remediation_quality",
        "score": float(score),
        "comment": f"LLM judge score for {severity} {failure_pattern}",
    }


def evaluate_summary_quality(run: Run, example: Example) -> dict:
    """
    LLM-as-a-judge evaluator for incident summary narrative quality.
    Scores 0-1 based on:
      - Is the narrative clear and concise (3-4 sentences)?
      - Does it explain what is happening and why?
      - Is it appropriate for an on-call engineer at 2am?
      - Does it reference specific signal values?

    LLM-as-a-judge pattern:
      Reference: https://docs.smith.langchain.com/evaluation/how_to_guides/
                 llm_as_judge
    """

    narrative = (run.outputs or {}).get("summary_narrative", "")
    severity = (run.outputs or {}).get("severity", "unknown")
    description = (example.metadata or {}).get("description", "unknown")

    if not narrative:
        return {
            "key": "summary_quality",
            "score": float(0),
            "comment": "no summary narrative produced",
        }

    prompt = f"""You are evaluating the quality of an incident summary narrative.

Incident description: {description}
Severity: {severity}

Summary narrative produced:
{narrative}

Score the summary on a scale of 0.0 to 1.0 based on:
  - Is it clear and concise (appropriate length for an on-call alert)?
  - Does it explain what is happening and why the SLO is burning?
  - Does it reference specific signal values and thresholds?
  - Is it actionable — does it tell the engineer what to do next?
  - Would an on-call engineer find this useful at 2am?

Respond with ONLY a decimal number between 0.0 and 1.0, nothing else."""

    try:
        response = judge_llm.invoke([
            SystemMessage(content="You are an expert SRE evaluating incident communication quality."),
            HumanMessage(content=prompt),
        ])
        score = float(response.content.strip())
        score = max(0.0, min(1.0, score))
    except Exception as e:
        score = float(0.5)
        return {
            "key": "summary_quality",
            "score": float(score),
            "comment": f"judge failed: {type(e).__name__} — defaulting to 0.5",
        }

    return {
        "key": "summary_quality",
        "score": float(score),
        "comment": f"LLM judge score for {severity} narrative",
    }


# ---------------------------------------------------------------------------
# Main evaluation runner
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Run the full evaluation suite against the golden dataset.

    Steps:
      1. Create or verify the golden dataset exists
      2. Run evaluate() against all 5 examples
      3. Print results summary
      4. View full results in LangSmith UI
    """
    print(f"[OK] Starting evaluation — dataset: {DATASET_NAME}")

    client = Client()
    dataset_name = create_or_get_dataset(client)

    print(f"[OK] Running evaluate() against {len(GOLDEN_EXAMPLES)} examples...")

    results = evaluate(
        run_agent,
        data=dataset_name,
        evaluators=[
            evaluate_severity_accuracy,
            evaluate_budget_state_accuracy,
            evaluate_remediation_quality,
            evaluate_summary_quality,
        ],
        experiment_prefix="slo-triage-agent",
        metadata={
            "model": "claude-sonnet-4-6",
            "agent_version": "0.1.0",
            "notes": "baseline evaluation run",
        },
    )

    print("\n[OK] Evaluation complete.")
    print("[OK] View results at: https://smith.langchain.com")
    print(f"[OK] Project: {os.getenv('LANGCHAIN_PROJECT', 'slo-incident-triage-agent')}")
    print("\nResults summary:")

    for result in results:
        print(f"  {result}")


if __name__ == "__main__":
    main()
