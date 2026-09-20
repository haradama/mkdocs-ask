# Authentication

Kagura refuses every request except `/health` until authentication is configured. Four
mechanisms are available, and they can be enabled at the same time.

## Choosing a mechanism

| Mechanism | Best for | Credential lifetime |
| --- | --- | --- |
| API keys | Server-to-server, cron jobs, CI | Until revoked |
| OAuth 2.0 | Interactive tools acting for a human | Access 1h, refresh 30d |
| JWT bearer | Existing identity provider already issuing tokens | Whatever the issuer sets |
| mTLS | Fixed fleets inside one network boundary | Certificate validity |

## API keys

The simplest option for machine clients. Send the key in the `X-API-Key` header.

```bash
curl -H "X-API-Key: kgr_live_9f3a1c2e..." https://kagura.internal/v1/streams
```

Keys are created in the console or from the CLI:

```bash
kagura auth key create --name checkout-api --scopes write:orders,read:orders --ttl 90d
```

Only the hash is stored, so the plaintext is shown exactly once. A key that starts with
`kgr_test_` is restricted to streams whose name begins with `test-`.

### Rotating a key

Rotate every 90 days. Issue the replacement first and revoke the old key only once traffic has
moved, otherwise you take an outage between the two steps.

```bash
kagura auth key create --name checkout-api-2 --scopes write:orders
# deploy the new key, watch kagura_auth_requests_total{key_name="checkout-api-2"} rise
kagura auth key revoke checkout-api
```

A request presenting a revoked key fails with `ERR_KEY_REVOKED`. A request presenting a key that
expired on its own fails with `ERR_KEY_EXPIRED`, which is a different code so that dashboards
can tell an operational mistake from a normal lifecycle event.

## OAuth 2.0

Use the authorization code flow with PKCE for anything a human logs into.

```bash
curl -X POST https://kagura.internal/oauth/token \
  -d grant_type=authorization_code \
  -d code=$CODE \
  -d code_verifier=$VERIFIER \
  -d client_id=$CLIENT_ID
```

Access tokens live one hour and refresh tokens thirty days. Refresh tokens are single use: the
response to a refresh contains a new refresh token and invalidates the one you sent. Reusing a
consumed refresh token returns `ERR_TOKEN_REUSE` and revokes the whole chain, which is the
standard defence against a stolen token being replayed.

### Scopes

`read:<stream>`, `write:<stream>`, `admin:<stream>` and the cluster-wide `admin:*`. Grant the
narrowest scope that works. A token lacking a scope gets `ERR_FORBIDDEN`, not a 404, so that
existence of a stream is not leaked to an authenticated caller.

## JWT bearer tokens

Point Kagura at your issuer and it validates signatures against the published JWKS.

```yaml
auth:
  jwt:
    issuer: https://login.example.com/
    audience: kagura
    jwks_url: https://login.example.com/.well-known/jwks.json
    jwks_refresh: 15m
    claim_mapping:
      scopes: https://example.com/claims/kagura_scopes
```

Clock skew tolerance is 60 seconds. A token whose `nbf` is further in the future is rejected
with `ERR_TOKEN_NOT_YET_VALID`, which in practice almost always means a node has drifted and
needs NTP.

## Mutual TLS

```yaml
auth:
  mtls:
    enabled: true
    client_ca: /etc/kagura/client-ca.pem
    subject_to_identity: "CN=([a-z0-9-]+)\\..*"
```

The regex capture becomes the caller identity used for authorisation and in the audit log.

## Service accounts

A service account is an identity that owns keys and scopes without belonging to a person, so
that an engineer leaving the company does not break a pipeline.

```bash
kagura auth service-account create etl-nightly --scopes read:orders,write:orders-enriched
```

## Debugging a rejected request

```bash
kagura auth whoami --api-key kgr_live_...
# identity: service-account/etl-nightly
# scopes:   read:orders write:orders-enriched
# expires:  2026-11-02T00:00:00Z
```

`kagura auth whoami` answers the question "which credential does the server think this is"
without needing access to the audit log. When it disagrees with what you deployed, you are
almost certainly hitting a different node or a stale secret. Error codes are listed in
[Error codes](../reference/errors.md).
