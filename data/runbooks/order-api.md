# Runbook: order-api

## Service Overview
order-api handles order creation, modification, and status tracking
for trading and transaction workflows. It depends on payments-service,
auth-service, and external market data feeds.

## SLO
- Availability target: 99.9%
- Latency target: p99 < 800ms
- Error rate target: < 1%

## Signal-Specific Runbooks

### Latency Degradation (p99 > 800ms)

**Immediate steps:**
1. Check upstream dependency latency — payments-service and auth-service
2. Check market data feed latency and connection status
   `kubectl logs -n orders deploy/order-api --tail=100 | grep market_data`
3. Check database query performance
   `kubectl exec -n orders deploy/order-api -- curl localhost:8080/metrics | grep db_query_duration`
4. Check for order processing backlog
   `kubectl exec -n orders deploy/order-api -- curl localhost:8080/metrics | grep queue_depth`
5. If upstream latency: follow upstream service runbook
6. If queue depth elevated: scale order processing workers
   `kubectl scale deploy/order-worker --replicas=8 -n orders`

**Escalation:** page orders-oncall if p99 > 2000ms for > 3 minutes

---

### Error Rate Elevated (> 1%)

**Immediate steps:**
1. Check error classification — upstream errors vs internal errors
   `kubectl logs -n orders deploy/order-api --tail=200 | grep ERROR`
2. Check payments-service error rate — order errors often originate there
3. Check auth-service error rate — failed auth produces order errors
4. Check market data feed connectivity
5. If payments errors > 2%: activate order hold mode to prevent bad orders
   `kubectl set env deploy/order-api ORDER_HOLD_MODE=true -n orders`
6. Notify trading desk if order processing is impacted

**Escalation:** page orders-oncall immediately if error rate > 3%

---

### Cloud Provider Failover (AWS degraded)

**Immediate steps:**
1. Confirm AWS degradation at https://status.aws.amazon.com
2. Identify affected services — RDS, SQS, ElastiCache
3. If SQS affected: switch to direct database writes (bypass queue)
   `kubectl set env deploy/order-api QUEUE_BYPASS=true -n orders`
4. If RDS affected: promote read replica and update connection string
5. Check payments-service failover status — coordinate failover timing
6. Notify trading desk and risk management of potential order delays
7. If full region failure: activate DR runbook
   `https://wiki.internal/runbooks/order-api-dr`

**Escalation:** page orders-oncall and infrastructure-oncall immediately
