from dataclasses import dataclass, field
from typing import (
    Any,
    Dict,
    NewType,
    Set,
)

Version = NewType("Version", str)


# TODO: generic UUID[T] in Python 3.12
SourceUUID = NewType("SourceUUID", str)
ItemUUID = NewType("ItemUUID", str)
JournalistUUID = NewType("JournalistUUID", str)


@dataclass
class Index:
    # Source metadata, optionally filtered by `source_prefix`:
    sources: Dict[SourceUUID, Version] = field(default_factory=dict)
    items: Dict[ItemUUID, Version] = field(default_factory=dict)

    # Non-source metadata (always returned):
    journalists: Dict[JournalistUUID, Version] = field(default_factory=dict)


@dataclass
class MetadataRequest:
    # Source metadata:
    sources: Set[SourceUUID] = field(default_factory=set)
    items: Set[ItemUUID] = field(default_factory=set)

    # Non-source metadata:
    journalists: Set[JournalistUUID] = field(default_factory=set)


@dataclass
class MetadataResponse:
    # Source metadata:
    sources: Dict[SourceUUID, Any] = field(default_factory=dict)
    items: Dict[ItemUUID, Any] = field(default_factory=dict)

    # Non-source metadata:
    journalists: Dict[JournalistUUID, Any] = field(default_factory=dict)
