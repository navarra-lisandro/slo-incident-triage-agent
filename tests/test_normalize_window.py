"""
tests/test_normalize_window.py

Unit tests for _normalize_window helper.
Covers known window translations, integer pass-through, and unknown fallback.
"""

import pytest
from agent.nodes.deterministic import (
    _normalize_window,
)

# ---------------------------------------------------------------------------
# _normalize_window tests
# ---------------------------------------------------------------------------
class TestNormalizeWindow:

    @pytest.mark.parametrize("raw_window,expected_seconds", [
        ("1h", 3600),
        ("6h", 21600),
        ("3600", 3600),
        ("21600", 21600),
    ])
    def test_known_windows_translated(self, raw_window, expected_seconds):
        warnings = []
        result, has_unknown = _normalize_window(raw_window, warnings)
        assert result == expected_seconds
        assert has_unknown is False
        assert warnings == []

    def test_unknown_window_defaults_to_3600(self):
        warnings = []
        result, has_unknown = _normalize_window("15m", warnings)
        assert result == 3600
        assert has_unknown is True
        assert len(warnings) == 1
        assert "15m" in warnings[0]

    def test_empty_window_defaults_to_3600(self):
        warnings = []
        result, has_unknown = _normalize_window("", warnings)
        assert result == 3600
        assert has_unknown is True

    def test_integer_string_parsed_directly(self):
        warnings = []
        result, has_unknown = _normalize_window("7200", warnings)
        assert result == 7200
        assert has_unknown is False