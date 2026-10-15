import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.payments.models.payment import Payment


class PaymentRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, payment_id: uuid.UUID) -> Payment | None:
        return await self._session.get(Payment, payment_id)

    async def get_for_update(self, payment_id: uuid.UUID) -> Payment | None:
        """Get the payment holding a row lock until the transaction commits.

        Serializes concurrent processors of duplicate messages: the second
        one waits for the first commit and then observes the terminal status
        instead of calling the gateway twice.
        """
        stmt = select(Payment).where(Payment.id == payment_id).with_for_update()
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_by_idempotency_key(
        self, key: str, *, for_update: bool = False
    ) -> Payment | None:
        stmt = select(Payment).where(Payment.idempotency_key == key)
        if for_update:
            stmt = stmt.with_for_update()
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    def add(self, payment: Payment) -> None:
        self._session.add(payment)
