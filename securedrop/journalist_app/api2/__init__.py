import hashlib
from dataclasses import asdict
from typing import Mapping, Optional

from flask import Blueprint, abort, json, jsonify, request
from journalist_app.api2.events import EventHandler
from journalist_app.api2.types import (
    BatchRequest,
    BatchResponse,
    Index,
    Version,
)
from models import EagerQuery, Journalist, Reply, Source, Submission, eager_query
from sqlalchemy.inspection import inspect
from sqlalchemy.orm.exc import MultipleResultsFound
from werkzeug.wrappers.response import Response

blp = Blueprint("api2", __name__, url_prefix="/api/v2")

PREFIX_MAX_LEN = inspect(Source).columns["uuid"].type.length


def json_version(d: Mapping) -> Version:
    """
    Calculate the version (BLAKE2s digest) of the normalized JSON representation
    of the dictionary ``d``.

    We use BLAKE2s here because SHA-256 is too slow (we don't care about
    cryptographic security) and CRC-32 is too collision-prone (we're not merely
    checksumming for transmission integrity).
    """
    s = json.dumps(d, separators=[",", ":"], sort_keys=True)
    b = s.encode("utf-8")
    return Version(hashlib.blake2s(b).hexdigest())


@blp.get("/index")
@blp.get("/index/<string:source_prefix>")
def index(source_prefix: Optional[str] = None) -> Response:
    """
    By default, return the ETag-versioned ``Index`` of all metadata unless the
    client provides the ETag of the current index.

    Given a ``source_prefix``, return the sub-index of source metadata for all
    sources whose UUIDs begin with that prefix, plus all non-source metadata,
    unless the client provides the ETag of the current sub-index for that
    prefix.  The client MAY choose an arbitrary prefix with each request: e.g.,
    a series of requests with the prefixes ``{0...f}`` will effectively shard
    the source index into 16 shards.  (Non-source metadata is not filtered by
    the prefix and is always returned.)
    """
    index = Index()

    source_query: EagerQuery = eager_query("Source")
    if source_prefix is not None:
        if len(source_prefix) >= PREFIX_MAX_LEN:
            abort(
                422,
                f"malformed request; source prefix must be shorter than {PREFIX_MAX_LEN} "
                f"characters",
            )

        source_query = source_query.filter(Source.uuid.startswith(source_prefix))

    for source in source_query.all():
        index.sources[source.uuid] = json_version(source.to_api_v2())
        for item in source.collection:
            index.items[item.uuid] = json_version(item.to_api_v2())

    journalist_query: EagerQuery = eager_query("Journalist")
    for journalist in journalist_query.all():
        index.journalists[journalist.uuid] = json_version(journalist.to_api_v2())

    version = json_version(asdict(index))
    response = jsonify(asdict(index))

    # If the request's `If-None-Match` header matches the version,
    # return HTTP 304 with an empty response.
    response.set_etag(version)
    return response.make_conditional(request)


@blp.post("/data")  # read-write BatchRequest
@blp.post("/metadata")  # DEPRECATED: read-only MetadataRequest
def data() -> Response:
    """
    Return the ``BatchResponse`` requested in the ``BatchRequest``.  The
    client MAY choose an arbitrary list of objects with each request, e.g. from
    a shard retrieved from ``/index/<source_prefix>``.

    NB.  Returning sources is O(1) from the eagerly-loaded ``all_sources()``.
    Returning items is O(2), since we have to search both the ``Submission`` and
    the ``Reply`` tables for the set of all item UUIDs.
    """
    try:
        requested = BatchRequest(**request.json)  # type: ignore
    except (TypeError, ValueError) as exc:
        abort(422, f"malformed request; {exc}")

    response = BatchResponse()

    for event in requested.events:
        """
        # Case 1: already processed: If event.snowflake_id is already cached in
        # Redis, we've already processed it; just return its status.
        status = EventStatus(event.snowflake_id, 200)  # FIXME
        response.events.append(status)
        """

        # Case 2: needs to be processed.
        result = EventHandler.process(event)
        for uuid, source in result.sources.items():
            response.sources[uuid] = source.to_api_v2()
        for uuid, item in result.items.items():
            response.items[uuid] = item.to_api_v2()
        response.events[result.event_id] = result.status

    if requested.sources:
        source_query: EagerQuery = eager_query("Source")
        for source in source_query.filter(Source.uuid.in_(str(uuid) for uuid in requested.sources)):
            response.sources[source.uuid] = source.to_api_v2()

    if requested.items:
        submission_query: EagerQuery = eager_query("Submission")
        for item in submission_query.filter(
            Submission.uuid.in_(str(uuid) for uuid in requested.items)
        ):
            response.items[item.uuid] = item.to_api_v2()

        reply_query: EagerQuery = eager_query("Reply")
        for item in reply_query.filter(Reply.uuid.in_(str(uuid) for uuid in requested.items)):
            if item.uuid in response.items:
                # Fail if we get unlucky and hit a UUID collision between the
                # `Submission` and `Reply` tables.  This is vanishingly unlikely,
                # but SQLite can't enforce uniqueness between them.
                raise MultipleResultsFound(f"found {item.uuid} in both submissions and replies")
            response.items[item.uuid] = item.to_api_v2()

    if requested.journalists:
        journalist_query: EagerQuery = eager_query("Journalist")
        for journalist in journalist_query.filter(
            Journalist.uuid.in_(str(uuid) for uuid in requested.journalists)
        ):
            response.journalists[journalist.uuid] = journalist.to_api_v2()

    return jsonify(asdict(response))
