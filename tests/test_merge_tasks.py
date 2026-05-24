"""
tests/test_merge_tags.py

Unit tests for _merge_tags helper.
Covers single/multi source merging, deduplication, malformed tags,
notification literals, and key casing normalization.
"""

from agent.nodes.deterministic import (
    _merge_tags,
)

# ---------------------------------------------------------------------------
# _merge_tags tests
# ---------------------------------------------------------------------------
class TestMergeTags:

    def test_single_tag_merged(self):
        unified = {}
        warnings = []
        _merge_tags(["team:payments"], unified, warnings)
        assert unified == {"team": ["payments"]}
        assert warnings == []

    def test_multiple_tags_merged(self):
        unified = {}
        warnings = []
        _merge_tags(
            ["team:payments", "env:prod", "service:checkout"],
            unified,
            warnings
        )
        assert unified["team"] == ["payments"]
        assert unified["env"] == ["prod"]
        assert unified["service"] == ["checkout"]

    def test_duplicate_values_deduplicated(self):
        unified = {}
        warnings = []
        _merge_tags(["team:payments", "team:payments"], unified, warnings)
        assert unified["team"] == ["payments"]

    def test_multiple_values_same_key_preserved(self):
        unified = {}
        warnings = []
        _merge_tags(
            ["team:payments", "team:backoffice"],
            unified,
            warnings
        )
        assert "payments" in unified["team"]
        assert "backoffice" in unified["team"]
        assert len(unified["team"]) == 2

    def test_malformed_tag_skipped_with_warning(self):
        unified = {}
        warnings = []
        _merge_tags(["malformed_tag"], unified, warnings)
        assert unified == {}
        assert len(warnings) == 1
        assert "malformed_tag" in warnings[0]

    def test_multi_source_merge(self):
        unified = {}
        warnings = []
        _merge_tags(["service:payments", "env:prod"], unified, warnings)
        _merge_tags(["team:payments", "env:prod"], unified, warnings)
        assert unified["service"] == ["payments"]
        assert unified["env"] == ["prod"]
        assert unified["team"] == ["payments"]

    def test_key_lowercased(self):
        unified = {}
        warnings = []
        _merge_tags(["TEAM:payments"], unified, warnings)
        assert "team" in unified
        assert "TEAM" not in unified

    def test_value_preserves_casing(self):
        unified = {}
        warnings = []
        _merge_tags(["notify:@Payments-Team@company.com"], unified, warnings)
        assert unified["notify"] == ["@Payments-Team@company.com"]

    def test_notification_literal_merged(self):
        unified = {}
        warnings = []
        _merge_tags(
            ["notify:@payments-team@company.com", "pagerduty:payments-escalation"],
            unified,
            warnings
        )
        assert "notify" in unified
        assert "pagerduty" in unified

