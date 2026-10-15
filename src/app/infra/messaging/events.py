"""Domain events exchanged through the message broker."""

import uuid

from pydantic import BaseModel, ConfigDict

PAYMENT_NEW_EVENT: str = "payments.new"
EVENT_VERSION: int = 1


class PaymentNewEvent(BaseModel):
    """Event published to the payments.new queue when a payment is created."""

    model_config = ConfigDict(extra="forbid")

    payment_id: uuid.UUID
    event_version: int = EVENT_VERSION
