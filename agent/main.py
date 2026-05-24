"""
agent/main.py

CLI entry point for the SLO incident triage agent.
Used for local development and testing via make run.

Usage:
  poetry run python agent/main.py --incident data/incidents/p1_payments.json
  make run INCIDENT=data/incidents/p1_payments.json

This entry point loads a pre-assembled incident fixture from disk,
constructs the initial IncidentState, and invokes the graph directly
without going through FastAPI. Useful for:
  - Local development and debugging
  - Verifying agent behavior before running LangSmith evals
  - Inspecting the full state at each node via LangSmith traces

See ADR-013 for the FastAPI vs direct graph invocation boundary.
See ADR-014 for sync vs async invocation rationale.
See Friction Log #5 for pre-assembled payload context.

dotenv loading:
  Reference: https://pypi.org/project/python-dotenv/
  load_dotenv() must be called before any LangSmith or Anthropic
  client initialization. This is the same pattern used in
  options-income-advisor-agent and documented in Friction Log
  (load_dotenv timing issue).
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# load_dotenv must be called before importing agent modules
# that initialize LangSmith or Anthropic clients
# Reference: options-income-advisor-agent friction log — load_dotenv timing
load_dotenv()

from agent.graph import graph  # noqa: E402


def load_incident_fixture(path: str) -> dict[str, Any]:
    """
    Load a pre-assembled incident fixture from a JSON file.
    Returns the raw dict ready for initial state construction.

    See data/incidents/ for available fixture files.
    See Friction Log #5 for pre-assembly context.
    """
    fixture_path = Path(path)
    if not fixture_path.exists():
        print(f"[ERROR] Incident fixture not found: {path}")
        sys.exit(1)

    with open(fixture_path, "r") as f:
        return json.load(f)


def build_initial_state(fixture: dict[str, Any]) -> dict[str, Any]:
    """
    Construct the initial IncidentState dict from a fixture.
    Mirrors the mapping in api.py triage_incident endpoint.
    ingest_incident handles semantic normalization downstream.
    Per ADR-013: mapping is the entry point's responsibility.
    """
    return {
        "incident_id": fixture["incident_id"],
        "triggered_at": fixture["triggered_at"],
        "service": fixture["service"],
        "raw_slo": fixture["slo"],
        "firing_monitors": fixture["firing_monitors"],
        "quiet_monitors": fixture.get("quiet_monitors", []),
        # initialize optional fields to safe defaults
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


def print_summary(result: dict[str, Any]) -> None:
    """
    Print the incident summary to stdout in a readable format.
    """
    summary = result.get("incident_summary")
    if not summary:
        print("[ERROR] Agent completed but produced no incident summary")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("INCIDENT TRIAGE SUMMARY")
    print("=" * 60)
    print(f"Title:       {summary['title']}")
    print(f"Severity:    {summary['severity']}")
    print(f"Service:     {summary['service']}")
    print(f"Budget:      {summary['budget_state']}")
    if summary.get("time_to_exhaustion_minutes"):
        print(f"Time left:   {summary['time_to_exhaustion_minutes']} minutes")
    print(f"Pattern:     {summary['failure_pattern']}")
    if summary.get("cloud_provider_impact"):
        print(f"Cloud:       {summary['cloud_provider_impact']}")
    print()
    print("Justification:")
    print(f"  {summary['severity_justification']}")
    print()
    print("Narrative:")
    print(f"  {summary['summary_narrative']}")
    print()
    print(f"Responsible teams: {', '.join(summary['responsible_teams'])}")
    print(f"Downstream impact: {', '.join(summary['downstream_impact'])}")
    print()
    print("Recommended steps:")
    for i, step in enumerate(summary["recommended_steps"], 1):
        print(f"  {i}. {step}")
    if summary.get("includes_failover"):
        print("  [includes failover options]")
    if summary.get("normalization_warnings"):
        print()
        print("Normalization warnings:")
        for w in summary["normalization_warnings"]:
            print(f"  [WARN] {w}")
    print()
    print(f"Created at:  {summary['created_at']}")
    print("=" * 60)


def main() -> None:
    """
    CLI entry point. Parses arguments, loads fixture, invokes graph,
    prints summary.
    """
    parser = argparse.ArgumentParser(
        description="SLO Incident Triage Agent — CLI entry point"
    )
    parser.add_argument(
        "--incident",
        required=True,
        help="Path to pre-assembled incident fixture JSON file",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print full agent state on completion",
    )
    args = parser.parse_args()

    print(f"[OK] Loading incident fixture: {args.incident}")
    fixture = load_incident_fixture(args.incident)

    print(f"[OK] Building initial state for service: {fixture.get('service')}")
    initial_state = build_initial_state(fixture)

    print(f"[OK] Invoking agent graph...")
    started_at = datetime.now(timezone.utc)

    try:
        result = graph.invoke(initial_state)
    except Exception as e:
        print(f"[ERROR] Agent invocation failed: {type(e).__name__}: {e}")
        sys.exit(1)

    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    print(f"[OK] Agent completed in {elapsed:.1f}s")

    if args.verbose:
        print("\n[FULL STATE]")
        print(json.dumps(result, indent=2, default=str))

    print_summary(result)


if __name__ == "__main__":
    main()
