from journalist_app.api2.types import (
    Event,
    EventResult,
    EventStatusCode,
    EventType,
    ItemTarget,
    SourceTarget,
)


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

        except (TypeError, ValueError):
            return EventResult(
                event_id=event.id,
                status=EventStatusCode.BadRequest,
            )

        try:
            handler = getattr(cls, f"handle_{event.type}")
        except AttributeError:
            return EventResult(
                event_id=event.id,
                status=EventStatusCode.NotImplemented,
            )

        return handler(event)
