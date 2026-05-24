"""
tests/conftest.py

Shared pytest fixtures for the SLO incident triage agent test suite.
Fixtures defined here are automatically available to all test files
without explicit imports — pytest loads conftest.py automatically.

pytest fixtures reference:
  https://docs.pytest.org/en/stable/reference/fixtures.html
"""

import pytest

# ---------------------------------------------------------------------------
# Fixtures — reusable test data
# ---------------------------------------------------------------------------
@pytest.fixture
def known_datadog_monitor():
    return {
        "type": "apm",
        "signal": "latency",
        "metric": "trace.web.request.duration",
        "current_value": 2840.0,
        "threshold": 800.0,
        "status": "triggered",
        "tags": ["team:payments", "env:prod"],
    }


@pytest.fixture
def known_quiet_monitor():
    return {
        "type": "metric",
        "signal": "errors",
        "metric": "trace.web.request.errors",
        "current_value": 0.1,
        "threshold": 1.0,
        "status": "ok",
        "tags": ["team:payments", "env:prod"],
    }


@pytest.fixture
def base_slo():
    return {
        "name": "payments-service availability",
        "target_pct": 99.9,
        "burn_rate": 6.2,
        "error_budget_remaining_pct": 12.4,
        "window": "1h",
        "tags": ["service:payments-service", "env:prod"],
    }


@pytest.fixture
def base_state(base_slo, known_datadog_monitor, known_quiet_monitor):
    return {
        "incident_id": "INC-2024-001",
        "triggered_at": "2024-01-15T02:34:00Z",
        "service": "payments-service",
        "raw_slo": base_slo,
        "firing_monitors": [known_datadog_monitor],
        "quiet_monitors": [known_quiet_monitor],
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