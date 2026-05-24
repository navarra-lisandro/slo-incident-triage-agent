"""
tests/test_extract_cloud_services.py

Unit tests for _extract_cloud_services helper.
Covers priority chain: metric inference, tag fallback, and empty fallback with note.
"""

from agent.nodes.deterministic import (
    _extract_cloud_services,
)

# ---------------------------------------------------------------------------
# _extract_cloud_services tests
# ---------------------------------------------------------------------------

class TestExtractCloudServices:

    def test_priority_1_metric_prefix_wins(self):
        unified_tags = {"aws-service": ["S3"]}
        firing_monitors = [
            {"metric": "aws.rds.connections", "tags": []}
        ]
        services, note = _extract_cloud_services("aws", unified_tags, firing_monitors)
        assert services == ["RDS"]
        assert note is None

    def test_priority_2_tag_fallback(self):
        unified_tags = {"aws-service": ["RDS", "S3"]}
        firing_monitors = [
            {"metric": "system.cpu.utilization", "tags": []}
        ]
        services, note = _extract_cloud_services("aws", unified_tags, firing_monitors)
        assert services == ["RDS", "S3"]
        assert note is None

    def test_priority_3_no_signal_returns_empty_with_note(self):
        unified_tags = {}
        firing_monitors = [
            {"metric": "system.cpu.utilization", "tags": []}
        ]
        services, note = _extract_cloud_services("aws", unified_tags, firing_monitors)
        assert services == []
        assert note is not None
        assert "contextually" in note

    def test_multiple_monitors_deduplicates_services(self):
        unified_tags = {}
        firing_monitors = [
            {"metric": "aws.rds.connections", "tags": []},
            {"metric": "aws.rds.read_iops", "tags": []},
        ]
        services, note = _extract_cloud_services("aws", unified_tags, firing_monitors)
        assert services == ["RDS"]
        assert note is None

    def test_multiple_monitors_different_services(self):
        unified_tags = {}
        firing_monitors = [
            {"metric": "aws.rds.connections", "tags": []},
            {"metric": "aws.elasticache.curr_connections", "tags": []},
        ]
        services, note = _extract_cloud_services("aws", unified_tags, firing_monitors)
        assert "RDS" in services
        assert "ElastiCache" in services

