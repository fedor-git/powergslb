# Implementation Summary: Redis + Leader Election

## What Changed

PowerGSLB now supports **distributed deployment** with Redis-backed leader election and status registry. This enables **high availability** across multiple pods while maintaining **consistent DNS answers**.

## Architecture Overview

```
┌────────────────────────────────────────────────────────────────┐
│                   PowerGSLB Pod (Any Pod)                      │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              HTTP Request Handler                        │  │
│  │  1. Read health status from Redis (SMEMBERS)             │  │
│  │  2. Filter records by health status                      │  │
│  │  3. Apply routing policy                                 │  │
│  │  4. Return DNS answer                                    │  │
│  └──────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │          MonitorLeaderThread (Only Leader)               │  │
│  │  • Waits for leadership via LeaderElection               │  │
│  │  • Once acquired, starts MonitorManager                  │  │
│  │  • Runs health checks                                    │  │
│  │  • Updates Redis with down records (SADD/SREM)           │  │
│  │  • Renewal thread keeps lock fresh                       │  │
│  └──────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │          LeaderElection (Redis Redlock)                  │  │
│  │  • Acquire: SET NX with TTL (30s default)                │  │
│  │  • Renew: Atomic Lua script every 10s                    │  │
│  │  • Release: On shutdown via DEL                          │  │
│  │  • Fallback: Graceful degradation if Redis down          │  │
│  └──────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │          /healthz Endpoint (No Auth)                     │  │
│  │  • Returns: {"status": "ok", "checks": {...}}            │  │
│  │  • Used by K8s health probes                             │  │
│  │  • Logs suppressed to avoid spam                         │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
                              ▼
                         Redis/Dragonfly
                    Key: powergslb:health:down
                    Value: Set of down content_ids
                    TTL: None (controlled by application)
```

## Key Components

### 1. LeaderElection Class (`src/powergslb/leader_election.py`)

**Purpose**: Implement distributed leader election using Redis Redlock pattern.

**Key Methods**:
- `acquire()` - Attempt to acquire leader lock (GET-first optimization)
- `renew()` - Renew lock with atomic Lua verification
- `release()` - Release lock on shutdown
- `wait_for_leadership()` - Block until leadership acquired
- `is_leader` - Property indicating leadership status

**Lua Verification Script**:
```lua
local current = redis.call('GET', KEYS[1])
if current == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
```
**Why Lua**: Ensures only the pod holding the lock can renew it. Prevents cross-pod interference.

**Background Renewal Thread**:
- Daemon thread started when leadership acquired
- Renews every 10 seconds (configurable)
- Gracefully stops on shutdown via Event.wait()
- Logs errors if renewal fails (lost leadership)

### 2. MonitorLeaderThread (`src/powergslb/main.py`)

**Purpose**: Wrapper that waits for leadership before starting health checks.

**Flow**:
```python
class MonitorLeaderThread(threading.Thread):
    def run(self) -> None:
        if self.leader_election:
            # Block until leader or timeout
            self.leader_election.wait_for_leadership()
            logging.info('Monitor acquired leadership, starting...')
        self.monitor_manager.run()
    
    def is_alive(self) -> bool:
        # CRITICAL: Check wrapper thread, not monitor_manager
        return threading.Thread.is_alive(self)
```

**Key Fix**: `is_alive()` checks wrapper thread status, not monitor_manager (which hasn't started yet).

### 3. StatusRegistry (with Redis Backend)

**Purpose**: Shared health status across all pods.

**Storage**:
```
Redis Set: "powergslb:health:down"
├─ content_id: 1 (down)
├─ content_id: 5 (down)
└─ content_id: 12 (down)

All up records implicitly: NOT in this set
```

**Operations**:
- Leader pod: `SADD powergslb:health:down {id}` (mark down)
- Leader pod: `SREM powergslb:health:down {id}` (mark up)
- All pods: `SISMEMBER powergslb:health:down {id}` (check status)
- All pods: `SMEMBERS powergslb:health:down` (get all down)

### 4. Health Check Endpoint (`/healthz`)

**Purpose**: K8s probe endpoint, no authentication required.

**Response**:
```json
{
    "status": "ok",
    "timestamp": 1694500000,
    "version": "2.4.3",
    "checks": {
        "database": {
            "status": "up",
            "connected": true
        }
    }
}
```

**Logging Suppressed**: HTTP handler skips logging for `/healthz` to avoid log spam.

## Configuration

### `build/powergslb.toml`

```toml
[redis]
host = "redis"              # or "dragonfly" for Redis-compatible
port = 6379
db = 0
connection_timeout = 2
socket_timeout = 2

[leader_election]
enabled = true              # Disable for single-pod deployments
lock_key = "powergslb:monitor:leader"
ttl = 30                    # Lease duration (seconds)
renewal_interval = 10       # How often to renew (must be < ttl)
acquisition_retry_interval = 5  # Retry acquiring if not leader
fallback_if_redis_unavailable = true  # Graceful degradation

[health]
enabled = true
check_database = true
check_redis = false
```

## Failover Sequence

### Normal State (1 Leader, 2 Standbys)

```
Time  Pod1          Pod2          Pod3          Redis
─────────────────────────────────────────────────────
0s    LEADER        standby       standby       lock: pod1
5s    renew lock    wait          wait          lock: pod1 (TTL 30)
10s   renew lock    wait          wait          lock: pod1 (TTL 30)
15s   runs checks   wait          wait          lock: pod1 (TTL 30)
20s   renew lock    wait          wait          lock: pod1 (TTL 30)
...
```

### Leader Crashes at T=0s

```
Time  Pod1          Pod2          Pod3          Redis
─────────────────────────────────────────────────────
0s    CRASH!        standby       standby       lock: pod1 (TTL 30)
5s    ❌            acquire fail  acquire fail  lock: pod1 (TTL ~25)
10s   ❌            acquire fail  acquire fail  lock: pod1 (TTL ~20)
15s   ❌            acquire fail  acquire fail  lock: pod1 (TTL ~15)
20s   ❌            acquire fail  acquire fail  lock: pod1 (TTL ~10)
25s   ❌            acquire fail  acquire fail  lock: pod1 (TTL ~5)
30s   ❌            ACQUIRE! ✓    standby       lock: pod2 (TTL 30)
31s   ❌            starts checks standby       lock: pod2 (TTL 30)
```

**Failover time**: ~30 seconds (until old lock TTL expires).

## Environment Variables

Every config option can be overridden:

```bash
# Redis connection
POWERGSLB_REDIS_HOST=dragonfly.prod.internal
POWERGSLB_REDIS_PORT=6379
POWERGSLB_REDIS_DB=0

# Leader election
POWERGSLB_LEADER_ELECTION_ENABLED=true
POWERGSLB_LEADER_ELECTION_TTL=30
POWERGSLB_LEADER_ELECTION_RENEWAL_INTERVAL=10
POWERGSLB_LEADER_ELECTION_ACQUISITION_RETRY_INTERVAL=5

# Logging
POWERGSLB_LOGGING_LEVEL=WARNING  # Suppress INFO logs
```

## Security & Reliability

### Atomicity
- **Lua scripting** ensures `GET + EXPIRE` is atomic
- No race conditions between renewal and acquisition

### Idempotency
- Renewal script succeeds only if pod owns lock
- Multiple renewal calls safe (idempotent)
- Cross-pod renewal impossible

### Graceful Degradation
- Redis unavailable → all records treated as UP
- Monitor continues running (fail-open, not fail-closed)
- Automatic recovery when Redis comes back

### Lock TTL Strategy
- **TTL = 30s** - Long enough to prevent flapping, short enough for reasonable failover
- **Renewal interval = 10s** - 3 renewals per TTL, safety margin
- If renewal fails: pod detects within 10s, loses leadership

## Performance Impact

| Operation | Latency | Frequency |
|-----------|---------|-----------|
| Acquire lock | ~10ms | Once at startup |
| Renew lock | ~10ms | Every 10s (leader only) |
| Check record status | ~5ms | Per DNS query (all pods) |
| Set record down | ~5ms | Per health check failure (leader only) |

**DNS Query Path** (all pods identical):
1. Parse DNS query
2. Look up records in MySQL
3. **Read status from Redis** (~5ms) ← NEW
4. Filter by health
5. Apply routing policy
6. Return answer

## Testing

### Unit Tests
```python
# Test leader election
def test_leader_acquires_lock(redis_client):
    le = LeaderElection(redis_config, le_config)
    assert le.acquire() == True
    assert le.is_leader == True

def test_renewal_requires_ownership(redis_client):
    le1 = LeaderElection(redis_config, le_config, pod_id="pod1")
    le2 = LeaderElection(redis_config, le_config, pod_id="pod2")
    
    le1.acquire()  # pod1 acquires
    assert le2.renew() == False  # pod2 cannot renew
```

### Integration Tests
```bash
# Docker: 3 pods + redis
docker-compose up -d

# Verify one leader
docker logs powergslb-1 | grep "Acquired leadership"
docker logs powergslb-2 | grep "Waiting for leadership"

# Kill leader, verify failover
docker stop powergslb-1
docker logs powergslb-2 | grep "Acquired leadership"  # Within 30s

# Query all pods, verify same answer
dig @localhost:8080 example.com
dig @localhost:8081 example.com
dig @localhost:8082 example.com
```

### Kubernetes
```bash
# Deploy
kubectl apply -f build/k8s/

# Verify leadership
kubectl logs -n powergslb powergslb-0 | grep leadership

# Test failover
kubectl delete pod -n powergslb powergslb-0
kubectl logs -n powergslb powergslb-1 | grep "Acquired"

# Verify DNS consistency
kubectl port-forward svc/powergslb 8080:8080 &
dig @127.0.0.1 -p 8080 example.com
```

## Migration Guide (from single-pod)

### Before (Single Pod)
```toml
[leader_election]
enabled = false
```

### After (Multi-Pod)
```toml
[leader_election]
enabled = true
ttl = 30
renewal_interval = 10
acquisition_retry_interval = 5

[redis]
host = "redis.prod.internal"
port = 6379
```

### Steps
1. Deploy Redis/Dragonfly
2. Update config with `enabled = true`
3. Deploy 3+ pods with same config
4. Verify logs show one leader
5. Query each pod, verify same answers
6. Kill leader pod, verify failover

## References

- [Redis Redlock Pattern](https://redis.io/docs/latest/develop/use/patterns/distributed-locks/)
- [Distributed System Consensus](https://en.wikipedia.org/wiki/Consensus_(computer_science))
- [Lua Scripting in Redis](https://redis.io/docs/latest/develop/interact/programmability/lua-api/)
- [Dragonfly (Redis-compatible)](https://www.dragonflydb.io/)
