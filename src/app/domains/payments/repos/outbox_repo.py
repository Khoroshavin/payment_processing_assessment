from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.payments.enums import OutboxStatus
from app.domains.payments.models.outbox import OutboxEvent


class OutboxRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, event: OutboxEvent) -> None:
        self._session.add(event)

    async def claim_pending(self, *, limit: int) -> list[OutboxEvent]:
        """Claim claimable pending rows, ordered by id, skipping locked ones.

        `available_at` is compared against the database clock (`func.now()`),
        not the application clock: both sides of the comparison then use the
        same time source, so a freshly inserted row is always claimable.
        """
        result = await self._session.execute(
            select(OutboxEvent)
            .where(
                OutboxEvent.status == OutboxStatus.PENDING,
                OutboxEvent.available_at <= func.now(),
            )
            .order_by(OutboxEvent.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars().all())
