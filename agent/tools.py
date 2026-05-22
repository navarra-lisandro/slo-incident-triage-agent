"""
agent/tools.py

LangChain tools available to the agent nodes.

Tools:
  read_runbook    reads a service runbook from data/runbooks/
                  used by query_runbook node to retrieve
                  relevant remediation steps

Design decisions:
  - Runbooks are Markdown files scoped per service (ADR-011)
  - Tool returns raw Markdown — Claude interprets content
  - Missing runbook returns a graceful fallback message
    rather than raising an exception
"""

import os
from langchain_core.tools import tool


RUNBOOKS_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "runbooks")


@tool
def read_runbook(service: str) -> str:
    """
    Read the runbook for a given service.
    Returns the runbook content as a Markdown string.
    If no runbook exists for the service, returns a fallback message.

    Args:
        service: the service name (e.g. payments-service, auth-service)

    Returns:
        str: runbook content or fallback message
    """
    runbook_path = os.path.join(RUNBOOKS_DIR, f"{service}.md")

    if not os.path.exists(runbook_path):
        return (
            f"[NO RUNBOOK FOUND] No runbook exists for service '{service}'. "
            f"Recommend creating data/runbooks/{service}.md. "
            f"Proceed with general SRE remediation principles."
        )

    with open(runbook_path, "r") as f:
        return f.read()
