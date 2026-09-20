# Environment variables

Every configuration key has an environment equivalent: uppercase the key and replace dots with
underscores, prefixed with `KAGURA_`. So `ingest.batch_size` becomes `KAGURA_INGEST_BATCH_SIZE`.

Environment variables override the configuration file and are overridden by command-line flags.

## Commonly used

| Variable | Equivalent key | Example |
| --- | --- | --- |
| `KAGURA_CONFIG` | `--config` | `/etc/kagura/kagura.yaml` |
| `KAGURA_PROFILE` | `--profile` | `prod` |
| `KAGURA_DATA_DIR` | `storage.data_dir` | `/var/lib/kagura` |
| `KAGURA_LISTEN` | `server.listen` | `0.0.0.0:7700` |
| `KAGURA_LOG_LEVEL` | `log.level` | `debug` |
| `KAGURA_STORAGE_BACKEND` | `storage.backend` | `s3` |
| `KAGURA_STORAGE_FSYNC` | `storage.fsync` | `interval` |
| `KAGURA_INGEST_BATCH_SIZE` | `ingest.batch_size` | `2000` |
| `KAGURA_INGEST_DEDUP_WINDOW` | `ingest.dedup_window` | `48h` |
| `KAGURA_QUERY_TIMEOUT` | `query.timeout` | `120s` |

## Client commands

| Variable | Description |
| --- | --- |
| `KAGURA_ENDPOINT` | Server address used by client commands |
| `KAGURA_API_KEY` | Credential used when `--api-key` is absent |
| `KAGURA_FORMAT` | Default output format |
| `NO_COLOR` | Any value disables ANSI colour |

## Credentials for object storage

Standard AWS SDK variables are honoured, so an instance role or a web identity token works
without extra configuration.

| Variable | Description |
| --- | --- |
| `AWS_ACCESS_KEY_ID` | Access key for the S3 backend |
| `AWS_SECRET_ACCESS_KEY` | Secret key |
| `AWS_SESSION_TOKEN` | Session token for temporary credentials |
| `AWS_REGION` | Region, when `storage.s3.region` is unset |
| `AWS_ENDPOINT_URL` | For MinIO and other S3-compatible services |

## Lists and nested values

A list is comma-separated:

```bash
export KAGURA_CLUSTER_PEERS=kagura-1.internal:7702,kagura-2.internal:7702
```

Values containing a comma must come from the configuration file instead; there is no escaping
syntax.

## Precedence, worked example

```bash
# kagura.yaml has ingest.batch_size: 500
export KAGURA_INGEST_BATCH_SIZE=1000
kagura serve --ingest-batch-size 2000     # effective value: 2000
```

Confirm with:

```bash
kagura config dump --effective --show-origin | grep batch_size
# ingest.batch_size: 2000  (origin: flag --ingest-batch-size)
```
