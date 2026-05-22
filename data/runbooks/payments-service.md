# Runbook: payments-service

## Service Overview
payments-service handles all payment processing including authorization,
capture, and refund flows. It is a critical path service — degradation
directly impacts revenue and customer trust.

## SLO
- Availability target: 99.9%
- Latency target: p99 < 500ms
- Error rate target: < 0.5%

## Signal-Specific Runbooks

### Latency Degradation (p99 > 500ms)

**Immediate steps:**
1. Check CPU and memory utilization on all payments-service pods
   `kubectl top pods -n payments`
2. Check database connection pool utilization
   `kubectl exec -n payments deploy/payments-service -- curl localhost:8080/metrics | grep db_pool`
3. Check for recent deployments in the last 30 minutes
   `kubectl rollout history deploy/payments-service -n payments`
4. Check upstream dependencies — payment-gateway and fraud-service latency
5. If connection pool exhausted: increase pool size or scale pods
   `kubectl scale deploy/payments-service --replicas=6 -n payments`
6. If recent deployment: consider rollback
   `kubectl rollout undo deploy/payments-service -n payments`

**Escalation:** page payments-oncall if p99 > 1000ms for > 5 minutes

---

### Error Rate Elevated (> 0.5%)

**Immediate steps:**
1. Check error logs for dominant error type
   `kubectl logs -n payments deploy/payments-service --tail=100 | grep ERROR`
2. Check payment-gateway status — most errors originate upstream
3. Check database connectivity and replication lag
4. If 5xx errors dominating: check for upstream timeouts
5. If 4xx errors dominating: check for schema or API contract changes
6. If errors > 5%: enable circuit breaker to prevent cascade
   `kubectl set env deploy/payments-service CIRCUIT_BREAKER_ENABLED=true -n payments`

**Escalation:** page payments-oncall immediately if error rate > 2%

---

### Saturation (CPU > 80% or Memory > 85%)

**Immediate steps:**
1. Identify which pods are saturated
   `kubectl top pods -n payments --sort-by=cpu`
2. Check if saturation is correlated with traffic spike
3. Scale horizontally if traffic-driven
   `kubectl scale deploy/payments-service --replicas=8 -n payments`
4. Check for memory leaks if memory saturation without traffic increase
5. If HPA not triggering: check HPA configuration
   `kubectl describe hpa payments-service -n payments`

**Escalation:** page payments-oncall if saturation persists > 10 minutes

---

### Cloud Provider Failover (AWS us-east-1 degraded)

**Immediate steps:**
1. Confirm AWS degradation at https://status.aws.amazon.com
2. Check which AWS services are affected (RDS, ElastiCache, Lambda)
3. If RDS affected: promote read replica in us-west-2
4. If ElastiCache affected: fall back to database reads temporarily
5. Update DNS to route traffic to us-west-2 endpoint
   `aws route53 change-resource-record-sets --hosted-zone-id Z123 --change-batch file://failover.json`
6. Notify downstream teams: order-api, auth-service

**Escalation:** page payments-oncall and infrastructure-oncall immediately
