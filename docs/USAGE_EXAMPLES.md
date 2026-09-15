# Usage Examples: Redis & Leader Election

## Table of Contents
1. [Docker Compose Examples](#docker-compose-examples)
2. [Kubernetes Examples](#kubernetes-examples)
3. [Redis Monitoring](#redis-monitoring)
4. [Troubleshooting Recipes](#troubleshooting-recipes)

---

## Docker Compose Examples

### Example 1: Development (Single Pod, No Redis)

**Use case**: Local testing, single instance, no HA requirements.

```yaml
# docker-compose.yml
version: '3.8'

services:
  mariadb:
    image: mariadb:12.1.1
    environment:
      MYSQL_ROOT_PASSWORD: root
    ports:
      - "3306:3306"

  powergslb:
    image: powergslb:latest
    environment:
      POWERGSLB_LEADER_ELECTION_ENABLED: "false"
      POWERGSLB_DATABASE_HOST: mariadb
      POWERGSLB_LOGGING_LEVEL: DEBUG
    ports:
      - "8080:8080"
      - "8443:8443"
    depends_on:
      - mariadb
```

**Start**:
```bash
docker-compose up -d
docker logs powergslb | grep -i "Starting monitor"
```

**Result**: Pod runs health checks locally, no Redis needed.

---

### Example 2: High Availability (3 Pods + Redis)

**Use case**: Production, 3 pods behind load balancer.

```yaml
# docker-compose.distributed.yml
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s

  mariadb:
    image: mariadb:12.1.1
    environment:
      MYSQL_ROOT_PASSWORD: root
    ports:
      - "3306:3306"

  powergslb-1:
    image: powergslb:latest
    environment:
      POWERGSLB_REDIS_HOST: redis
      POWERGSLB_LEADER_ELECTION_ENABLED: "true"
      POWERGSLB_LEADER_ELECTION_TTL: "30"
      POWERGSLB_DATABASE_HOST: mariadb
      POWERGSLB_SERVER_PORT: "8080"
      POWERGSLB_LOGGING_LEVEL: WARNING
    ports:
      - "8080:8080"
    depends_on:
      - redis
      - mariadb

  powergslb-2:
    image: powergslb:latest
    environment:
      POWERGSLB_REDIS_HOST: redis
      POWERGSLB_LEADER_ELECTION_ENABLED: "true"
      POWERGSLB_DATABASE_HOST: mariadb
      POWERGSLB_SERVER_PORT: "8080"
      POWERGSLB_LOGGING_LEVEL: WARNING
    ports:
      - "8081:8080"
    depends_on:
      - redis
      - mariadb

  powergslb-3:
    image: powergslb:latest
    environment:
      POWERGSLB_REDIS_HOST: redis
      POWERGSLB_LEADER_ELECTION_ENABLED: "true"
      POWERGSLB_DATABASE_HOST: mariadb
      POWERGSLB_SERVER_PORT: "8080"
      POWERGSLB_LOGGING_LEVEL: WARNING
    ports:
      - "8082:8080"
    depends_on:
      - redis
      - mariadb

  load-balancer:
    image: haproxy:2.8-alpine
    volumes:
      - ./haproxy.cfg:/usr/local/etc/haproxy/haproxy.cfg:ro
    ports:
      - "53:8080/tcp"
      - "53:8080/udp"
    depends_on:
      - powergslb-1
      - powergslb-2
      - powergslb-3
```

**haproxy.cfg**:
```
global
    log stdout local0
    maxconn 4096

defaults
    log     global
    mode    http
    option  httpclose
    timeout connect 5000ms
    timeout client  50000ms
    timeout server  50000ms

frontend dns_front
    bind *:8080
    default_backend dns_back

backend dns_back
    balance roundrobin
    server pod1 powergslb-1:8080 check
    server pod2 powergslb-2:8080 check
    server pod3 powergslb-3:8080 check
```

**Start**:
```bash
docker-compose -f docker-compose.distributed.yml up -d

# Verify leadership
docker logs powergslb-1 | grep "Acquired leadership"
docker logs powergslb-2 | grep "Waiting for"
docker logs powergslb-3 | grep "Waiting for"
```

**Test**:
```bash
# Query all pods, verify same answers
dig @127.0.0.1 -p 8080 example.com
dig @127.0.0.1 -p 8081 example.com
dig @127.0.0.1 -p 8082 example.com

# All should return identical answers
```

**Failover Test**:
```bash
# Kill leader
docker stop powergslb-1

# Wait ~30s, verify new leader
docker logs powergslb-2 | grep "Acquired leadership"

# Queries still work
dig @127.0.0.1 -p 8081 example.com
```

---

## Kubernetes Examples

### Example 1: Minimal Distributed Setup

**Use case**: Kubernetes cluster with existing Redis.

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: powergslb

---
apiVersion: v1
kind: ConfigMap
metadata:
  name: powergslb-config
  namespace: powergslb
data:
  powergslb.toml: |
    [logging]
    level = "WARNING"
    
    [database]
    host = "mariadb.powergslb.svc.cluster.local"
    
    [redis]
    host = "redis.powergslb.svc.cluster.local"
    port = 6379
    
    [leader_election]
    enabled = true
    ttl = 30
    renewal_interval = 10

---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: powergslb
  namespace: powergslb
spec:
  replicas: 3
  selector:
    matchLabels:
      app: powergslb
  template:
    metadata:
      labels:
        app: powergslb
    spec:
      affinity:
        podAntiAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
          - weight: 100
            podAffinityTerm:
              labelSelector:
                matchExpressions:
                - key: app
                  operator: In
                  values:
                  - powergslb
              topologyKey: kubernetes.io/hostname
      
      containers:
      - name: powergslb
        image: powergslb:2.4.3
        volumeMounts:
        - name: config
          mountPath: /etc/powergslb
        ports:
        - containerPort: 8080
          name: dns
        - containerPort: 8443
          name: admin
        - containerPort: 8070
          name: health
        
        livenessProbe:
          httpGet:
            path: /healthz
            port: health
          initialDelaySeconds: 30
          periodSeconds: 10
        
        readinessProbe:
          httpGet:
            path: /healthz
            port: health
          initialDelaySeconds: 20
          periodSeconds: 5
      
      volumes:
      - name: config
        configMap:
          name: powergslb-config

---
apiVersion: v1
kind: Service
metadata:
  name: powergslb
  namespace: powergslb
spec:
  selector:
    app: powergslb
  ports:
  - port: 53
    targetPort: 8080
    protocol: UDP
    name: dns
  - port: 443
    targetPort: 8443
    protocol: TCP
    name: admin
  type: LoadBalancer
```

**Deploy**:
```bash
kubectl apply -f powergslb.yaml

# Check pods
kubectl get pods -n powergslb -w

# Check leadership
kubectl logs -n powergslb -l app=powergslb --tail=50 | grep -i leadership
```

---

### Example 2: With Dragonfly (High Performance Redis Alternative)

```yaml
apiVersion: dragonflydb.io/v1alpha1
kind: Dragonfly
metadata:
  name: dragonfly
  namespace: powergslb
spec:
  image: dragonflydb/dragonfly:latest
  replicas: 2
  resources:
    requests:
      cpu: 100m
      memory: 128Mi
    limits:
      cpu: 500m
      memory: 512Mi

---
apiVersion: v1
kind: Service
metadata:
  name: dragonfly
  namespace: powergslb
spec:
  selector:
    app.kubernetes.io/name: dragonfly
  ports:
  - port: 6379
    targetPort: 6379
  type: ClusterIP
```

**Update PowerGSLB Config**:
```toml
[redis]
host = "dragonfly.powergslb.svc.cluster.local"  # Changed from redis
port = 6379
```

---

## Redis Monitoring

### Check Leadership Status

```bash
# Port forward to Redis
kubectl port-forward -n powergslb svc/redis 6379:6379 &

# Connect
redis-cli -h 127.0.0.1 -p 6379

# View leader lock
127.0.0.1:6379> GET powergslb:monitor:leader
"{\"pod_id\": \"powergslb-0\", \"timestamp\": 1694500000}"

# View TTL (should be around 30)
127.0.0.1:6379> TTL powergslb:monitor:leader
(integer) 25

# View down records
127.0.0.1:6379> SMEMBERS powergslb:health:down
1) "12345"
2) "67890"
(2.5 seconds)
```

### Monitor Lock Renewal

```bash
# Watch lock TTL every second
watch -n 1 'redis-cli -h 127.0.0.1 TTL powergslb:monitor:leader'

# Output should show TTL oscillating (30 → ~5 → 30 → ~5...)
# If TTL stays at 30, renewal is not working
```

### Check Health Status Changes

```bash
# Monitor down records in real-time
redis-cli -h 127.0.0.1 MONITOR | grep powergslb:health

# Kill an endpoint in PowerDNS admin, watch SADD command
# Set endpoint up again, watch SREM command
```

---

## Troubleshooting Recipes

### Recipe 1: No Pod is Leader

**Symptoms**: All pods stuck with "Waiting for leadership"

**Diagnosis**:
```bash
# Check Redis connectivity
kubectl logs -n powergslb powergslb-0 | grep -i redis

# Check lock exists
redis-cli -h redis.powergslb.svc.cluster.local GET powergslb:monitor:leader
# If nil/empty, no leader acquired
```

**Fix**:
```bash
# Option 1: Restart all pods
kubectl rollout restart deployment/powergslb -n powergslb

# Option 2: Clear stuck lock
redis-cli -h redis.powergslb.svc.cluster.local DEL powergslb:monitor:leader

# Verify within 5 seconds
kubectl logs -n powergslb -f -l app=powergslb | grep "Acquired leadership"
```

---

### Recipe 2: DNS Inconsistency (Pods Disagree)

**Symptoms**: Different answers from different pods

**Diagnosis**:
```bash
# Query Pod A
kubectl exec -n powergslb powergslb-0 -- dig @127.0.0.1 example.com

# Query Pod B
kubectl exec -n powergslb powergslb-1 -- dig @127.0.0.1 example.com

# If different, check Redis connection
redis-cli -h redis.powergslb.svc.cluster.local SMEMBERS powergslb:health:down
```

**Common Causes**:
1. **Redis unreachable**: Pod A sees different down records than Pod B
   - Fix: Check network connectivity, DNS resolution
   
2. **Health check not syncing**: Leader not updating Redis
   - Fix: Check leader pod logs for errors, restart leader

3. **Stale cache**: Old data not invalidated
   - Fix: Flush redis cache: `FLUSHDB`

---

### Recipe 3: Leader Renewal Failing

**Symptoms**: Leader loses leadership unexpectedly, frequent failovers

**Diagnosis**:
```bash
# Watch renewal in logs
kubectl logs -n powergslb -f powergslb-0 | grep -i renewal

# Check Redis latency
redis-cli -h redis.powergslb.svc.cluster.local --latency-history
# If > 100ms, renewal might fail

# Check lock TTL stays fresh
watch -n 1 'redis-cli -h redis.powergslb.svc.cluster.local TTL powergslb:monitor:leader'
# Should oscillate around 30, not decay to 0
```

**Common Causes**:
1. **Redis slow**: Network lag, overload
   - Fix: Upgrade Redis resources, check CPU/memory
   
2. **Renewal thread crashed**: Check logs for errors
   - Fix: Increase renewal timeout in config
   
3. **Pod CPU throttled**: Process too slow
   - Fix: Increase CPU limits

**Adjust Timings** (in `powergslb.toml`):
```toml
[leader_election]
ttl = 60                    # Increase TTL
renewal_interval = 20       # Increase interval
acquisition_retry_interval = 10  # Increase retry
```

---

### Recipe 4: Failover Takes Too Long

**Symptoms**: Leader crashes, but standby takes > 1 minute to acquire

**Diagnosis**:
```bash
# Check TTL at time of crash
redis-cli GET powergslb:monitor:leader
redis-cli TTL powergslb:monitor:leader

# If TTL is large (e.g., 120), failover will take that long
```

**Fix**: Reduce TTL in config:
```toml
[leader_election]
ttl = 15  # Failover in ~15 seconds (was 30)
renewal_interval = 5  # Renew every 5s (was 10)
```

**Trade-off**: Lower TTL = faster failover but higher Redis load.

---

### Recipe 5: All Pods Are Leaders (Multiple Leaders Bug)

**Symptoms**: Multiple pods log "Acquired leadership" simultaneously

**This should never happen** - indicates code bug. If seen:

```bash
# Check Redis lock
redis-cli GET powergslb:monitor:leader
redis-cli TTL powergslb:monitor:leader

# Verify Lua script atomicity
redis-cli EVAL "return redis.call('GET', KEYS[1])" 1 powergslb:monitor:leader
```

**Action**: Contact developers, likely need to rebuild with fixed LeaderElection code.

---

### Recipe 6: High Memory Usage

**Symptoms**: Pod memory grows over time

**Diagnosis**:
```bash
# Check health status set size
redis-cli -h redis.powergslb.svc.cluster.local SCARD powergslb:health:down

# If thousands, check for leaks
redis-cli --bigkeys
```

**Common Causes**:
1. **Uncleared down records**: Health checks not marking as up
   - Fix: Verify health check configuration
   
2. **Redis memory fragmentation**
   - Fix: Restart Redis, use INFO memory

**Monitor**:
```bash
# Watch memory over time
watch -n 5 'redis-cli -h redis.powergslb.svc.cluster.local INFO memory | grep used_memory_human'
```

---

### Recipe 7: Redis Failover (Sentinel/Cluster)

**Use case**: High-availability Redis setup

**With Redis Sentinel**:
```toml
[redis]
# Sentinel will redirect automatically
host = "sentinel-master.redis.svc.cluster.local"
port = 6379
```

**With Redis Cluster**:
```toml
[redis]
# redis-py cluster mode
host = "cluster-node-1.redis.svc.cluster.local"
port = 6379
# Other nodes auto-discovered
```

**Testing Failover**:
```bash
# Kill Redis master
kubectl delete pod redis-0

# Verify sentinel/cluster failover
redis-cli -h sentinel-master.redis.svc.cluster.local PING
# Should still work

# PowerGSLB should auto-reconnect
kubectl logs -n powergslb powergslb-0 | grep -i "redis.*reconnect"
```

---

## Performance Tuning

### Reduce Leader Election Overhead

```toml
[leader_election]
ttl = 60                # Increase TTL (fewer acquisitions)
renewal_interval = 20   # Renew less frequently
```

**Result**: Less Redis traffic, higher failover latency.

---

### Optimize for Fast Failover

```toml
[leader_election]
ttl = 10                # Decrease TTL (faster failover)
renewal_interval = 3    # Renew frequently
```

**Result**: More Redis traffic, lower failover latency (~10s).

---

### Monitor Metrics

```bash
# Count Redis operations per minute
redis-cli MONITOR | wc -l

# Typical baseline (3 pods):
# - 18 renewals/minute (1 per 3.3 seconds × 3 pods)
# - < 10 status updates/minute (per health check)
# Total: ~30 ops/min, < 100 bytes/min
```

---

## See Also

- [Distributed Deployment Architecture](DISTRIBUTED_DEPLOYMENT.md)
- [Implementation Summary](IMPLEMENTATION_SUMMARY.md)
- [Quick Start Guide](QUICK_START_DISTRIBUTED.md)
