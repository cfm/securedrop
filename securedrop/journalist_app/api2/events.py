from db import db
from journalist_app import utils
from journalist_app.api2.shared import save_reply
from journalist_app.api2.types import (
    Event,
    EventResult,
    EventStatusCode,
    EventType,
    ItemTarget,
    SourceTarget,
)
from models import Reply, Source, Submission
from sqlalchemy.orm.exc import MultipleResultsFound, NoResultFound


class EventHandler:
    """
    This class is the per-event entry point for handling events.  To add a
    handler for a new event `thing_done`, you must:

    1. define the enum value `EventType.THING_DONE` in journalist_api2.types;

    2. define the handler as a static method `handle_thing_done(event: Event)`
       in this class

    3. explicitly register `{"thing_done": cls.handle_thing_done}` inside
       `EventHandler.process()`.

    This is belt-and-suspenders for ensuring that only the intended methods are
    exposed as callable event handlers.
    """

    @classmethod
    def process(cls, event_dict: dict) -> EventResult:
        try:
            event = Event(**event_dict)
            event.event_type = EventType(event.type)
            if "source_uuid" in event.target:
                event.target = SourceTarget(**event.target)
            elif "item_uuid" in event.target:
                event.target = ItemTarget(**event.target)
            else:
                raise TypeError("invalid event target")

        except (TypeError, ValueError) as e:
            return EventResult(
                event_id=event.id,
                status=(EventStatusCode.BadRequest, str(e)),
            )

        try:
            handler = {
                "item_deleted": cls.handle_item_deleted,
                "reply_sent": cls.handle_reply_sent,
            }[event.type]
        except AttributeError:
            return EventResult(
                event_id=event.id,
                status=(EventStatusCode.NotImplemented, f"no handler for event type: {event.type}"),
            )

        return handler(event)

    @staticmethod
    def handle_item_deleted(event: Event) -> EventResult:
        submission = Submission.query.filter(
            Submission.uuid == event.target.item_uuid
        ).one_or_none()
        reply = Reply.query.filter(Reply.uuid == event.target.item_uuid).one_or_none()

        if submission and reply:
            # Fail if we get unlucky and hit a UUID collision between the
            # `Submission` and `Reply` tables.  This is vanishingly unlikely,
            # but SQLite can't enforce uniqueness between them.
            raise MultipleResultsFound(
                f"found {event.target.item_uuid} in both submissions and replies"
            )

        elif submission:
            utils.delete_file_object(submission)
            return EventResult(
                event_id=event.id,
                status=(EventStatusCode.OK, None),
                items={event.target.item_uuid: None},
            )

        elif reply:
            utils.delete_file_object(reply)
            return EventResult(
                event_id=event.id,
                status=(EventStatusCode.OK, None),
                items={event.target.item_uuid: None},
            )

        return EventResult(
            event_id=event.id,
            status=(EventStatusCode.NotFound, f"could not find item: {event.target.item_uuid}"),
        )

    @staticmethod
    def handle_reply_sent(event: Event) -> EventResult:
        try:
            source = Source.query.filter(Source.uuid == event.target.source_uuid).one()
        except NoResultFound:
            return EventResult(
                event_id=event.id,
                status=EventStatusCode.NotFound,
            )

        reply = save_reply(source, event.data)
        db.session.refresh(source)

        return EventResult(
            event_id=event.id,
            status=(EventStatusCode.OK, None),
            sources={source.uuid: source},
            items={reply.uuid: reply},
        )
