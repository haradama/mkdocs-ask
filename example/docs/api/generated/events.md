# EventsService (generated)

<!-- Generated from proto. Excluded from the ask index via "api/generated/*". -->

## rpc Append(AppendRequest) returns (AppendResponse)

| Field | Type | Label |
| --- | --- | --- |
| stream | string | required |
| events | repeated Event | required |
| idempotency_key | string | optional |

## rpc Subscribe(SubscribeRequest) returns (stream Event)

| Field | Type | Label |
| --- | --- | --- |
| stream | string | required |
| group | string | optional |
