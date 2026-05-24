"""
agent/graph.py

LangGraph graph definition for the SLO incident triage agent.

Graph topology (ADR-011):
  Full path (9 nodes):
    ingest_incident -> normalize_incident -> assess_slo_impact ->
    check_cloud_status -> triage_firing_signals -> classify_severity ->
    query_runbook -> generate_remediation -> draft_summary

  Happy path (7 nodes — known provider, no cloud tag):
    ingest_incident -> assess_slo_impact -> triage_firing_signals ->
    classify_severity -> query_runbook -> generate_remediation ->
    draft_summary

Two conditional edges:
  Edge 1: after ingest_incident
    has_unknown_values = True  -> normalize_incident
    has_unknown_values = False -> assess_slo_impact

  Edge 2: after assess_slo_impact
    cloud tag present          -> check_cloud_status
    cloud tag absent           -> triage_firing_signals

StateGraph pattern:
  Reference: https://langchain-ai.github.io/langgraph/reference/graphs/

add_conditional_edges pattern:
  Reference: https://langchain-ai.github.io/langgraph/tutorials/rag/
             langgraph_adaptive_rag/

Routing function Literal type pattern:
  Reference: https://harshaselvi.medium.com/building-ai-agents-using-langgraph-part-1-f8c2d92c8da1
"""

from typing import Literal

from langgraph.graph import StateGraph, START, END

from agent.state import IncidentState
from agent.nodes import (
    ingest_incident,
    normalize_incident,
    assess_slo_impact,
    check_cloud_status,
    triage_firing_signals,
    classify_severity,
    query_runbook,
    generate_remediation,
    draft_summary,
)


# ---------------------------------------------------------------------------
# Routing functions — conditional edges
# Reference: https://langchain-ai.github.io/langgraph/reference/graphs/
# ---------------------------------------------------------------------------

def route_after_ingest(
    state: IncidentState,
) -> Literal["normalize_incident", "assess_slo_impact"]:
    """
    Conditional edge 1 — after ingest_incident.

    Routes to normalize_incident if unknown values were flagged.
    Routes directly to assess_slo_impact on the happy path.

    See ADR-006 for the normalize_incident exception rationale.
    See ADR-011 for the full graph topology.
    """
    if state.get("has_unknown_values", False):
        return "normalize_incident"
    return "assess_slo_impact"


def route_after_assess(
    state: IncidentState,
) -> Literal["check_cloud_status", "triage_firing_signals"]:
    """
    Conditional edge 2 — after assess_slo_impact (and normalize_incident).

    Routes to check_cloud_status if a cloud:* tag is present.
    Routes directly to triage_firing_signals when no cloud tag present.

    Skipping check_cloud_status keeps the happy path fast and avoids
    unnecessary external HTTP calls. See ADR-009 and ADR-011.
    """
    unified_tags = state.get("unified_tags", {})
    cloud_tags = unified_tags.get("cloud", [])
    if cloud_tags:
        return "check_cloud_status"
    return "triage_firing_signals"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_graph() -> StateGraph:
    """
    Construct and compile the SLO incident triage agent graph.

    Returns a compiled LangGraph StateGraph ready for invocation.

    Usage:
      graph = build_graph()
      result = graph.invoke(initial_state)

    StateGraph pattern:
      Reference: https://langchain-ai.github.io/langgraph/reference/graphs/
    """
    builder = StateGraph(IncidentState)

    # ------------------------------------------------------------------
    # Register nodes
    # ------------------------------------------------------------------
    builder.add_node("ingest_incident", ingest_incident)
    builder.add_node("normalize_incident", normalize_incident)
    builder.add_node("assess_slo_impact", assess_slo_impact)
    builder.add_node("check_cloud_status", check_cloud_status)
    builder.add_node("triage_firing_signals", triage_firing_signals)
    builder.add_node("classify_severity", classify_severity)
    builder.add_node("query_runbook", query_runbook)
    builder.add_node("generate_remediation", generate_remediation)
    builder.add_node("draft_summary", draft_summary)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    builder.add_edge(START, "ingest_incident")

    # ------------------------------------------------------------------
    # Conditional edge 1 — after ingest_incident
    # has_unknown_values -> normalize_incident or assess_slo_impact
    # ------------------------------------------------------------------
    builder.add_conditional_edges(
        "ingest_incident",
        route_after_ingest,
        {
            "normalize_incident": "normalize_incident",
            "assess_slo_impact": "assess_slo_impact",
        },
    )

    # ------------------------------------------------------------------
    # normalize_incident always proceeds to assess_slo_impact
    # ------------------------------------------------------------------
    builder.add_edge("normalize_incident", "assess_slo_impact")

    # ------------------------------------------------------------------
    # Conditional edge 2 — after assess_slo_impact
    # cloud tag present -> check_cloud_status or triage_firing_signals
    # ------------------------------------------------------------------
    builder.add_conditional_edges(
        "assess_slo_impact",
        route_after_assess,
        {
            "check_cloud_status": "check_cloud_status",
            "triage_firing_signals": "triage_firing_signals",
        },
    )

    # ------------------------------------------------------------------
    # check_cloud_status always proceeds to triage_firing_signals
    # ------------------------------------------------------------------
    builder.add_edge("check_cloud_status", "triage_firing_signals")

    # ------------------------------------------------------------------
    # Linear reasoning chain — no more conditional edges
    # ------------------------------------------------------------------
    builder.add_edge("triage_firing_signals", "classify_severity")
    builder.add_edge("classify_severity", "query_runbook")
    builder.add_edge("query_runbook", "generate_remediation")
    builder.add_edge("generate_remediation", "draft_summary")
    builder.add_edge("draft_summary", END)

    return builder.compile()


# ---------------------------------------------------------------------------
# Module-level compiled graph instance
# Imported by api.py and main.py
# ---------------------------------------------------------------------------

graph = build_graph()
