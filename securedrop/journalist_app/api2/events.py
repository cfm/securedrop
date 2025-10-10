from db import db
from journalist_app.api2.types import (
    Event,
    EventResult,
    EventStatusCode,
    EventType,
    ItemTarget,
    SourceTarget,
)
from journalist_app.api2.shared import save_reply
from models import Source
from sqlalchemy.orm.exc import NoResultFound


class EventHandler:
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
            handler = getattr(cls, f"handle_{event.type}")
        except AttributeError:
            return EventResult(
                event_id=event.id,
                status=(EventStatusCode.NotImplemented, f"no handler for event type: {event.type}"),
            )

        return handler(event)

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
