import uuid
from contextlib import contextmanager
from dataclasses import asdict

import pytest
from flask import url_for
from flask_sqlalchemy import get_debug_queries
from journalist_app.api2 import json_version
from journalist_app.api2.types import Event, EventStatusCode, EventType, SourceTarget
from sqlalchemy.orm.exc import MultipleResultsFound
from tests.utils.api_helper import get_api_headers


def filtered_queries():
    # filter out PRAGMA, instance_config loading, etc.
    return [
        q
        for q in get_debug_queries()
        if q.statement.startswith("SELECT")
        and not q.statement.startswith("SELECT instance_config.")
    ]


@contextmanager
def assert_query_count(expected_count, expect_login=True):
    """verify an API request makes the expected number of queries"""
    initial_count = len(filtered_queries())
    yield
    new_queries = filtered_queries()[initial_count:]
    # If the first API request is to look up journalists, it's part of the login flow, so skip it
    if (
        expect_login
        and len(new_queries) >= 1
        and new_queries[0].statement.startswith("SELECT journalists.")
    ):
        new_queries = new_queries[1:]

    assert (
        len(new_queries) == expected_count
    ), f"Expected {expected_count} queries, but {len(new_queries)} were executed"


def test_json_version():
    d = {"foo": "bar", "baz": "biz"}
    version1 = json_version(d)
    assert version1 == "2231968214a50f92d216048c7fc624c061372a4225e9e94aca88bdfaca162087"

    d2 = {"baz": "biz", "foo": "bar"}
    version2 = json_version(d2)
    assert version1 == version2


@pytest.mark.parametrize(
    ("endpoint", "kwargs"),
    [
        ("api2.index", {}),
        ("api2.index", {"source_prefix": "foo"}),
        # while this should be a POST request, the 403 will kick in first
        ("api2.data", {}),
    ],
)
def test_auth_required(journalist_app, endpoint, kwargs):
    """
    Verify all APIv2 endpoints require authentication
    """
    with journalist_app.test_client() as app:
        response = app.get(url_for(endpoint, **kwargs))

        assert response.status_code == 403


def test_index(journalist_app, test_files, journalist_api_token):
    """
    Verify GET /index response and HTTP 304 behavior
    """
    with journalist_app.test_client() as app:
        uuid = test_files["source"].uuid
        with assert_query_count(2):
            response = app.get(
                url_for("api2.index"),
                headers=get_api_headers(journalist_api_token),
            )

        # Verify the source is in the response
        assert response.status_code == 200
        assert uuid in response.json["sources"]
        # test_files generates 2 submissions and 1 reply, so 3 items total
        assert len(response.json["items"]) == 3

        with assert_query_count(2):
            response2 = app.get(
                url_for("api2.index"),
                headers={
                    **get_api_headers(journalist_api_token),
                    "If-None-Match": response.headers["ETag"],
                },
            )

        # With the etag, verify we get an empty 304
        assert response2.status_code == 304
        assert response2.calculate_content_length() == 0


def test_index_with_source_prefix(journalist_app, test_files, journalist_api_token):
    """
    Verify GET /index/<source_prefix> response and HTTP 304 behavior
    """
    with journalist_app.test_client() as app:
        uuid = test_files["source"].uuid
        with assert_query_count(2):
            response = app.get(
                url_for("api2.index", source_prefix=uuid[0]),
                headers=get_api_headers(journalist_api_token),
            )

        # Verify the source is in the response
        assert response.status_code == 200
        assert uuid in response.json["sources"]
        # test_files generates 2 submissions and 1 reply, so 3 items total
        assert len(response.json["items"]) == 3

        with assert_query_count(2):
            response2 = app.get(
                url_for("api2.index", source_prefix=uuid[0]),
                headers={
                    **get_api_headers(journalist_api_token),
                    "If-None-Match": response.headers["ETag"],
                },
            )

        # With the etag, verify we get an empty 304
        assert response2.status_code == 304
        assert response2.calculate_content_length() == 0

        # Make a response with an invalid source_prefix ("x")
        response3 = app.get(
            url_for("api2.index", source_prefix="x"),
            headers=get_api_headers(journalist_api_token),
        )
        # HTTP 200, but zero sources
        assert response3.status_code == 200
        assert response3.json["sources"] == {}
        assert response3.json["items"] == {}


def test_index_with_invalid_source_prefix(journalist_app, test_files, journalist_api_token):
    """
    Verify that a too-long source_prefix is rejected.
    """
    with journalist_app.test_client() as app:
        uuid = test_files["source"].uuid
        too_long = uuid[0] * 100
        with assert_query_count(0):
            response = app.get(
                url_for("api2.index", source_prefix=too_long),
                headers=get_api_headers(journalist_api_token),
            )

        assert response.status_code == 422
        assert "malformed request; source prefix must be shorter than" in response.get_data(
            as_text=True
        )


def test_metadata(journalist_app, test_files, test_journo, journalist_api_token):
    """
    Verify POST /metadata response
    """
    with journalist_app.test_client() as app:
        uuid = test_files["source"].uuid
        index = app.get(
            url_for("api2.index"),
            headers=get_api_headers(journalist_api_token),
        )

        assert index.status_code == 200
        source_versions = index.json["sources"][uuid]

        # Get the full source
        with assert_query_count(1):
            response = app.post(
                url_for("api2.data"),
                json={"sources": [uuid]},
                headers=get_api_headers(journalist_api_token),
            )
        assert response.status_code == 200
        assert uuid in response.json["sources"]
        source = response.json["sources"][uuid]
        # Verify the source has the same version
        assert json_version(source) == source_versions

        # Get an item
        item_uuid = test_files["submissions"][0].uuid
        with assert_query_count(2):
            response = app.post(
                url_for("api2.data"),
                json={"items": [item_uuid]},
                headers=get_api_headers(journalist_api_token),
            )
        assert response.status_code == 200
        assert item_uuid in response.json["items"]
        # Verify no source metadata is returned
        assert len(response.json["sources"]) == 0
        # Verify the versions are the same
        assert json_version(response.json["items"][item_uuid]) == index.json["items"][item_uuid]

        # Get a journalist
        journalist_uuid = test_journo["uuid"]
        with assert_query_count(1, expect_login=False):
            response = app.post(
                url_for("api2.data"),
                json={"journalists": [journalist_uuid]},
                headers=get_api_headers(journalist_api_token),
            )
        assert response.status_code == 200
        assert journalist_uuid in response.json["journalists"]
        # Verify no source or item metadata is returned
        assert len(response.json["sources"]) == 0
        assert len(response.json["items"]) == 0
        # Verify the versions are the same
        assert (
            json_version(response.json["journalists"][journalist_uuid])
            == index.json["journalists"][journalist_uuid]
        )


def test_item_collision(journalist_app, test_files_with_uuid_collision, journalist_api_token):
    """
    Test the edge case where a ``Submission`` and a ``Reply`` have the same UUID
    in separate tables.
    """
    with journalist_app.test_client() as app:
        # Get an item:
        item_uuid = test_files_with_uuid_collision["submissions"][0].uuid
        with pytest.raises(MultipleResultsFound):  # HTTP 500 in production
            app.post(
                url_for("api2.data"),
                json={"items": [item_uuid]},
                headers=get_api_headers(journalist_api_token),
            )


# Verify POST /sources input validation


@pytest.mark.parametrize(
    "invalid_data",
    [
        "invalid json{",
        "",
        None,
    ],
)
def test_api2_metadata_validation_invalid_json(journalist_app, journalist_api_token, invalid_data):
    """Test that Flask rejects invalid JSON."""
    with journalist_app.test_client() as app:
        response = app.post(
            url_for("api2.data"),
            data=invalid_data,
            headers=get_api_headers(journalist_api_token),
        )
        assert response.status_code == 400


@pytest.mark.parametrize(
    "invalid_request",
    [
        ["not", "a", "dict"],
        "string instead of dict",
        123,
        True,
    ],
)
def test_api2_metadata_validation_non_dict_request(
    journalist_app, journalist_api_token, invalid_request
):
    """Test that non-dict request body returns 400."""
    with journalist_app.test_client() as app:
        response = app.post(
            url_for("api2.data"),
            json=invalid_request,
            headers=get_api_headers(journalist_api_token),
        )
        assert response.status_code == 422
        assert "malformed request" in response.get_data(as_text=True)


@pytest.mark.parametrize(
    "valid_request",
    [
        # Empty but valid
        {"sources": [], "items": []},
        # Only sources
        {"sources": ["uuid1", "uuid2"], "items": ["uuid1", "uuid2"]},
        # Only items
        {"sources": [], "items": ["item1", "item2"]},
        # Both with data
        {
            "sources": ["uuid1", "uuid2"],
            "items": ["item1", "item2"],
        },
    ],
)
def test_api2_metadata_validation_valid_requests(
    journalist_app, journalist_api_token, valid_request
):
    """Test that valid requests pass validation."""
    with journalist_app.test_client() as app:
        response = app.post(
            url_for("api2.data"),
            json=valid_request,
            headers=get_api_headers(journalist_api_token),
        )
        assert response.status_code == 200


@pytest.mark.parametrize(
    "request_with_events,event_statuses",
    [
        (
            {
                "events": [
                    {
                        "id": "123456",
                        "type": "foobar",
                        "target": {"source_uuid": "abcdef", "version": "uvwxyz"},
                    }
                ]
            },
            {"123456": 400},
        ),
    ],
)
def test_api2_invalid_events(
    journalist_app,
    journalist_api_token,
    request_with_events,
    event_statuses,
):
    """Test that invalid events are rejected."""
    with journalist_app.test_client() as app:
        response = app.post(
            url_for("api2.data"),
            json=request_with_events,
            headers=get_api_headers(journalist_api_token),
        )
        for event in request_with_events["events"]:
            event_id = event["id"]
            assert response.json["events"][event_id] == event_statuses[event_id]


# FIXME: This is
# "app/server_tests/data/items/40e13a88-5409-4201-9495-d06c335e203f.gpg" via
# "gpg --enarmor".  Should probably pull from test_files fixture via
# download_reply().
REPLY = """-----BEGIN PGP MESSAGE-----
Comment: Use "gpg --dearmor" for unpacking

wcFMAwEQfVJow2WPAQ/9FAbkuKbTAu4WHk+iKNrEz21R0QeMDdKxffuQlD/36Gek
gDqa4O8Nvkw4MfvprRuPwiXG6Jvm9++hiy1sjIlN/obIb9zUz/CfzQIrzOAipaBn
OdwIc32s4hMtnnLdUZJa2vMKWFMyMAUrye3u0l7BgdBoDNUfDpKKLtDRtWGp0Uly
5JkWfgVgfSEwzHkGKZvHI3EBVCt53eIyrK8B/KZ7NdMDtgzQgWb04pdWONx1SwKU
72kIAgH7B44Btgn1MVj6Ri9IB470YZSWweIM0yTvQ/2//BPje6dCuK24vVmpz5Xd
7tcZyqZYtIifwz0p4sfdoXMQmxMiyrCGmY7hosRjbbRFFVvI7yQ/ujEsdLqDbGok
Ukv4gChYFMLOxqfpwF6v29A3MCHO3vwDBqQcwToQkP5BJE89jfF3+Z+n9+ahC6yX
Gi1gYX+X0/1S9Q2kB1q+Pyqst4CtSiu9n+WJ4CoJXA27fafkUIWjxcu0GIzA+Y40
2UzNiA4CRzj0rD9jOhDwCmrVqA/eR/nXdYK6wYnL7swGTHzD2HRf5p6fE6TnFSdt
8K79sTDiVnH4S3AAS9vnL9HIBqhn6sSa/uojCazb7ZVcWexWLt2Mcd5ZFOpZ32qB
Qf3Rhw4VSTJDZ4cIEDs531Pf+HZlIZRDEC64jNtTBVBJf9nn51cSxJmi4T8o4pLB
wUwDw+fEwKIgGyoBEACLYAx2OqvbkscWu6Fp/zMM43omBiiEMQRAs87ldE1sddwk
UE7N4H0xJE3l4x6poavY1oScEy+DiSvk6CYInEcDzGf6MSoCCBQ2cGjctfR84bE+
mJV7M7P41AgV8Xj+NsfCcirwTrd1zir06/D3qg5JacKpscJFYJXJg0fBCFkFqiIv
/6X1jcX9YAitLS3cLw+uV3ZuwKFUqnXLaclhvCvCTpdM+MuGvcNep+QFUeJnm+WS
jVQPko4RiCOpTgH+g2pw1oBjVZC2UX0Iqake3Bnge89REs+zIzQ2SA+RhjVA6jR1
rCf0ZIWckYg/WDxe2Dn4PAFhWgsjm4MM5dIE5YHvHPV8x0rIoAZQ8SwAXzTc1B1y
pLq5Iop2AaJSQ+SFaiuFsUfc391kfsnOShQn00jLqZ7+bhWUWUS/rCH8ePa7Hp8W
44MFOh8D3EsNN+hzuGXklHdL/dt41xmaO0o97yzusd8MJQ4fV0LBHkJEg5f21g3G
bDpt5Fed7BWpR20cWrwuGPL46/UfjMqHJoS56ZjuZyBuUwvoC7gQlPmyPlvSWdEb
8rF1gA4pPWURTNmClaHvPoubBig53mtXTz9esQfYu4FGmfUeFRnhdIWnrcvG3gXs
/jY8gBIQ6N+MjkRNHS6nwzcUukStsaSvBI2uL0iclGFOCAx7p6TNQPDqwrITi9LA
GwFbjruOwUUTjAErWxcTkGFWsUDPQV9gxamt7Agacinql4UAjSr21imTfqXkC/Kf
6bgOb8EfLunqRg+44Zgk0JluiXYh+ss7alf/dcqeF1AMq+vhJ40F6r940IOWJ/0W
cpZG68fwzC2YXTg4kU+OQdm4xBIeqTcgwiAnfZKKZtUCt8+JmRAzvbrLGXPe80Xc
Dl9xuZb+mEQlOnxD3mYf6htx5CNRdp//Rl3fgbGv3vZCmE+GQ7CvgHkf+7Evinno
+eozX+auvPwAyXG4npnwjwEJ0XaTKAJwRVH26Q==
=Eozu
-----END PGP MESSAGE-----
"""


def test_api2_reply_sent_event(
    journalist_app,
    journalist_api_token,
    test_files,
    test_journo,
):
    """Test processing of the "reply_sent" event."""
    with journalist_app.test_client() as app:
        source_uuid = test_files["source"].uuid
        index = app.get(
            url_for("api2.index"),
            headers=get_api_headers(journalist_api_token),
        )

        assert index.status_code == 200
        source_version = index.json["sources"][source_uuid]

        reply = {
            "journalist_uuid": test_journo["uuid"],
            "uuid": str(uuid.uuid4()),
            "reply": REPLY,
        }
        event = Event(
            id="123456",
            target=SourceTarget(source_uuid=source_uuid, version=source_version),
            type=EventType.REPLY_SENT,
            data=reply,
        )
        response = app.post(
            url_for("api2.data"),
            json={"events": [asdict(event)]},
            headers=get_api_headers(journalist_api_token),
        )
        assert response.json["events"][event.id] == EventStatusCode.OK
        assert reply["uuid"] in response.json["items"]
