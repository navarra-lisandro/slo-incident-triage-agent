# Runbook: auth-service

## Service Overview
auth-service handles authentication and session management for all
internal and external services. Degradation has wide blast radius —
every service that requires authentication is impacted.

## SLO
- Availability target: 99.95%
- Latency target: p99 < 200ms
- Error rate target: < 0.1%

## Signal-Specific Runbooks

### Latency Degradation (p99 > 200ms)

**Immediate steps:**
1. Check token validation cache hit rate
   `kubectl exec -n auth deploy/auth-service -- curl localhost:8080/metrics | grep cache_hit`
2. Check Redis latency — auth-service is heavily cache-dependent
   `kubectl exec -n auth deploy/redis -- redis-cli --latency`
3. Check JWT signing key rotation — recent rotation can cause cache misses
4. If cache miss rate elevated: warm the cache
   `kubectl exec -n auth deploy/auth-service -- curl -X POST localhost:8080/admin/warm-cache`
5. If Redis latency elevated: check Redis memory and eviction policy
6. Scale auth-service if CPU-bound
   `kubectl scale deploy/auth-service --replicas=6 -n auth`

**Escalation:** page auth-oncall immediately — downstream impact is wide

---

### Synthetic Check Failing

**Immediate steps:**
1. Determine if synthetic failure is global or region-specific
2. Run manual authentication check against production endpoint
   `curl -X POST https://auth.internal/v1/authenticate -d '{"test": true}'`
3. Check if failure is consistent or intermittent (flapping)
4. Check auth-service pod readiness
   `kubectl get pods -n auth -l app=auth-service`
5. If pods not ready: check recent deployment or config change
6. If pods ready but synthetic failing: check network policy changes
7. Flapping synthetics with healthy SLO = likely synthetic configuration
   issue, not service degradation — escalate to observability team

**Escalation:** page auth-oncall if manual check also fails

---

### Error Rate Elevated (> 0.1%)

**Immediate steps:**
1. Classify error type from logs
   `kubectl logs -n auth deploy/auth-service --tail=200 | grep ERROR`
2. Check for expired or rotated secrets
   `kubectl get secrets -n auth`
3. Check downstream database connectivity
4. If token validation errors: check signing key configuration
5. If database errors: check connection pool and database health

**Escalation:** page auth-oncall immediately — any auth errors impact all services
