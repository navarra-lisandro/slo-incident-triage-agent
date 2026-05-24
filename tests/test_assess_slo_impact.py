"""
tests/test_assess_slo_impact.py

Unit tests for assess_slo_impact node.
Covers all budget_state, urgency_score, and time_to_exhaustion
calculation paths including boundary conditions.
No LLM calls, no HTTP calls, no fixtures required.
"""

from agent.nodes.deterministic import (
    assess_slo_impact,
)

# ---------------------------------------------------------------------------
# assess_slo_impact tests
# ---------------------------------------------------------------------------

class TestAssessSloImpact:

    def _make_state(self, burn_rate, budget_remaining, window_seconds=3600):
        return {
            "raw_slo": {
                "burn_rate": burn_rate,
                "error_budget_remaining_pct": budget_remaining,
                "window_seconds": window_seconds,
            }
        }

    def test_high_burn_rate_urgency_score(self):
        result = assess_slo_impact(self._make_state(6.2, 12.4))
        assert result["urgency_score"] == "HIGH"

    def test_medium_burn_rate_urgency_score(self):
        result = assess_slo_impact(self._make_state(3.0, 30.0))
        assert result["urgency_score"] == "MEDIUM"

    def test_low_burn_rate_urgency_score(self):
        result = assess_slo_impact(self._make_state(1.5, 60.0))
        assert result["urgency_score"] == "LOW"

    def test_budget_state_healthy(self):
        result = assess_slo_impact(self._make_state(1.0, 75.0))
        assert result["budget_state"] == "HEALTHY"

    def test_budget_state_degraded(self):
        result = assess_slo_impact(self._make_state(3.0, 25.0))
        assert result["budget_state"] == "DEGRADED"

    def test_budget_state_exhausted(self):
        result = assess_slo_impact(self._make_state(5.0, 0.0))
        assert result["budget_state"] == "EXHAUSTED"

    def test_budget_state_debt(self):
        result = assess_slo_impact(self._make_state(8.0, -5.0))
        assert result["budget_state"] == "DEBT"

    def test_time_to_exhaustion_calculated(self):
        # burn_rate 6.2x, budget 12.4%, window 3600s (60min)
        # (0.124 / 6.2) * 60 = ~1.2 minutes
        result = assess_slo_impact(self._make_state(6.2, 12.4, 3600))
        assert result["time_to_exhaustion_minutes"] is not None
        assert result["time_to_exhaustion_minutes"] > 0

    def test_time_to_exhaustion_none_when_exhausted(self):
        result = assess_slo_impact(self._make_state(5.0, 0.0))
        assert result["time_to_exhaustion_minutes"] is None

    def test_time_to_exhaustion_none_when_debt(self):
        result = assess_slo_impact(self._make_state(8.0, -5.0))
        assert result["time_to_exhaustion_minutes"] is None

    def test_time_to_exhaustion_none_when_zero_burn_rate(self):
        result = assess_slo_impact(self._make_state(0.0, 50.0))
        assert result["time_to_exhaustion_minutes"] is None

    def test_urgency_boundary_exactly_5x(self):
        result = assess_slo_impact(self._make_state(5.0, 30.0))
        assert result["urgency_score"] == "HIGH"

    def test_urgency_boundary_exactly_2x(self):
        result = assess_slo_impact(self._make_state(2.0, 30.0))
        assert result["urgency_score"] == "MEDIUM"

    def test_budget_boundary_exactly_50_pct(self):
        result = assess_slo_impact(self._make_state(1.0, 50.0))
        assert result["budget_state"] == "DEGRADED"

    def test_6h_window_affects_time_to_exhaustion(self):
        result_1h = assess_slo_impact(self._make_state(6.2, 12.4, 3600))
        result_6h = assess_slo_impact(self._make_state(6.2, 12.4, 21600))
        assert result_6h["time_to_exhaustion_minutes"] > result_1h["time_to_exhaustion_minutes"]
