"""Domain ports: contracts for external integrations.

Adapters (infrastructure implementations) implement these protocols, so the
domain never depends on concrete implementations.
"""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class ProcessingResult:
    status: str


class PaymentGatewayPort(Protocol):
    async def process(self, payment_id: UUID) -> ProcessingResult: ...
