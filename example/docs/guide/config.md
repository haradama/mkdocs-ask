# Configuration

## Where settings come from

Four sources, in increasing order of precedence:

1. Built-in defaults
2. The configuration file, `/etc/kagura/kagura.yaml` by default
3. Environment variables prefixed `KAGURA_`
4. Command-line flags

So `--ingest-batch-size 2000` beats `KAGURA_INGEST_BATCH_SIZE=1000`, which beats
`ingest.batch_size: 500` in the file. Print the effective result with:

```bash
kagura config dump --effective --show-origin
```

Each line of that output names the source that won, which is the fastest way to find out why a
setting you changed appears to have no effect.

## File layout

```yaml
server:
  listen: 0.0.0.0:7700
  admin_listen: 127.0.0.1:7701
  timeout: 30s

storage:
  data_dir: /var/lib/kagura
  backend: local            # local | s3
  fsync: always             # always | interval | never

ingest:
  batch_size: 500
  max_payload_bytes: 1048576
  dedup_window: 24h

auth:
  provider: api_key         # api_key | oauth | jwt | mtls
  client_id: ${CLIENT_ID}   # expanded from the environment at load time
```

`${VAR}` is expanded when the file is read. A missing variable stops startup with
`ERR_CONFIG_UNRESOLVED` rather than silently substituting an empty string.

## Reloading without a restart

Sending `SIGHUP`, or calling `kagura config reload`, re-reads the file and applies the settings
that are marked reloadable in [Configuration keys](../reference/configuration.md). Listen
addresses and the data directory are not reloadable, and changing them in the file has no effect
until the process restarts.

```bash
kagura config reload
# reloaded 6 keys, 2 keys require a restart: server.listen, storage.data_dir
```

## Validating before you deploy

```bash
kagura config validate --file ./kagura.yaml --strict
```

`--strict` turns unknown keys into errors. This matters more than it sounds: a typo such as
`storage.fsyncs` is otherwise ignored, and you discover it only after an unclean shutdown has
already cost you data.

## Profiles

One file can hold several profiles, selected with `--profile` or `KAGURA_PROFILE`.

```yaml
profiles:
  dev:
    storage: { fsync: never }
    auth:    { provider: none }
  prod:
    storage: { fsync: always }
    auth:    { provider: oauth }
```

Profile values are merged over the top-level settings, they do not replace them wholesale.

## Secrets

Never put credentials in the file. Use environment expansion, or read from a file:

```yaml
auth:
  client_secret_file: /run/secrets/kagura-oauth
```

Values read this way are redacted in `kagura config dump` and never appear in logs, even at
`debug` level.
