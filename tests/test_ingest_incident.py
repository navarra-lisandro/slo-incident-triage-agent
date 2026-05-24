"""
tests/test_ingest_incident.py

Unit tests for ingest_incident node.
Integration of _normalize_monitor, _normalize_window, and _merge_tags.
Uses shared fixtures from conftest.py.
No LLM calls, no HTTP calls.
"""

from agent.nodes.deterministic import (
    ingest_incident,
)

# ---------------------------------------------------------------------------
# ingest_incident tests
# ---------------------------------------------------------------------------
class TestIngestIncident:

    def test_known_payload_no_unknown_values(self, base_state):
        result = ingest_incident(base_state)
        assert result["has_unknown_values"] is False
        assert result["normalization_warnings"] == []

    def test_firing_monitors_normalized(self, base_state):
        result = ingest_incident(base_state)
        monitor = result["firing_monitors"][0]
        assert monitor["status"] == "firing"
        assert monitor["type"] == "performance"
        assert monitor["signal"] == "latency"

    def test_quiet_monitors_normalized(self, base_state):
        result = ingest_incident(base_state)
        monitor = result["quiet_monitors"][0]
        assert monitor["status"] == "healthy"
        assert monitor["type"] == "performance"
        assert monitor["signal"] == "errors"

    def test_window_translated_to_seconds(self, base_state):
        result = ingest_incident(base_state)
        assert result["raw_slo"]["window_seconds"] == 3600

    def test_tags_unified_across_sources(self, base_state):
        result = ingest_incident(base_state)
        unified = result["unified_tags"]
        assert "service" in unified
        assert "env" in unified
        assert "team" in unified

    def test_tag_values_deduplicated(self, base_state):
        # both firing and quiet monitors have team:payments
        result = ingest_incident(base_state)
        assert result["unified_tags"]["team"].count("payments") == 1

    def test_unknown_monitor_value_sets_flag(self, base_state):
        base_state["firing_monitors"][0]["status"] = "critical_unknown"
        result = ingest_incident(base_state)
        assert result["has_unknown_values"] is True
        assert len(result["normalization_warnings"]) > 0

    def test_malformed_tag_skipped_with_warning(self, base_state):
        base_state["firing_monitors"][0]["tags"].append("malformed_no_colon")
        result = ingest_incident(base_state)
        assert any(
            "malformed_no_colon" in w
            for w in result["normalization_warnings"]
        )

    def test_empty_firing_monitors(self, base_state):
        base_state["firing_monitors"] = []
        result = ingest_incident(base_state)
        assert result["firing_monitors"] == []
        assert result["has_unknown_values"] is False

    def test_empty_quiet_monitors(self, base_state):
        base_state["quiet_monitors"] = []
        result = ingest_incident(base_state)
        assert result["quiet_monitors"] == []