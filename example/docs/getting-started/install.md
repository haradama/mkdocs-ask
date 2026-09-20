# Install

Kagura ships as a single static binary. There is no external database to run: storage is a local
directory, optionally tiered to S3-compatible object storage.

## Requirements

| Item | Minimum | Recommended |
| --- | --- | --- |
| CPU | 2 cores | 8 cores or more |
| Memory | 2 GiB | 16 GiB or more |
| Disk | 10 GiB | NVMe SSD, 1.5x the retained data |
| OS | Linux (glibc 2.28+), macOS 13+ | Linux x86_64 or arm64 |
| Open file descriptors | 8192 | 65536 |

If `ulimit -n` is below 8192, writes start failing with `ERR_TOO_MANY_OPEN_FILES` once the
partition count grows. Raise it before you scale out, not after.

## Installation methods

<!-- Material content tabs indent their body four spaces, which a CommonMark parser reads
     as an indented code block. Scoped off here rather than repo-wide. -->
<!-- markdownlint-disable MD046 -->

=== "Docker"

    ```bash
    docker run -d --name kagura \
      -p 7700:7700 -p 7701:7701 \
      -v kagura-data:/var/lib/kagura \
      ghcr.io/example/kagura:2.4.0 serve
    ```

=== "Single binary"

    ```bash
    curl -fsSL https://dl.example.com/kagura/v2.4.0/kagura-linux-amd64.tar.gz \
      | tar xz -C /usr/local/bin kagura
    kagura version
    ```

=== "From source"

    ```bash
    git clone https://github.com/example/kagura
    cd kagura && make build          # requires Go 1.23 or newer
    ./bin/kagura version
    ```

<!-- markdownlint-enable MD046 -->

## Verifying the download

Every artifact is published with a SHA-256 checksum and a cosign signature.

```bash
curl -fsSLO https://dl.example.com/kagura/v2.4.0/SHA256SUMS
sha256sum -c SHA256SUMS --ignore-missing
cosign verify-blob --certificate SHA256SUMS.pem --signature SHA256SUMS.sig SHA256SUMS
```

## First run

```bash
kagura serve --data-dir /var/lib/kagura &
kagura health
# status: healthy  raft: leader  streams: 0  disk_free: 412 GiB
```

If `kagura health` reports `ERR_CONNECTION_REFUSED`, the server is most likely still replaying
its write-ahead log. Large logs can take minutes. See [Troubleshooting](../troubleshooting.md).

## Uninstalling

```bash
kagura serve --stop
rm -rf /var/lib/kagura /etc/kagura
```

Removing the data directory destroys every event it holds. Take a
[backup](../operations/backup.md) first.
