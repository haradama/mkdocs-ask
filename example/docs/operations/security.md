# Security

## Encryption in transit

TLS is mandatory on every port except when `--dev` is used.

```yaml
server:
  tls:
    cert_file: /etc/kagura/server.pem
    key_file: /etc/kagura/server-key.pem
    min_version: "1.3"
    client_auth: require   # none | request | require
```

TLS 1.3 only, by default. Allowing 1.2 is possible with `min_version: "1.2"` but should be a
temporary measure with an expiry date attached.

Raft peer traffic on port 7702 uses a separate certificate pair, so a compromised client
certificate cannot join the cluster as a replica.

## Encryption at rest

```yaml
storage:
  encryption:
    enabled: true
    provider: kms          # kms | file
    key_id: arn:aws:kms:ap-northeast-1:...:key/...
    rotation_interval: 90d
```

Segments are encrypted with AES-256-GCM using a data key wrapped by the KMS key. Rotation
re-wraps data keys without rewriting segments, so it is cheap and should be automatic.

Losing access to the KMS key makes every segment unreadable, including backups. Grant decrypt
rights to the disaster recovery role as well as the production role.

## Restricting who can do what

Roles bundle scopes, and identities are bound to roles.

```bash
kagura rbac role create analyst --scopes 'read:*'
kagura rbac role create ingestor --scopes 'write:orders,write:events'
kagura rbac bind service-account/etl-nightly --role ingestor
```

Built-in roles: `viewer` for read on every stream, `operator` for stream administration without
data access, `admin` for everything. `operator` exists precisely so an on-call engineer can
change retention without being able to read customer payloads.

## Keeping tenants apart

```yaml
tenancy:
  enabled: true
  isolation: strict     # strict | shared
```

Under `strict`, a tenant gets its own namespace, quota and encryption key, and cross-tenant
queries are rejected at planning time rather than filtered at execution time, which removes a
whole category of leak.

## Network exposure

| Port | Should be reachable from |
| --- | --- |
| 7700 | Application subnets |
| 7701 | Operator and monitoring subnets |
| 7702 | Other cluster nodes only |

```yaml
# Kubernetes NetworkPolicy sketch
spec:
  ingress:
    - from: [{podSelector: {matchLabels: {app: checkout}}}]
      ports: [{port: 7700}]
    - from: [{podSelector: {matchLabels: {app: kagura}}}]
      ports: [{port: 7702}]
```

## Audit trail

Every administrative action is recorded with identity, action, target and result. Ship the audit
log off the node: an attacker with node access can stop the process, and a local-only trail is
the first thing lost.

```yaml
audit:
  sink: otlp
  endpoint: http://collector.internal:4317
```

## Handling secrets

- Never place credentials in `kagura.yaml`. Use `${VAR}` expansion or `*_file` keys.
- Files referenced by `*_file` keys should be mode 0400 and owned by the service user.
- `kagura config dump` redacts secrets, and so does every log level.

## Vulnerability policy

Security fixes are released for the current minor version and the one before it. Report issues
to `security@example.com`; the acknowledgement target is two business days.
