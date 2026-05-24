"""
tests/test_infer_service.py

Unit tests for _infer_service_from_metric helper.
Covers AWS and GCP metric prefix mappings and generic metric fallback.
"""

import pytest
from agent.nodes.deterministic import (
    _infer_service_from_metric,
)

# ---------------------------------------------------------------------------
# _infer_service_from_metric tests
# ---------------------------------------------------------------------------
class TestInferServiceFromMetric:

    @pytest.mark.parametrize("metric,expected_service", [
        ("aws.rds.connections", "RDS"),
        ("aws.elasticache.curr_connections", "ElastiCache"),
        ("aws.ec2.cpu_utilization", "EC2"),
        ("aws.s3.bucket_size_bytes", "S3"),
        ("aws.lambda.duration", "Lambda"),
        ("aws.sqs.number_of_messages_sent", "SQS"),
        ("aws.alb.request_count", "ALB"),
        ("aws.dynamodb.consumed_read_capacity_units", "DynamoDB"),
        ("aws.kinesis.get_records_bytes", "Kinesis"),
        ("aws.eks.node_cpu_utilization", "EKS"),
        ("aws.secretsmanager.resource_count", "Secrets Manager"),
        ("aws.kms.number_of_requests", "KMS"),
        ("kubernetes.cpu.usage.total", "EKS"),
    ])
    def test_aws_metric_prefixes(self, metric, expected_service):
        result = _infer_service_from_metric("aws", metric)
        assert result == expected_service

    @pytest.mark.parametrize("metric,expected_service", [
        ("gcp.cloudsql.database.cpu.utilization", "Cloud SQL"),
        ("gcp.compute.instance.cpu.utilization", "Compute Engine"),
        ("gcp.storage.object_count", "Cloud Storage"),
        ("gcp.pubsub.subscription.num_undelivered_messages", "Pub/Sub"),
        ("gcp.kubernetes.node.cpu.allocatable_utilization", "GKE"),
        ("gcp.run.request_count", "Cloud Run"),
        ("gcp.functions.execution_count", "Cloud Functions"),
        ("gcp.secretmanager.secret_count", "Secret Manager"),
        ("gcp.cloudkms.request_count", "Cloud KMS"),
        ("kubernetes.cpu.usage.total", "GKE"),
    ])
    def test_gcp_metric_prefixes(self, metric, expected_service):
        result = _infer_service_from_metric("gcp", metric)
        assert result == expected_service

    def test_generic_metric_returns_none(self):
        result = _infer_service_from_metric("aws", "system.cpu.utilization")
        assert result is None

    def test_generic_metric_gcp_returns_none(self):
        result = _infer_service_from_metric("gcp", "system.mem.used")
        assert result is None

    def test_unknown_provider_returns_none(self):
        result = _infer_service_from_metric("azure", "azure.sql.dtu_consumption")
        assert result is None