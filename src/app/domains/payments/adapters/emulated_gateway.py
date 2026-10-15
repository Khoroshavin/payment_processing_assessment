"""Payment gateway emulation (adapter of the PaymentGatewayPort).

Emulates an external payment gateway: 2-5s delay, 90% success, 10% failure.
A real PSP adapter implements the same PaymentGatewayPort protocol.
"""

import asyncio
import random
from uuid import UUID

from app.core.configs.settings import EmulationSettings
from app.domains.payments.enums import PaymentStatus
from app.domains.payments.ports import ProcessingResult


class PaymentGateway:
    def __init__(self, settings: EmulationSettings) -> None:
        self._min_delay = settings.min_delay_seconds
        self._max_delay = settings.max_delay_seconds
        self._success_rate = settings.success_rate

    async def process(self, payment_id: UUID) -> ProcessingResult:
        del payment_id  # эмулятор не смотрит на идентификатор
        delay = random.uniform(self._min_delay, self._max_delay)
        await asyncio.sleep(delay)
        if random.random() < self._success_rate:
            return ProcessingResult(status=PaymentStatus.SUCCEEDED)
        return ProcessingResult(status=PaymentStatus.FAILED)
