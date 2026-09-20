# Changelog

This page is excluded from the ask index through `exclude: [changelog.md]` in `mkdocs.yml`.
Release notes are mostly dates and version numbers, which crowd out useful matches without
answering questions.

## 2.4.0 (2026-08-14)

- Tiered storage to S3-compatible object storage
- `kagura diagnose` for one-command bug reports
- Percentile aggregations backed by t-digest
- Fixed a leak in the dedup table with very wide windows

## 2.3.0 (2026-05-02)

- JWT bearer authentication
- `--explain` for query plans
- Reduced compaction disk amplification by about 30 percent

## 2.2.0 (2026-02-18)

- Projections
- Session windows
- `kagura config dump --show-origin`

## 2.0.0 (2025-11-10)

- Built-in Raft, etcd no longer required
- KQL replaces the v1 query syntax
- YAML configuration
