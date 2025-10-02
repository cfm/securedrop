from dataclasses import dataclass, field
from enum import IntEnum, StrEnum, auto
from typing import (
    Any,
    Dict,
    List,
    NewType,
    Set,
    Union,
)

Version = NewType("Version", str)


# TODO: generic UUID[T] in Python 3.12
SourceUUID = NewType("SourceUUID", str)
ItemUUID = NewType("ItemUUID", str)
JournalistUUID = NewType("JournalistUUID", str)


EventID = NewType("EventID", str)  # int, but opaque on the wire


class EventType(StrEnum):
    REPLY_SENT = auto()


class EventStatusCode(IntEnum):
    OK = 200
    BadRequest = 400
    NotFound = 404
    NotImplemented = 501


@dataclass
class Index:
    # Source metadata, optionally filtered by `source_prefix`:
    sources: Dict[SourceUUID, Version] = field(default_factory=dict)
    items: Dict[ItemUUID, Version] = field(default_factory=dict)

    # Non-source metadata (always returned):
    journalists: Dict[JournalistUUID, Version] = field(default_factory=dict)


@dataclass
class SourceTarget:
    source_uuid: SourceUUID
    version: Version


@dataclass
class ItemTarget:
    item_uuid: ItemUUID
    version: Version


@dataclass
class Event:
    id: EventID
    target: Union[SourceTarget, ItemTarget]
    type: EventType
    data: Any = field(default_factory=dict)


@dataclass
class EventResult:
    event_id: EventID
    status: EventStatusCode

    # Changed:
    sources: Dict[SourceUUID, Any] = field(default_factory=dict)
    items: Dict[ItemUUID, Any] = field(default_factory=dict)


@dataclass
class BatchRequest:
    # Source metadata:
    sources: Set[SourceUUID] = field(default_factory=set)
    items: Set[ItemUUID] = field(default_factory=set)

    # Non-source metadata:
    journalists: Set[JournalistUUID] = field(default_factory=set)

    # Events submitted by the client:
    events: List[Event] = field(default_factory=list)


@dataclass
class BatchResponse:
    # Source metadata:
    sources: Dict[SourceUUID, Any] = field(default_factory=dict)
    items: Dict[ItemUUID, Any] = field(default_factory=dict)

    # Non-source metadata:
    journalists: Dict[JournalistUUID, Any] = field(default_factory=dict)

    # Events processed by the server:
    events: Dict[EventID, EventStatusCode] = field(default_factory=dict)
