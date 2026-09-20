# Deployment

## Single node

Fine for development and for workloads that tolerate a restart. Everything lives on one disk,
and losing that disk loses the data that is not backed up.

```bash
kagura serve --data-dir /var/lib/kagura --listen 0.0.0.0:7700
```

## High availability

Kagura replicates through Raft, so a cluster tolerates the loss of a minority of nodes.

| Nodes | Tolerates | Notes |
| --- | --- | --- |
| 1 | nothing | Development only |
| 3 | 1 failure | The usual choice |
| 5 | 2 failures | Across three availability zones |

Even node counts add cost without adding fault tolerance, since quorum is a majority either way.

```yaml
cluster:
  node_id: kagura-1
  peers:
    - kagura-1.internal:7702
    - kagura-2.internal:7702
    - kagura-3.internal:7702
  election_timeout: 1s
  heartbeat_interval: 100ms
```

Across availability zones, raise `election_timeout` to at least three times the p99 inter-zone
round trip. Too low and you get leader elections triggered by ordinary network jitter, which
looks like random write latency spikes.

## Kubernetes

```bash
helm repo add kagura https://charts.example.com/kagura
helm install kagura kagura/kagura \
  --set cluster.replicas=3 \
  --set storage.size=500Gi \
  --set storage.storageClass=fast-ssd
```

The chart deploys a StatefulSet with stable network identities, which Raft requires. A
Deployment will not work: pods must keep their identity and their volume across restarts.

Set `podDisruptionBudget.minAvailable` to 2 for a three-node cluster, otherwise a node drain can
take down quorum.

## Docker Compose

```yaml
services:
  kagura:
    image: ghcr.io/example/kagura:2.4.0
    command: serve
    ports: ["7700:7700"]
    volumes: ["kagura-data:/var/lib/kagura"]
    healthcheck:
      test: ["CMD", "kagura", "health"]
      interval: 10s
      timeout: 3s
      retries: 5
volumes:
  kagura-data:
```

## Rolling upgrades

Upgrade one node at a time, followers first and the leader last.

```bash
kagura cluster status                 # find the leader
kagura cluster step-down              # on the leader, once the followers are upgraded
```

Two adjacent minor versions interoperate, so a mixed cluster during a rollout is supported.
Skipping a minor version is not: go 2.2 to 2.3 to 2.4, never 2.2 straight to 2.4.

Downgrades are not supported once a node has written segments in the new format. Take a
[backup](backup.md) before upgrading.

## Replacing a node

```bash
kagura cluster remove kagura-2
# start the replacement with the same node_id and an empty data directory
kagura cluster add kagura-2 --address kagura-2.internal:7702
```

The new node catches up from the leader's snapshot plus the log tail. Expect roughly ten minutes
per 100 GiB on a 1 Gbit link. Remove before you add: adding first briefly creates an even-sized
cluster with a stale member.

## Network ports

| Port | Purpose | Expose to |
| --- | --- | --- |
| 7700 | Client API | Applications |
| 7701 | Admin and metrics | Operators and Prometheus |
| 7702 | Raft peer traffic | Other nodes only |

Port 7702 must never be reachable from outside the cluster. It carries replication traffic and
trusts its peers.
