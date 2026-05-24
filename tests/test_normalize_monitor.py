import pytest
from agent.nodes.deterministic import (
    _normalize_monitor,
)

# ---------------------------------------------------------------------------
# _normalize_monitor tests
# ---------------------------------------------------------------------------
class TestNormalizeMonitor:

    def test_known_datadog_values_translated(self, known_datadog_monitor):
        result, has_unknown, warnings = _normalize_monitor(known_datadog_monitor)
        assert result["status"] == "firing"
        assert result["type"] == "performance"
        assert result["signal"] == "latency"
        assert has_unknown is False
        assert warnings == []

    def test_known_quiet_monitor_translated(self, known_quiet_monitor):
        result, has_unknown, warnings = _normalize_monitor(known_quiet_monitor)
        assert result["status"] == "healthy"
        assert result["type"] == "performance"
        assert result["signal"] == "errors"
        assert has_unknown is False
        assert warnings == []

    @pytest.mark.parametrize("raw_status,expected", [
        ("triggered", "firing"),
        ("ok", "healthy"),
        ("warn", "firing"),
        ("no data", "healthy"),
        ("alerting", "firing"),
        ("pending", "firing"),
        ("no_data", "healthy"),
        ("resolved", "healthy"),
    ])
    def test_status_translation_table(self, raw_status, expected):
        monitor = {
            "type": "apm",
            "signal": "latency",
            "metric": "test.metric",
            "current_value": 1.0,
            "threshold": 1.0,
            "status": raw_status,
            "tags": [],
        }
        result, has_unknown, warnings = _normalize_monitor(monitor)
        assert result["status"] == expected
        assert has_unknown is False

    @pytest.mark.parametrize("raw_type,expected", [
        ("apm", "performance"),
        ("metric", "performance"),
        ("query", "performance"),
        ("synthetics", "synthetic"),
        ("rum", "synthetic"),
    ])
    def test_type_translation_table(self, raw_type, expected):
        monitor = {
            "type": raw_type,
            "signal": "latency",
            "metric": "test.metric",
            "current_value": 1.0,
            "threshold": 1.0,
            "status": "ok",
            "tags": [],
        }
        result, has_unknown, _ = _normalize_monitor(monitor)
        assert result["type"] == expected

    @pytest.mark.parametrize("raw_signal,expected", [
        ("latency", "latency"),
        ("errors", "errors"),
        ("error_rate", "errors"),
        ("traffic", "traffic"),
        ("throughput", "traffic"),
        ("saturation", "saturation"),
        ("cpu", "saturation"),
        ("memory", "saturation"),
        ("synthetic_check", "synthetic_check"),
        ("rum", "synthetic_check"),
    ])
    def test_signal_translation_table(self, raw_signal, expected):
        monitor = {
            "type": "apm",
            "signal": raw_signal,
            "metric": "test.metric",
            "current_value": 1.0,
            "threshold": 1.0,
            "status": "ok",
            "tags": [],
        }
        result, has_unknown, _ = _normalize_monitor(monitor)
        assert result["signal"] == expected

    def test_unknown_status_flagged(self):
        monitor = {
            "type": "apm",
            "signal": "latency",
            "metric": "test.metric",
            "current_value": 1.0,
            "threshold": 1.0,
            "status": "critical_alert",
            "tags": [],
        }
        result, has_unknown, warnings = _normalize_monitor(monitor)
        assert has_unknown is True
        assert len(warnings) == 1
        assert "unknown status value" in warnings[0]
        assert "critical_alert" in warnings[0]

    def test_unknown_type_flagged(self):
        monitor = {
            "type": "custom_monitor",
            "signal": "latency",
            "metric": "test.metric",
            "current_value": 1.0,
            "threshold": 1.0,
            "status": "ok",
            "tags": [],
        }
        result, has_unknown, warnings = _normalize_monitor(monitor)
        assert has_unknown is True
        assert any("unknown type value" in w for w in warnings)

    def test_unknown_signal_flagged(self):
        monitor = {
            "type": "apm",
            "signal": "custom_signal",
            "metric": "test.metric",
            "current_value": 1.0,
            "threshold": 1.0,
            "status": "ok",
            "tags": [],
        }
        result, has_unknown, warnings = _normalize_monitor(monitor)
        assert has_unknown is True
        assert any("unknown signal value" in w for w in warnings)

    def test_multiple_unknown_fields_all_flagged(self):
        monitor = {
            "type": "unknown_type",
            "signal": "unknown_signal",
            "metric": "test.metric",
            "current_value": 1.0,
            "threshold": 1.0,
            "status": "unknown_status",
            "tags": [],
        }
        result, has_unknown, warnings = _normalize_monitor(monitor)
        assert has_unknown is True
        assert len(warnings) == 3

    def test_original_fields_preserved(self, known_datadog_monitor):
        result, _, _ = _normalize_monitor(known_datadog_monitor)
        assert result["metric"] == known_datadog_monitor["metric"]
        assert result["current_value"] == known_datadog_monitor["current_value"]
        assert result["threshold"] == known_datadog_monitor["threshold"]
        assert result["tags"] == known_datadog_monitor["tags"]