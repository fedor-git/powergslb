# Distributed Deployment with Redis and Leader Election

## Overview

PowerGSLB supports running multiple pods/instances behind a load balancer for **high availability**. All pods serve DNS queries from a shared database and must return **consistent health status** regardless of which pod handles the request.

```
                    ┌─────────────────┐
                    │  Load Balancer  │
                    │  (DNS on :53)   │
                    └────────┬────────┘
                             │
         ┌───────────────────┼───────────────────┐
         │                   │                   │
    ┌────▼────┐         ┌────▼────┐         ┌────▼────┐
    │  Pod 1  │         │  Pod 2  │         │  Pod 3  │
    │ LEADER  │         │STANDBY  │         │STANDBY  │
    └────┬────┘         └────┬────┘         └────┬────┘
         │                   │                   │
         └───────────────────┼───────────────────┘
                             │
                      ┌──────▼──────┐
                      │    Redis    │
                      │  (Redlock)  │
                      └─────────────┘
```

## How It Works

### 1. Leader Election (Redis Redlock)

**Only one pod** acquires the **leader lock** in Redis and runs health checks:

- **Leader**: Runs the Monitor thread, performs health checks every N seconds, updates health status in Redis
- **Standby**: Wait for leadership, serve DNS queries using data from Redis
- **Lock TTL**: 30 seconds (configurable)
- **Renewal**: Every 10 seconds (configurable)
- **Automatic Failover**: If leader pod crashes, standby acquires lock within ~30 seconds

### 2. Distributed Status Registry

Health status is stored in a **Redis Set** shared by all pods:

```
Redis key: "powergslb:health:down"
Value: Set of content_ids currently DOWN

All pods read from this set when serving DNS queries
```

### 3. Consistent DNS Answers

When a pod serves a DNS query:
1. Reads health status from Redis Set
2. Filters records based on health status
3. Returns the same answer regardless of which pod handles it

### 4. Graceful Degradation

If Redis becomes unavailable:
- Health checks continue to run
- All records treated as **UP** (safe mode)
- DNS queries succeed with all backends available
- Automatic recovery when Redis comes back online

## Configuration

Enable Redis in `build/powergslb.toml`:

```toml
[redis]
host = "redis"            # or "dragonfly" for Redis-compatible cluster
port = 6379
db = 0
connection_timeout = 2
socket_timeout = 2

[leader_election]
enabled = true
lock_key = "powergslb:monitor:leader"
ttl = 30                  # Lease time (seconds)
renewal_interval = 10     # How often leader renews (must be < ttl)
acquisition_retry_interval = 5  # Retry acquiring if not leader
```

## Environment Variables Override

```bash
# Redis connection
POWERGSLB_REDIS_HOST=redis.example.com
POWERGSLB_REDIS_PORT=6379
POWERGSLB_REDIS_DB=0

# Leader election
POWERGSLB_LEADER_ELECTION_ENABLED=true
POWERGSLB_LEADER_ELECTION_TTL=30
POWERGSLB_LEADER_ELECTION_RENEWAL_INTERVAL=10
```

## Key Concepts

### Lock Ownership Verification

The leader renewal uses atomic Lua script to verify ownership:

```python
# Only the pod holding the lock can renew it
# Another pod cannot steal the lock before TTL expires
def renew(self) -> bool:
    """Atomically verify lock ownership and renew TTL."""
    script = """
    local current = redis.call('GET', KEYS[1])
    if current == ARGV[1] then
        return redis.call('EXPIRE', KEYS[1], ARGV[2])
    end
    return 0
    """
    return self.redis_client.eval(script, 1, self.lock_key, 
                                  self._leader_json(), self.ttl)
```

### Renewal Loop (Background Thread)

The leader has a background thread that renews every 10 seconds:

```python
def __renewal_loop(self):
    """Background thread renews leader lock every 10s."""
    while not self.__renewal_stop.is_set():
        if self.is_leader and self.redis_client:
            success = self.renew()
            if not success:
                logging.error('Lost leadership during renewal')
                self.is_leader = False
        self.__renewal_stop.wait(self.renewal_interval)
```

### Standby Acquisition Loop

Standby pods retry acquiring the lock every 5 seconds:

```python
def wait_for_leadership(self) -> None:
    """Block until this pod acquires leadership."""
    while not self.is_leader:
        if self.acquire():
            logging.info('Acquired leadership (pod=%s)', self.pod_id)
            self.__start_renewal()
            return
        time.sleep(self.acquisition_retry)
```

## Deployment Scenarios

### Single-Pod (Development)

Disable leader election:

```toml
[leader_election]
enabled = false
```

Pod runs health checks locally.

### Multi-Pod (High Availability)

```toml
[leader_election]
enabled = true
```

3+ pods behind a load balancer, sharing Redis.

### Dragonfly (Redis-Compatible)

Use **Dragonfly** instead of Redis for better performance:

```toml
[redis]
host = "dragonfly"  # Dragonfly is Redis-compatible
port = 6379
```

## Monitoring

### Check Pod Leadership Status

```bash
# Which pod is the leader?
kubectl logs -n powergslb -l app=powergslb | grep "leadership"

# PowerGSLB logs:
# INFO: Acquired leadership (pod=powergslb-0)
# INFO: Leader acquired leadership, starting monitor manager
# INFO: Monitor acquired leadership, starting monitor manager
```

### Check Redis Health Status

```bash
# Connect to Redis
redis-cli -h redis.powergslb.svc.cluster.local

# View down records
127.0.0.1:6379> SMEMBERS powergslb:health:down
1) "12345"  # content_id of down record

# View leader lock
127.0.0.1:6379> GET powergslb:monitor:leader
"{\"pod_id\": \"powergslb-0\", \"timestamp\": 1234567890}"
```

### Prometheus Metrics

(Future: Add custom metrics for leader election state)

## Troubleshooting

### Multiple Pods Becoming Leaders

**Problem**: Two pods think they're leaders simultaneously.

**Cause**: Renewal Lua script didn't verify ownership.

**Solution**: Ensure `LeaderElection.renew()` uses atomic Lua verification.

### Standby Never Acquires Leadership

**Problem**: Backup pods stuck waiting forever.

**Cause**: Leader lock never expires, renewal loop crashed.

**Solution**: Check renewal thread is running, verify leader pod health.

### DNS Inconsistency Between Pods

**Problem**: Pods return different answers for same query.

**Cause**: Health status not synced to Redis.

**Solution**: Verify Redis connectivity, check `powergslb:health:down` key.

### Redis Unavailable - No Fallback

**Problem**: Pods go down when Redis is unreachable.

**Cause**: `fallback_if_redis_unavailable` not enabled.

**Solution**: Set to true for graceful degradation:

```toml
[leader_election]
fallback_if_redis_unavailable = true
```

## Performance

- **Lock Acquisition**: ~10ms (Redis round-trip)
- **Lock Renewal**: ~10ms every 10 seconds
- **Health Status Lookup**: ~5ms per query (Redis SMEMBERS on small set)
- **Failover Time**: ~30 seconds (until old lock TTL expires)

## Security Considerations

1. **Redis Access**: Use ACL or network isolation (VPC)
2. **Lock Key Collision**: Use unique `lock_key` if sharing Redis
3. **TTL Too Long**: Increases failover time if leader crashes
4. **TTL Too Short**: May cause frequent leadership flaps

## References

- [Redis Redlock](https://redis.io/docs/latest/develop/use/patterns/distributed-locks/)
- [Dragonfly (Redis-compatible)](https://www.dragonflydb.io/)
- [Kubernetes StatefulSet](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/)
