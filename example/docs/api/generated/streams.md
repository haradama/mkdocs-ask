# StreamsService (generated)

<!-- Generated from proto. Excluded from the ask index via "api/generated/*". -->

## rpc CreateStream(CreateStreamRequest) returns (Stream)

| Field | Type | Label |
| --- | --- | --- |
| name | string | required |
| partitions | int32 | required |
| retention | google.protobuf.Duration | optional |

## rpc ListStreams(ListStreamsRequest) returns (ListStreamsResponse)

| Field | Type | Label |
| --- | --- | --- |
| page_size | int32 | optional |
| page_token | string | optional |
