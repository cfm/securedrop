"""
This module contains helper functions factored out of the v1 Journalist API
(journalist_app.api) and shared with the v2 Journalist API (journalist_app.api2)
"""

from os import path
from uuid import UUID

from db import db
from journalist_app.sessions import session
from models import (
    Reply,
    SeenReply,
    Source,
)
from sqlalchemy.exc import IntegrityError
from store import Storage


def save_reply(source: Source, data: dict) -> Reply:
    source.interaction_count += 1
    filename = Storage.get_default().save_pre_encrypted_reply(
        source.filesystem_id,
        source.interaction_count,
        source.journalist_filename,
        data["reply"],
    )

    # issue #3918
    filename = path.basename(filename)

    reply = Reply(session.get_user(), source, filename, Storage.get_default())

    reply_uuid = data.get("uuid")
    if reply_uuid is not None:
        # check that is is parseable
        UUID(reply_uuid)
        reply.uuid = reply_uuid

    try:
        db.session.add(reply)
        seen_reply = SeenReply(reply=reply, journalist=session.get_user())
        db.session.add(seen_reply)
        db.session.add(source)
        db.session.commit()
    except IntegrityError as e:
        db.session.rollback()
        raise e

    return reply
