# Schemas

Binding a schema to a stream turns malformed writes into an immediate, actionable error instead
of a downstream mystery.

## Registering a schema

```bash
kagura schema register orders --file ./schemas/order.avsc --compatibility BACKWARD
kagura schema list orders
# version 1  BACKWARD  registered 2026-06-01  fingerprint 9f3a1c2e
# version 2  BACKWARD  registered 2026-08-14  fingerprint 4bf92f11
```

Avro and JSON Schema are both supported. Every version is kept, and events record the version
they were written with, so an old consumer keeps working.

## Compatibility modes

| Mode | A new version may | Safe to upgrade first |
| --- | --- | --- |
| `NONE` | Anything | Nothing is checked |
| `BACKWARD` | Delete fields, add optional fields | Consumers |
| `FORWARD` | Add fields, delete optional fields | Producers |
| `FULL` | Only add or remove optional fields | Either |

`BACKWARD` is the default because the common case is that a new producer rolls out while old
consumers are still running.

## Evolving a schema safely

1. Add the field as optional, with a default.
2. Deploy producers that write it.
3. Deploy consumers that read it.
4. Only then consider making it required, which is a `FULL`-incompatible change and needs a new
   major version of the stream.

A write that violates the mode is rejected with `ERR_SCHEMA_MISMATCH`, and the message names the
offending field and the rule it broke.

```text
ERR_SCHEMA_MISMATCH: field "currency" removed but is required by reader schema version 1
```

## Checking before you deploy

```bash
kagura schema check orders --file ./schemas/order-v3.avsc
# BACKWARD compatible with versions 1, 2
```

Wire this into CI. It is the cheapest possible guard against a schema change that only fails in
production.

## Breaking changes

When a change genuinely cannot be compatible, create a new stream rather than forcing the old
one. Run both in parallel, migrate consumers, then retire the original.

```bash
kagura stream create orders-v2 --partitions 8 --schema ./schemas/order-v3.avsc
kagura stream mirror orders --to orders-v2 --transform ./transform.kql
```

## Schema-less streams

Omitting `--schema` accepts arbitrary JSON. This is convenient during exploration and painful
later, because nothing prevents two producers from disagreeing about what a field means. Attach
a schema before a stream gets a second producer.
