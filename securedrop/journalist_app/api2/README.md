# Journalist API v2

This package implements and documents the synchronization strategy for the v2
Journalist API.

| File                                  | Contents                             |
| ------------------------------------- | ------------------------------------ |
| `README.md`                           | Specification                        |
| `__init__.py`                         | Server implementation                |
| `../../tests/test_journalist_api2.py` | Test suite for server implementation |

A client-side implementation should be able to interact with the endpoints
implemented in `__init__.py` according to this specification.

## Overview

The request/response schemas referred to in these sequence diagrams are defined
as mypy types in `__init__.py`.

### Initial synchronization

```mermaid
sequenceDiagram
participant Client
participant Server

alt Global
    Client -->> Server: GET /api/v2/index
else Sharded by UUID prefix
    Client -->> Server: GET /api/v2/index/<prefix>
end

Server ->> Client: ETag: abcdef<br>Index
Note over Client: We want metadata for all new sources and items.
Client -->> Server: POST /api/v2/metadata<br>MetadataRequest
Server ->> Client: MetadataResponse
```

### Incremental synchronization

```mermaid
sequenceDiagram
participant Client
participant Server

Note over Client: Global version abcdef
Note over Client: Shard <prefix> version uvwxyz

alt Global
    Client -->> Server: GET /api/v2/index<br>If-None-Match: abcdef
else Sharded by UUID prefix
    Client -->> Server: GET /api/v2/index/<prefix><br>If-None-Match: uvwxyz
end

alt Up to date
    Server ->> Client: HTTP 304
else Out of date
    Server ->> Client: ETag: abcdef<br>Index
    Note over Client: We want metadata for all new/changed sources and items.
    Client -->> Server: POST /api/v2/metadata<br>MetadataRequest
    Server ->> Client: MetadataResponse
end
```

### Batched events from client

```mermaid
sequenceDiagram
participant Client
participant Server

Note over Client: Global version abcdef
Note over Server: Global version abcdef

Client ->> Client: reply_sent {snowflake_id: X}
Client -->> Server: POST /api/v2/metadata<br>BatchRequest
alt Already processed:
Server ->> Server: look up status of event {snowflake_id: X}
Note over Server: Return status,<br>in addition to anything else requested.
Server ->> Client: BatchResponse
else
Server ->> Server: process "reply_sent" event
Note over Server: Return new item and updated source,<br>in addition to anything else requested.
Note over Server: Global version uvwxyz
Server ->> Client: BatchResponse
Note over Client: Global version uvwxyz
end
end
```
