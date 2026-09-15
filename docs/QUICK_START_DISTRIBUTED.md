# Quick Start: Distributed Deployment with Redis

## Prerequisites

- Docker and docker-compose
- kubectl (for Kubernetes deployments)
- 3+ GB available RAM
- Redis or Dragonfly running

## Local Docker Compose (3 Pods)

### 1. Start Infrastructure

```bash
cd /Users/fedor.src/Develop/Github/powergslb
docker-compose up -d redis mariadb
```

Wait for both services to be healthy:

```bash
docker exec redis redis-cli ping
# PONG

docker exec mariadb mariadb -u root -proot -e "SELECT 1;"
# 1
```

### 2. Build PowerGSLB Image

```bash
docker build -t powergslb:test .
```

### 3. Start 3 PowerGSLB Pods

```bash
# Pod 1 (Leader)
docker run -d --name powergslb-1 \
  --network host \
  -e POWERGSLB_REDIS_HOST=127.0.0.1 \
  -e POWERGSLB_DATABASE_HOST=127.0.0.1 \
  -e POWERGSLB_LEADER_ELECTION_ENABLED=true \
  powergslb:test

# Pod 2 (Standby)
docker run -d --name powergslb-2 \
  --network host \
  -e POWERGSLB_REDIS_HOST=127.0.0.1 \
  -e POWERGSLB_DATABASE_HOST=127.0.0.1 \
  -e POWERGSLB_LEADER_ELECTION_ENABLED=true \
  powergslb:test

# Pod 3 (Standby)
docker run -d --name powergslb-3 \
  --network host \
  -e POWERGSLB_REDIS_HOST=127.0.0.1 \
  -e POWERGSLB_DATABASE_HOST=127.0.0.1 \
  -e POWERGSLB_LEADER_ELECTION_ENABLED=true \
  powergslb:test
```

### 4. Verify Leader Election

```bash
# Check logs for leadership status
docker logs powergslb-1 2>&1 | grep -i "leadership\|leader"
# INFO: Acquired leadership (pod=powergslb-1)
# INFO: Leader acquired leadership, starting monitor manager

docker logs powergslb-2 2>&1 | grep -i "leadership\|leader"
# INFO: Waiting for leadership

docker logs powergslb-3 2>&1 | grep -i "leadership\|leader"
# INFO: Waiting for leadership
```

### 5. Query DNS (All Pods Same Answer)

```bash
# All pods listen on different ports for testing
# Pod 1: 8080, Pod 2: 8081, Pod 3: 8082

# Query Pod 1
dig @127.0.0.1 -p 8080 example.com

# Query Pod 2 (same answer)
dig @127.0.0.1 -p 8081 example.com

# Query Pod 3 (same answer)
dig @127.0.0.1 -p 8082 example.com
```

### 6. Test Failover

Kill the leader pod:

```bash
docker stop powergslb-1
```

Check standby acquires leadership:

```bash
docker logs powergslb-2 2>&1 | grep -i "leadership"
# INFO: Acquired leadership (pod=powergslb-2)
# INFO: Leader acquired leadership, starting monitor manager
```

Verify DNS still works on Pod 2:

```bash
dig @127.0.0.1 -p 8081 example.com
```

### 7. Cleanup

```bash
docker stop powergslb-1 powergslb-2 powergslb-3 redis mariadb
docker rm powergslb-1 powergslb-2 powergslb-3 redis mariadb
```

---

## Kubernetes Deployment (3 Pods + Dragonfly)

### 1. Create Namespace

```bash
kubectl create namespace powergslb
```

### 2. Apply K8s Manifests

```bash
cd /Users/fedor.src/Develop/Github/powergslb
kubectl apply -f build/k8s/
```

Manifests deployed:
- `01-configmap-powergslb.yaml` - PowerGSLB config
- `02-redis-deployment.yaml` - Dragonfly (Redis-compatible)
- `03-mariadb-configmap.yaml` - Database init
- `04-mariadb-deployment.yaml` - MariaDB instance
- `05-powergslb-deployment.yaml` - 3 PowerGSLB pods
- `05-powerdns-deployment.yaml` - 2 PowerDNS pods
- `06-services.yaml` - All K8s services
- `07-dnsdist-deployment.yaml` - DNSDist load balancer

### 3. Wait for Pods Ready

```bash
kubectl get pods -n powergslb -w

# Expected output:
# NAME                         READY   STATUS    RESTARTS   AGE
# dragonfly-0                  1/1     Running   0          30s
# mariadb-0                    1/1     Running   0          35s
# powergslb-0                  1/1     Running   0          40s
# powergslb-1                  1/1     Running   0          40s
# powergslb-2                  1/1     Running   0          40s
# powerdns-0                   1/1     Running   0          45s
# powerdns-1                   1/1     Running   0          45s
# dnsdist-0                    1/1     Running   0          50s
# dnsdist-1                    1/1     Running   0          50s
```

### 4. Check Leadership

```bash
# Pod leader logs
kubectl logs -n powergslb powergslb-0 | grep -i leadership

# Expected:
# INFO: Acquired leadership (pod=powergslb-0)
# INFO: Leader acquired leadership, starting monitor manager
```

### 5. Port Forward to Test

```bash
# Forward PowerGSLB DNS
kubectl port-forward -n powergslb svc/powergslb 8080:8080 &

# Forward PowerDNS
kubectl port-forward -n powergslb svc/powerdns 8001:8080 &

# Query
dig @127.0.0.1 -p 8080 example.com
```

### 6. Test Failover

Delete leader pod:

```bash
kubectl delete pod -n powergslb powergslb-0

# Watch replacement:
kubectl get pods -n powergslb -w

# Standby becomes new leader:
kubectl logs -n powergslb powergslb-1 | grep -i leadership
```

### 7. Monitor Redis

```bash
# Port forward Dragonfly
kubectl port-forward -n powergslb svc/dragonfly 6379:6379 &

# Connect to Dragonfly
redis-cli -h 127.0.0.1

# Check leader lock
127.0.0.1:6379> GET powergslb:monitor:leader

# Check down records
127.0.0.1:6379> SMEMBERS powergslb:health:down
```

### 8. Cleanup

```bash
kubectl delete namespace powergslb
```

---

## Configuration Examples

### Development (Single Pod, No Redis)

`build/powergslb.toml`:
```toml
[leader_election]
enabled = false

[redis]
host = "localhost"  # Will fail to connect, gracefully degrade
```

**Result**: Pod runs health checks locally, no leader election overhead.

### Staging (3 Pods, Redis)

Environment variables:
```bash
POWERGSLB_LEADER_ELECTION_ENABLED=true
POWERGSLB_REDIS_HOST=redis.staging.internal
POWERGSLB_REDIS_PORT=6379
POWERGSLB_LEADER_ELECTION_TTL=30
```

**Result**: One pod runs checks, all pods serve consistent DNS.

### Production (5 Pods, Dragonfly + Replication)

`build/powergslb.toml`:
```toml
[leader_election]
enabled = true
ttl = 30
renewal_interval = 10
acquisition_retry_interval = 5

[redis]
host = "dragonfly-primary.prod.internal"
port = 6379
db = 0
connection_timeout = 2
```

**Result**: High availability, fast failover, no health check overhead on all pods.

---

## Common Operations

### View Health Status in Redis

```bash
redis-cli -h redis.powergslb.svc.cluster.local

# All down records
SMEMBERS powergslb:health:down

# Leader pod info
GET powergslb:monitor:leader

# Example output:
# {"pod_id": "powergslb-0", "timestamp": 1694500000}
```

### Manually Promote Standby

```bash
# Delete leader lock from Redis (forces new election)
redis-cli -h redis.powergslb.svc.cluster.local DEL powergslb:monitor:leader

# Standby pods will race to acquire within 5 seconds
# Check logs for new leader
```

### Disable Leader Election Temporarily

```bash
# Pod will treat all records as UP (graceful degradation)
kubectl set env deployment/powergslb \
  POWERGSLB_LEADER_ELECTION_ENABLED=false -n powergslb
```

### Monitor Renewal Rate

Watch logs for renewal pattern:

```bash
kubectl logs -f -n powergslb powergslb-0 | grep -i renewal
# Should see one renewal every 10 seconds if leader
```

---

## Troubleshooting Checklist

| Problem | Command | Expected Output |
|---------|---------|-----------------|
| No pod is leader | `kubectl logs -n powergslb powergslb-0 \| grep leadership` | `Acquired leadership` in one pod |
| Redis unreachable | `kubectl logs -n powergslb powergslb-0` | Should see error logs, graceful fallback |
| DNS inconsistency | `dig @pod1; dig @pod2` | Same answers |
| Leader not renewing | `redis-cli GET powergslb:monitor:leader` | TTL decreases every 10s |
| High CPU | `kubectl top pod -n powergslb` | Renewal thread <1% CPU |

---

## Next Steps

- [Distributed Deployment Architecture](DISTRIBUTED_DEPLOYMENT.md)
- [Kubernetes Manifests Reference](../build/k8s/)
- [Configuration Reference](../build/powergslb.toml)
